"""HERE Routing v8 (truck) + HERE Geocoding & Search v7."""

from __future__ import annotations

from datetime import UTC, datetime

import flexpolyline
import httpx

from ..domain.geo import haversine_mi
from .base import LatLng, Place, ProviderError, RawRoute, get_json

ROUTES_URL = "https://router.hereapi.com/v8/routes"
GEOCODE_URL = "https://geocode.search.hereapi.com/v1/geocode"
REVGEOCODE_URL = "https://revgeocode.search.hereapi.com/v1/revgeocode"

LB_TO_KG = 0.45359237
NORTH_AMERICA = "countryCode:USA,CAN,MEX"


def _fmt(p: LatLng) -> str:
    return f"{p[0]:.6f},{p[1]:.6f}"


class HereClient:
    def __init__(self, api_key: str, timeout_s: float = 15.0):
        if not api_key:
            raise ProviderError("here_auth", "HERE_API_KEY is not configured.", 503)
        self.api_key = api_key
        self.http = httpx.Client(timeout=timeout_s, headers={"User-Agent": "weather-aware-truck-routing/1.0"})

    # --- Routing -------------------------------------------------------------

    def routes(
        self,
        origin: LatLng,
        destination: LatLng,
        departure: datetime,
        gross_weight_lb: float,
        alternatives: int = 2,
        via: LatLng | None = None,
    ) -> list[RawRoute]:
        params: dict[str, str] = {
            "apiKey": self.api_key,
            "transportMode": "truck",
            "origin": _fmt(origin),
            "destination": _fmt(destination),
            "return": "polyline,summary",
            "spans": "duration",
            "alternatives": str(alternatives if via is None else 0),
            # Predicted traffic for the planned departure.
            "departureTime": departure.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Routing v8 'vehicle[...]' parameters (the older 'truck[...]' ones are deprecated).
            "vehicle[grossWeight]": str(int(round(gross_weight_lb * LB_TO_KG))),
        }
        if via is not None:
            params["via"] = f"{_fmt(via)}!passThrough=true"
        data = get_json(self.http, ROUTES_URL, params, service="HERE routing")
        routes = data.get("routes") or []
        if not routes:
            notices = "; ".join(n.get("title", "") for n in data.get("notices", []))
            raise ProviderError(
                "no_route",
                f"No truck-legal route found between these places. {notices}".strip(),
                422,
            )
        return [self._parse_route(r) for r in routes]

    @staticmethod
    def _parse_route(route: dict) -> RawRoute:
        coords: list[LatLng] = []
        times: list[float] = []
        notices: list[str] = []
        clock = 0.0
        for section in route.get("sections", []):
            pts = [(p[0], p[1]) for p in flexpolyline.decode(section["polyline"])]
            sec_times = _vertex_times(pts, section.get("spans") or [], section.get("summary", {}).get("duration", 0))
            if section.get("type") == "ferry" or section.get("transport", {}).get("mode") == "ferry":
                notices.append("Route includes a ferry crossing.")
            for n in section.get("notices", []):
                if n.get("title"):
                    notices.append(n["title"])
            start = 1 if coords and pts and coords[-1] == pts[0] else 0
            for p, t in list(zip(pts, sec_times))[start:]:
                coords.append(p)
                times.append(clock + t)
            clock = times[-1] if times else clock
        if len(coords) < 2:
            raise ProviderError("no_route", "Routing provider returned an empty route geometry.", 502)
        return RawRoute(coords, times, sorted(set(notices)))

    # --- Geocoding -----------------------------------------------------------

    def search(self, query: str, limit: int = 5) -> list[Place]:
        data = get_json(
            self.http,
            GEOCODE_URL,
            {"apiKey": self.api_key, "q": query, "limit": str(limit), "in": NORTH_AMERICA},
            service="HERE geocoding",
        )
        out = []
        for item in data.get("items", []):
            pos = item.get("position")
            if pos:
                label = item.get("address", {}).get("label") or item.get("title", query)
                out.append(Place(pos["lat"], pos["lng"], label))
        return out

    def reverse(self, lat: float, lng: float) -> str | None:
        try:
            data = get_json(
                self.http,
                REVGEOCODE_URL,
                {"apiKey": self.api_key, "at": f"{lat:.5f},{lng:.5f}", "limit": "1"},
                service="HERE geocoding",
                retries=0,
            )
        except ProviderError:
            return None  # place names are a nicety, never a failure
        items = data.get("items") or []
        if not items:
            return None
        addr = items[0].get("address", {})
        city = addr.get("city") or addr.get("county")
        state = addr.get("stateCode") or addr.get("state")
        street = addr.get("street")
        place = ", ".join(x for x in (city, state) if x)
        if street and place:
            return f"{street} near {place}"
        return place or items[0].get("title")


def _vertex_times(pts: list[LatLng], spans: list[dict], total_s: float) -> list[float]:
    """Cumulative seconds at each vertex.

    HERE spans give the duration of each span (vertices offset_k..offset_{k+1}).
    Within a span, time is spread by distance. Without spans, the section duration
    is spread over the whole section by distance.
    """
    n = len(pts)
    seg = [0.0] + [haversine_mi(a, b) for a, b in zip(pts, pts[1:])]
    times = [0.0] * n
    bounds = [s["offset"] for s in spans if "offset" in s and "duration" in s]
    durations = [float(s["duration"]) for s in spans if "offset" in s and "duration" in s]
    if not bounds:
        bounds, durations = [0], [float(total_s)]
    bounds.append(n - 1)
    clock = 0.0
    for k, dur in enumerate(durations):
        lo, hi = bounds[k], max(bounds[k + 1], bounds[k])
        span_len = sum(seg[lo + 1 : hi + 1])
        for v in range(lo + 1, hi + 1):
            share = (seg[v] / span_len) if span_len > 0 else 1.0 / max(hi - lo, 1)
            clock += dur * share
            times[v] = clock
    for v in range(1, n):  # fill any gap past the last span
        times[v] = max(times[v], times[v - 1])
    return times
