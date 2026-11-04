"""Deterministic demo providers.

Used for tests, offline development and as a clearly-labelled fallback when no
API keys are configured. They mimic the live APIs' semantics (hourly series
starting at 00:00 UTC, accumulated precipitation per preceding hour) so every
code path the live providers hit is exercised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.geo import bearing_deg, haversine_mi, offset_point
from ..domain.weather import HourlySeries
from .base import LatLng, Place, ProviderError, RawRoute

CITIES: list[tuple[str, float, float]] = [
    ("Atlanta, GA", 33.749, -84.388),
    ("Albuquerque, NM", 35.084, -106.650),
    ("Amarillo, TX", 35.222, -101.831),
    ("Billings, MT", 45.783, -108.500),
    ("Birmingham, AL", 33.519, -86.810),
    ("Boise, ID", 43.615, -116.202),
    ("Boston, MA", 42.360, -71.058),
    ("Charlotte, NC", 35.227, -80.843),
    ("Cheyenne, WY", 41.140, -104.820),
    ("Chicago, IL", 41.878, -87.630),
    ("Cleveland, OH", 41.499, -81.694),
    ("Columbus, OH", 39.961, -82.999),
    ("Dallas, TX", 32.777, -96.797),
    ("Denver, CO", 39.739, -104.990),
    ("Des Moines, IA", 41.587, -93.625),
    ("Detroit, MI", 42.331, -83.046),
    ("El Paso, TX", 31.762, -106.485),
    ("Houston, TX", 29.760, -95.370),
    ("Indianapolis, IN", 39.768, -86.158),
    ("Jacksonville, FL", 30.332, -81.656),
    ("Kansas City, MO", 39.100, -94.579),
    ("Las Vegas, NV", 36.170, -115.140),
    ("Laramie, WY", 41.311, -105.591),
    ("Little Rock, AR", 34.746, -92.290),
    ("Los Angeles, CA", 34.052, -118.244),
    ("Louisville, KY", 38.253, -85.759),
    ("Memphis, TN", 35.149, -90.049),
    ("Miami, FL", 25.762, -80.192),
    ("Minneapolis, MN", 44.978, -93.265),
    ("Nashville, TN", 36.163, -86.781),
    ("New Orleans, LA", 29.951, -90.072),
    ("New York, NY", 40.713, -74.006),
    ("North Platte, NE", 41.124, -100.765),
    ("Oklahoma City, OK", 35.468, -97.516),
    ("Omaha, NE", 41.257, -95.935),
    ("Philadelphia, PA", 39.953, -75.165),
    ("Phoenix, AZ", 33.448, -112.074),
    ("Pittsburgh, PA", 40.441, -79.996),
    ("Portland, OR", 45.515, -122.679),
    ("Rapid City, SD", 44.081, -103.231),
    ("Reno, NV", 39.530, -119.814),
    ("Sacramento, CA", 38.582, -121.494),
    ("Salt Lake City, UT", 40.761, -111.891),
    ("San Antonio, TX", 29.424, -98.494),
    ("San Francisco, CA", 37.775, -122.419),
    ("Seattle, WA", 47.606, -122.332),
    ("Sioux Falls, SD", 43.546, -96.731),
    ("St. Louis, MO", 38.627, -90.199),
    ("Tulsa, OK", 36.154, -95.993),
    ("Wichita, KS", 37.687, -97.330),
]

ROAD_FACTOR = 1.12  # roads are longer than great circles


class DemoGeo:
    def search(self, query: str, limit: int = 5) -> list[Place]:
        q = query.strip().lower()
        if not q:
            return []
        hits = [c for c in CITIES if c[0].lower().startswith(q)] + [
            c for c in CITIES if q in c[0].lower() and not c[0].lower().startswith(q)
        ]
        return [Place(lat, lng, name) for name, lat, lng in hits[:limit]]

    def reverse(self, lat: float, lng: float) -> str | None:
        name, clat, clng = min(CITIES, key=lambda c: haversine_mi((lat, lng), (c[1], c[2])))
        d = haversine_mi((lat, lng), (clat, clng))
        if d < 15:
            return name
        if d < 150:
            return f"{round(d)} mi from {name}"
        return None


def _path(points: list[LatLng], step_mi: float = 3.0) -> list[LatLng]:
    """A smooth curve through the control points with a gentle road-like wobble."""
    o, d = points[0], points[-1]
    total = haversine_mi(o, d)
    n = max(8, int(total * ROAD_FACTOR / step_mi))
    if len(points) == 3:  # quadratic Bezier passing through the middle point at t=0.5
        v = points[1]
        ctrl = (2 * v[0] - (o[0] + d[0]) / 2, 2 * v[1] - (o[1] + d[1]) / 2)
    else:
        ctrl = ((o[0] + d[0]) / 2, (o[1] + d[1]) / 2)
    brg = bearing_deg(o, d)
    out: list[LatLng] = []
    for k in range(n + 1):
        t = k / n
        lat = (1 - t) ** 2 * o[0] + 2 * (1 - t) * t * ctrl[0] + t**2 * d[0]
        lng = (1 - t) ** 2 * o[1] + 2 * (1 - t) * t * ctrl[1] + t**2 * d[1]
        wobble = math.sin(t * math.pi * 7) * math.sin(t * math.pi) * total * 0.012
        out.append(offset_point((lat, lng), brg + 90, wobble))
    return out


def _speed_mph(lat: float, lng: float) -> float:
    # Slower in the mountain west and near big cities; ~62 mph cruising otherwise.
    mountain = 14 if -117 < lng < -104 and lat > 35 else 0
    urban = min((max(0.0, 18 - haversine_mi((lat, lng), (c[1], c[2])) / 2) for c in CITIES), default=0)
    return max(28.0, 63 - mountain - urban)


class DemoRouter:
    """Returns the direct route plus one alternative, so the planner's via-point
    fallback is exercised to reach three options (as happens with real HERE data)."""

    def routes(self, origin, destination, departure, gross_weight_lb, alternatives=2, via=None):
        dist = haversine_mi(origin, destination)
        if dist < 0.5:
            raise ProviderError("no_route", "Origin and destination are the same place.", 422)
        if via is not None:
            shapes = [[origin, via, destination]]
        else:
            mid = ((origin[0] + destination[0]) / 2, (origin[1] + destination[1]) / 2)
            brg = bearing_deg(origin, destination)
            shapes = [[origin, destination]]
            if alternatives >= 1:
                shapes.append([origin, offset_point(mid, brg + 90, dist * 0.09), destination])
        out = []
        for shape in shapes:
            pts = _path(shape)
            times = [0.0]
            for a, b in zip(pts, pts[1:]):
                mph = _speed_mph((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                times.append(times[-1] + haversine_mi(a, b) / mph * 3600)
            out.append(RawRoute(pts, times, ["Demo route geometry (synthetic)."]))
        return out


@dataclass(frozen=True)
class Storm:
    lat: float
    lng: float
    vlat: float  # degrees per hour
    vlng: float
    radius_mi: float
    peak_h: float  # hours after series start when intensity peaks
    span_h: float
    wind: float  # added sustained wind at the core, mph
    rain: float  # in/hr at the core
    snow: float  # in/hr at the core


STORMS = [
    Storm(41.6, -104.5, 0.02, 0.32, 190, 20, 14, 50, 0.04, 0.0),  # Plains wind event
    Storm(39.2, -92.5, 0.10, 0.30, 150, 28, 12, 22, 0.62, 0.0),  # Midwest rain band
    Storm(44.8, -109.0, -0.02, 0.10, 170, 26, 16, 30, 0.0, 2.4),  # N. Rockies snow
    Storm(33.2, -88.5, 0.08, 0.35, 110, 22, 8, 34, 1.15, 0.0),  # Deep South storms
    Storm(42.6, -77.5, 0.03, 0.20, 140, 44, 12, 20, 0.08, 1.2),  # Northeast lake snow
    Storm(31.0, -101.0, 0.06, 0.25, 160, 36, 12, 37, 0.0, 0.0),  # West Texas wind
]


class DemoWeather:
    def hourly(self, points: list[LatLng], forecast_days: int) -> dict[LatLng, HourlySeries]:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        hours = max(1, min(16, forecast_days)) * 24
        return {p: self._series(p, start, hours) for p in points}

    @staticmethod
    def _series(p: LatLng, start: datetime, hours: int) -> HourlySeries:
        lat, lng = p
        keys = ("wind_mph", "gust_mph", "rain_in", "snow_in", "weather_code", "visibility_m", "temp_f")
        vals: dict[str, list[float | None]] = {k: [] for k in keys}
        for h in range(hours):
            base_wind = 7 + 4 * math.sin(lat * 0.7 + h * 0.26) + 3 * math.cos(lng * 0.4)
            wind, rain, snow = base_wind, 0.0, 0.0
            for s in STORMS:
                envelope = math.exp(-(((h - s.peak_h) / s.span_h) ** 2))
                c = (s.lat + s.vlat * (h - s.peak_h), s.lng + s.vlng * (h - s.peak_h))
                k = math.exp(-((haversine_mi(p, c) / s.radius_mi) ** 2)) * envelope
                wind += s.wind * k
                rain += s.rain * k
                snow += s.snow * k
            temp = 66 - (lat - 28) * 1.6 - 8 * math.cos((h % 24 - 15) / 24 * 2 * math.pi) - snow * 6
            code = 0.0
            if snow >= 0.05:
                code = 75.0 if snow > 1.0 else 73.0 if snow > 0.4 else 71.0
            elif rain >= 0.01:
                code = 66.0 if temp < 33 else (65.0 if rain > 0.3 else 63.0 if rain > 0.1 else 61.0)
            vis = 24000 - 9000 * min(rain, 1.5) - 14000 * min(snow, 1.6)
            vals["wind_mph"].append(round(wind, 1))
            vals["gust_mph"].append(round(wind * 1.35 + 3, 1))
            vals["rain_in"].append(round(rain, 3))
            vals["snow_in"].append(round(snow, 2))
            vals["weather_code"].append(code)
            vals["visibility_m"].append(max(150.0, vis))
            vals["temp_f"].append(round(temp, 1))
        return HourlySeries(start, vals)
