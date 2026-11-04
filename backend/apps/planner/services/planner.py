"""Plan orchestration: routes -> checkpoints -> weather -> risk -> recommendation."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from shapely.geometry import LineString

from ..domain import hos
from ..domain.geo import sample_miles
from ..domain.risk import Level
from ..domain.scoring import (
    CheckpointEval,
    RouteScore,
    compare,
    evaluate_checkpoints,
    miles_tolerance,
    rank,
    score_route,
    worst_stretch,
)
from ..providers.base import Place, ProviderError
from ..providers.registry import get_providers
from .routes_service import Candidate, candidate_routes
from .weather_service import WeatherLookup

MAX_DEPARTURE_AHEAD = timedelta(days=7)
PAST_GRACE = timedelta(minutes=10)
LEAVE_LATER_OFFSETS_H = list(range(2, 25, 2))
FEDERAL_GROSS_LIMIT_LB = 80_000
LEVEL_KEYS = {
    Level.LOW: "low",
    Level.MODERATE: "moderate",
    Level.HIGH: "high",
    Level.SEVERE: "severe",
    Level.NO_TRAVEL: "no_travel",
}


@dataclass
class PlanRequest:
    origin: Place
    destination: Place
    departure_local: datetime | None  # naive wall-clock time at the origin; None = now
    load_lb: float
    interval_mi: int = 25
    hos_enabled: bool = True


@lru_cache(maxsize=1)
def _tzfinder():
    from timezonefinder import TimezoneFinder

    return TimezoneFinder(in_memory=True)


@lru_cache(maxsize=4096)
def tz_name(lat: float, lng: float) -> str:
    name = _tzfinder().timezone_at(lat=round(lat, 3), lng=round(lng, 3))
    return name or "UTC"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_duration(seconds: float) -> str:
    minutes = int(round(abs(seconds) / 60))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m:02d}m"
    return f"{h}h" if h else f"{m} min"


def resolve_departure(req: PlanRequest, now: datetime) -> tuple[datetime, str, list[str]]:
    tz = tz_name(req.origin.lat, req.origin.lng)
    warnings: list[str] = []
    if req.departure_local is None:
        return now, tz, warnings
    local = req.departure_local.replace(tzinfo=ZoneInfo(tz))
    dep = local.astimezone(UTC)
    if dep < now - PAST_GRACE:
        warnings.append("Departure time was in the past, so the plan uses the current time instead.")
        dep = now
    elif dep > now + MAX_DEPARTURE_AHEAD:
        raise ProviderError(
            "departure_too_far",
            "Choose a departure within the next 7 days — forecasts further out are not reliable enough to route on.",
            422,
        )
    return max(dep, now), tz, warnings


@dataclass
class RouteEval:
    cand: Candidate
    stops: list[hos.Stop]
    elapsed_s: float
    checkpoints: list[CheckpointEval]
    score: RouteScore


def _evaluate(
    cands: dict[str, Candidate], stops: dict[str, list[hos.Stop]], departure, req, lookup
) -> dict[str, RouteEval]:
    out = {}
    for rid, c in cands.items():
        elapsed = hos.total_elapsed(c.line.drive_s, stops[rid])
        cps = evaluate_checkpoints(c.line, departure, req.interval_mi, req.load_lb, stops[rid], lookup)
        out[rid] = RouteEval(c, stops[rid], elapsed, cps, score_route(cps, elapsed, c.line.length_mi))
    return out


def build_plan(req: PlanRequest) -> dict:
    providers = get_providers()
    cfg = settings.PLANNER
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    departure, origin_tz, warnings = resolve_departure(req, now)

    gross = req.load_lb + cfg["TRACTOR_TRAILER_TARE_LB"]
    if gross > FEDERAL_GROSS_LIMIT_LB:
        warnings.append(
            f"Estimated gross weight {gross:,.0f} lb (load + {cfg['TRACTOR_TRAILER_TARE_LB']:,.0f} lb tractor-trailer) "
            f"exceeds the {FEDERAL_GROSS_LIMIT_LB:,} lb federal limit — an overweight permit is required."
        )

    candidates, route_warnings = candidate_routes(
        providers.router, (req.origin.lat, req.origin.lng), (req.destination.lat, req.destination.lng), departure, gross
    )
    warnings += route_warnings
    cands = {chr(ord("A") + i): c for i, c in enumerate(candidates)}
    stops = {rid: hos.plan_stops(c.line.drive_s, req.hos_enabled) for rid, c in cands.items()}

    # One weather fetch covers the plan and the leave-later scan.
    max_elapsed = max(hos.total_elapsed(c.line.drive_s, stops[rid]) for rid, c in cands.items())
    scan_until = departure + timedelta(seconds=max_elapsed) + timedelta(hours=max(LEAVE_LATER_OFFSETS_H))
    points = []
    for c in cands.values():
        points += [c.line.point_at(m) for m in sample_miles(c.line.length_mi, req.interval_mi)]
    lookup = WeatherLookup(providers.weather, providers.mode, points, scan_until)

    evals = _evaluate(cands, stops, departure, req, lookup)
    tol = miles_tolerance(req.interval_mi)
    scores = {rid: e.score for rid, e in evals.items()}
    order = rank(scores, tol)
    recommendation = _recommendation(order, scores, tol)
    leave_later = _leave_later(cands, stops, departure, req, lookup, scores[order[0]], tol, origin_tz)

    if any(e.score.unknown_miles > 0 for e in evals.values()):
        warnings.append(
            "Part of the trip is beyond the available forecast. Those miles are shown as 'No forecast' "
            "and counted as High risk when comparing routes."
        )

    stretches = {rid: worst_stretch(e.checkpoints) for rid, e in evals.items()}
    places = _stretch_places(providers.geocoder, evals, stretches)

    plan_id = uuid.uuid4().hex[:12]
    routes_out = [
        _route_json(rid, evals[rid], departure, stretches[rid], places.get(rid), req.interval_mi) for rid in cands
    ]
    cache.set(
        f"plan:{plan_id}",
        {
            "routes": {r["id"]: r["geometry"] for r in routes_out},
            "load_lb": req.load_lb,
            "mode": providers.mode,
        },
        timeout=cfg["PLAN_TTL_S"],
    )
    return {
        "plan_id": plan_id,
        "mode": providers.mode,
        "generated_utc": iso(datetime.now(UTC)),
        "forecast_source": "Open-Meteo" if providers.mode == "live" else "Synthetic demo weather",
        "inputs": {
            "origin": vars(req.origin),
            "destination": vars(req.destination),
            "departure_utc": iso(departure),
            "departure_tz": origin_tz,
            "load_lb": req.load_lb,
            "gross_weight_lb": gross,
            "interval_mi": req.interval_mi,
            "hos_enabled": req.hos_enabled,
        },
        "warnings": warnings,
        "routes": routes_out,
        "recommendation": recommendation,
        "leave_later": leave_later,
        "stats": {"weather_locations_fetched": lookup.fetched, "weather_locations_cached": lookup.cached},
    }


# --- Recommendation text ------------------------------------------------------------


def _reason(criterion: str, w: str, o: str, sw: RouteScore, so: RouteScore) -> str:
    if criterion == "no_travel_gate":
        return f"avoids the No Travel conditions on Route {o} ({so.no_travel_miles:.0f} mi)"
    if criterion == "no_travel_miles":
        return f"fewer No Travel miles than Route {o} ({sw.no_travel_miles:.0f} vs {so.no_travel_miles:.0f})"
    if criterion == "severe_miles":
        return f"{sw.severe_miles:.0f} Severe miles vs {so.severe_miles:.0f} on Route {o}"
    if criterion == "high_miles":
        return f"{sw.rank_high_miles:.0f} High miles vs {so.rank_high_miles:.0f} on Route {o}"
    if criterion == "avg_risk":
        return f"lower average risk than Route {o} ({sw.rank_avg_risk:.2f} vs {so.rank_avg_risk:.2f})"
    if criterion == "time":
        return f"similar weather risk to Route {o} and {fmt_duration(so.elapsed_s - sw.elapsed_s)} faster"
    return f"same risk and time as Route {o}, but shorter"


def _status(score: RouteScore) -> str:
    if score.no_travel_miles > 0:
        return "no_travel"
    if score.severe_miles > 0 or score.high_miles > 0 or score.unknown_miles > 0:
        return "caution"
    return "go"


def _recommendation(order: list[str], scores: dict[str, RouteScore], tol: float) -> dict:
    best = order[0]
    sb = scores[best]
    reasons = []
    for other in order[1:]:
        so = scores[other]
        d = compare(sb, so, tol)
        text = _reason(d.criterion, best, other, sb, so)
        if d.criterion not in ("time", "distance") and sb.elapsed_s > so.elapsed_s + 60:
            text += f", at the cost of {fmt_duration(sb.elapsed_s - so.elapsed_s)} more travel time"
        reasons.append({"versus": other, "criterion": d.criterion, "text": text})

    status = _status(sb)
    if status == "no_travel":
        headline = "Don't depart now — every route crosses No Travel conditions."
    elif len(order) == 1:
        headline = f"Route {best} is the only truck route."
    elif all(r["criterion"] in ("time", "distance") for r in reasons):
        if status == "go":
            headline = f"Take Route {best}: no significant weather risk on any route, so the fastest wins."
        else:
            headline = f"Take Route {best}: weather risk is similar on all routes, and it is the fastest."
    else:
        headline = f"Take Route {best}: it has the lowest weather risk."
    return {"route_id": best, "ranking": order, "status": status, "headline": headline, "reasons": reasons}


def _leave_later(cands, stops, departure, req, lookup, current_best: RouteScore, tol, tz) -> dict | None:
    """Re-score every route with later departures (no new routing calls).

    Only suggests a later departure when it materially lowers risk (No Travel,
    Severe or High miles) — not merely to shave average risk.
    """
    best = None
    for offset in LEAVE_LATER_OFFSETS_H:
        dep = departure + timedelta(hours=offset)
        evals = _evaluate(cands, stops, dep, req, lookup)
        scores = {rid: e.score for rid, e in evals.items()}
        rid = rank(scores, tol)[0]
        d = compare(scores[rid], current_best, tol)
        if d.sign < 0 and d.criterion in ("no_travel_gate", "no_travel_miles", "severe_miles", "high_miles"):
            if best is None or compare(scores[rid], best[2], tol).sign < 0:
                best = (offset, rid, scores[rid], d.criterion)
    if best is None:
        return None
    offset, rid, s, criterion = best
    dep = departure + timedelta(hours=offset)
    local = dep.astimezone(ZoneInfo(tz))
    gains = []
    if current_best.no_travel_miles > s.no_travel_miles:
        gains.append(f"No Travel miles {current_best.no_travel_miles:.0f} → {s.no_travel_miles:.0f}")
    if current_best.severe_miles > s.severe_miles:
        gains.append(f"Severe miles {current_best.severe_miles:.0f} → {s.severe_miles:.0f}")
    if current_best.rank_high_miles > s.rank_high_miles:
        gains.append(f"High miles {current_best.rank_high_miles:.0f} → {s.rank_high_miles:.0f}")
    return {
        "offset_h": offset,
        "departure_utc": iso(dep),
        "route_id": rid,
        "criterion": criterion,
        "text": f"Leaving {offset} h later ({local.strftime('%a %-I:%M %p')} {local.tzname()}) on Route {rid} "
        f"lowers risk: {', '.join(gains)}.",
        "summary": _summary(s),
    }


def _stretch_places(geocoder, evals, stretches) -> dict[str, str | None]:
    jobs = {}
    for rid, st in stretches.items():
        if st is not None:
            mid = evals[rid].checkpoints[(st.start_index + st.end_index) // 2]
            jobs[rid] = (mid.lat, mid.lng)
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        names = list(pool.map(lambda p: _reverse_cached(geocoder, *p), jobs.values()))
    return dict(zip(jobs, names))


def _reverse_cached(geocoder, lat: float, lng: float) -> str | None:
    key = f"rev:{lat:.2f}:{lng:.2f}"
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    name = geocoder.reverse(lat, lng)
    cache.set(key, name or "", timeout=7 * 86400)
    return name


# --- Serialization ------------------------------------------------------------------


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(float(x), n)


def _summary(s: RouteScore) -> dict:
    return {
        "miles_by_level": {LEVEL_KEYS[lv]: round(mi, 1) for lv, mi in s.miles_by_level.items()},
        "unknown_mi": round(s.unknown_miles, 1),
        "avg_risk": _r(s.avg_risk),
        "max_level": None if s.max_level is None else int(s.max_level),
    }


def _route_json(rid: str, e: RouteEval, departure: datetime, stretch, place: str | None, interval: int) -> dict:
    line = e.cand.line
    simplified = LineString([(lng, lat) for lat, lng in line.coords]).simplify(0.0008, preserve_topology=False)
    geometry = [[round(x, 5), round(y, 5)] for x, y in simplified.coords]

    stops_out = []
    for s in e.stops:
        mile = line.mile_at_drive_s(s.at_drive_s)
        lat, lng = line.point_at(mile)
        start = departure + timedelta(seconds=hos.elapsed_at(s.at_drive_s, e.stops))
        stops_out.append(
            {
                "kind": s.kind,
                "mile": round(mile, 1),
                "lat": _r(lat, 5),
                "lng": _r(lng, 5),
                "start_utc": iso(start),
                "end_utc": iso(start + timedelta(seconds=s.duration_s)),
                "duration_s": s.duration_s,
                "tz": tz_name(lat, lng),
            }
        )

    # Time-indexed track so the map can place a 'truck ghost' at any slider hour.
    n = min(400, max(50, int(line.length_mi / 2)))
    track_drive = sorted({line.drive_s * k / n for k in range(n + 1)} | {s.at_drive_s for s in e.stops})
    track = []
    for d in track_drive:
        lat, lng = line.point_at(line.mile_at_drive_s(d))
        t0 = hos.elapsed_at(d, e.stops)
        track.append([round(t0 / 60, 1), round(lng, 5), round(lat, 5)])
        stop = next((s for s in e.stops if s.at_drive_s == d), None)
        if stop:  # parked: same position until the stop ends
            track.append([round((t0 + stop.duration_s) / 60, 1), round(lng, 5), round(lat, 5)])

    cps = []
    for cp in e.checkpoints:
        wx = cp.weather
        cps.append(
            {
                "i": cp.index,
                "mile": round(cp.mile, 1),
                "lat": _r(cp.lat, 5),
                "lng": _r(cp.lng, 5),
                "span": [round(cp.span[0], 1), round(cp.span[1], 1)],
                "eta_utc": iso(cp.eta),
                "tz": tz_name(cp.lat, cp.lng),
                "level": cp.assessment.code,
                "levels": {
                    "wind": _code(cp.assessment.wind_level),
                    "rain": _code(cp.assessment.rain_level),
                    "snow": _code(cp.assessment.snow_level),
                },
                "drivers": list(cp.assessment.drivers),
                "load_escalated": cp.assessment.load_escalated,
                "advisories": cp.advisories,
                "wx": {
                    "wind_mph": _r(wx.get("wind_mph"), 1),
                    "gust_mph": _r(wx.get("gust_mph"), 1),
                    "rain_in": _r(wx.get("rain_in"), 3),
                    "snow_in": _r(wx.get("snow_in"), 2),
                    "temp_f": _r(wx.get("temp_f"), 0),
                    "visibility_m": _r(wx.get("visibility_m"), 0),
                },
            }
        )

    worst = None
    if stretch is not None:
        start_cp, end_cp = e.checkpoints[stretch.start_index], e.checkpoints[stretch.end_index]
        worst = {
            "level": int(stretch.level),
            "factors": stretch.factors,
            "start_mile": round(stretch.start_mile, 1),
            "end_mile": round(stretch.end_mile, 1),
            "start_utc": iso(start_cp.eta),
            "end_utc": iso(end_cp.eta),
            "tz": tz_name(start_cp.lat, start_cp.lng),
            "place": place,
            "peak": {k: _r(v, 2) for k, v in stretch.peak.items()},
        }

    advisories = sorted({a for cp in e.checkpoints for a in cp.advisories})
    return {
        "id": rid,
        "flags": e.cand.flags,
        "notices": e.cand.raw.notices,
        "source": e.cand.raw.source,
        "distance_mi": round(line.length_mi, 1),
        "drive_s": round(line.drive_s),
        "elapsed_s": round(e.elapsed_s),
        "arrival_utc": iso(departure + timedelta(seconds=e.elapsed_s)),
        "arrival_tz": tz_name(*line.coords[-1]),
        "geometry": geometry,
        "track": track,
        "stops": stops_out,
        "checkpoints": cps,
        "summary": _summary(e.score),
        "worst_stretch": worst,
        "advisories": advisories,
    }


def _code(level) -> int:
    return -1 if level is None else int(level)
