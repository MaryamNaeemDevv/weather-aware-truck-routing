"""Route evaluation and recommendation ranking.

Ranking (spec order, plus two deliberate additions documented in the README):

0. **No Travel gate** — a route with any No Travel miles ranks below every route
   without them; among gated routes, fewer No Travel miles is better.
1. Fewest Severe miles
2. Fewest High miles
3. Lowest distance-weighted average risk
4. Shortest travel time (wall clock, including HOS stops)
5. Deterministic tiebreak: shorter distance, then route id

**Tolerances.** Comparing raw floats would make criteria 3–4 almost never
matter. Level-mile differences below half a sampling interval (the resolution of
one checkpoint) count as ties, as do average-risk differences below 0.1.

**Unknown miles** (no forecast) are counted as High *for ranking only*, so a
route can never look safer because we know less about it. They are reported
separately in the UI.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .geo import MeasuredLine, owned_miles, sample_miles
from .hos import Stop, elapsed_at
from .risk import Assessment, Level, advisories, assess
from .weather import HourlySeries

AVG_RISK_TOLERANCE = 0.1
SeriesLookup = Callable[[float, float], "HourlySeries | None"]


@dataclass
class CheckpointEval:
    index: int
    mile: float
    lat: float
    lng: float
    owned_mi: float
    span: tuple[float, float]  # (start mile, end mile) this checkpoint represents
    drive_s: float
    eta: datetime
    weather: dict[str, float | None]
    assessment: Assessment
    advisories: list[str]


@dataclass
class RouteScore:
    miles_by_level: dict[Level, float]
    unknown_miles: float
    avg_risk: float | None  # over known miles; None if nothing known
    max_level: Level | None
    elapsed_s: float
    distance_mi: float

    @property
    def no_travel_miles(self) -> float:
        return self.miles_by_level[Level.NO_TRAVEL]

    @property
    def severe_miles(self) -> float:
        return self.miles_by_level[Level.SEVERE]

    @property
    def high_miles(self) -> float:
        return self.miles_by_level[Level.HIGH]

    # Values used for ranking: unknown counts as High.
    @property
    def rank_high_miles(self) -> float:
        return self.high_miles + self.unknown_miles

    @property
    def rank_avg_risk(self) -> float:
        known = sum(self.miles_by_level.values())
        total = known + self.unknown_miles
        if total <= 0:
            return 0.0
        weighted = sum(int(lv) * mi for lv, mi in self.miles_by_level.items())
        weighted += int(Level.HIGH) * self.unknown_miles
        return weighted / total


def evaluate_checkpoints(
    line: MeasuredLine,
    departure: datetime,
    interval_mi: float,
    load_lb: float,
    stops: list[Stop],
    lookup: SeriesLookup,
) -> list[CheckpointEval]:
    marks = sample_miles(line.length_mi, interval_mi)
    owned = owned_miles(marks, line.length_mi)
    bounds = [0.0] + [(a + b) / 2 for a, b in zip(marks, marks[1:])] + [line.length_mi]
    out = []
    for i, (mile, own) in enumerate(zip(marks, owned)):
        lat, lng = line.point_at(mile)
        drive_s = line.drive_s_at(mile)
        eta = departure + timedelta(seconds=elapsed_at(drive_s, stops))
        series = lookup(lat, lng)
        wx = series.at(eta) if series is not None else {}
        a = assess(wx.get("wind_mph"), wx.get("rain_in"), wx.get("snow_in"), load_lb)
        adv = advisories(
            int(wx["weather_code"]) if wx.get("weather_code") is not None else None,
            wx.get("visibility_m"),
            wx.get("gust_mph"),
            wx.get("wind_mph"),
        )
        out.append(CheckpointEval(i, mile, lat, lng, own, (bounds[i], bounds[i + 1]), drive_s, eta, wx, a, adv))
    return out


def score_route(checkpoints: list[CheckpointEval], elapsed_s: float, distance_mi: float) -> RouteScore:
    by_level = {lv: 0.0 for lv in Level}
    unknown = 0.0
    for cp in checkpoints:
        if cp.assessment.level is None:
            unknown += cp.owned_mi
        else:
            by_level[cp.assessment.level] += cp.owned_mi
    known = sum(by_level.values())
    avg = (sum(int(lv) * mi for lv, mi in by_level.items()) / known) if known > 0 else None
    levels = [cp.assessment.level for cp in checkpoints if cp.assessment.level is not None]
    return RouteScore(by_level, unknown, avg, max(levels) if levels else None, elapsed_s, distance_mi)


@dataclass(frozen=True)
class Decision:
    """Outcome of comparing two routes: which criterion decided it."""

    criterion: str
    sign: int  # -1: a better, +1: b better, 0: tie


CRITERIA = ("no_travel_gate", "no_travel_miles", "severe_miles", "high_miles", "avg_risk", "time", "distance")


def compare(a: RouteScore, b: RouteScore, miles_tol: float) -> Decision:
    def miles_cmp(x: float, y: float) -> int:
        if abs(x - y) <= miles_tol:
            return 0
        return -1 if x < y else 1

    a_gate, b_gate = a.no_travel_miles > 0, b.no_travel_miles > 0
    if a_gate != b_gate:
        return Decision("no_travel_gate", 1 if a_gate else -1)
    if a_gate and (s := miles_cmp(a.no_travel_miles, b.no_travel_miles)):
        return Decision("no_travel_miles", s)
    if s := miles_cmp(a.severe_miles, b.severe_miles):
        return Decision("severe_miles", s)
    if s := miles_cmp(a.rank_high_miles, b.rank_high_miles):
        return Decision("high_miles", s)
    if abs(a.rank_avg_risk - b.rank_avg_risk) > AVG_RISK_TOLERANCE:
        return Decision("avg_risk", -1 if a.rank_avg_risk < b.rank_avg_risk else 1)
    if a.elapsed_s != b.elapsed_s:
        return Decision("time", -1 if a.elapsed_s < b.elapsed_s else 1)
    if a.distance_mi != b.distance_mi:
        return Decision("distance", -1 if a.distance_mi < b.distance_mi else 1)
    return Decision("distance", 0)


def rank(scores: dict[str, RouteScore], miles_tol: float) -> list[str]:
    """Route ids best-first. Ties fall back to id order so results never flicker."""

    def cmp(x: str, y: str) -> int:
        s = compare(scores[x], scores[y], miles_tol).sign
        return s if s else (-1 if x < y else (1 if x > y else 0))

    return sorted(scores, key=functools.cmp_to_key(cmp))


def miles_tolerance(interval_mi: float) -> float:
    return interval_mi / 2


@dataclass
class Stretch:
    level: Level
    start_index: int
    end_index: int
    start_mile: float
    end_mile: float
    factors: list[str] = field(default_factory=list)
    peak: dict[str, float] = field(default_factory=dict)

    @property
    def miles(self) -> float:
        return self.end_mile - self.start_mile


def worst_stretch(checkpoints: list[CheckpointEval]) -> Stretch | None:
    """The longest contiguous run at the route's worst level (Moderate or above)."""
    levels = [cp.assessment.level for cp in checkpoints]
    known = [lv for lv in levels if lv is not None]
    if not known or max(known) < Level.MODERATE:
        return None
    top = max(known)
    best: Stretch | None = None
    i = 0
    while i < len(checkpoints):
        if levels[i] != top:
            i += 1
            continue
        j = i
        while j + 1 < len(checkpoints) and levels[j + 1] == top:
            j += 1
        run = checkpoints[i : j + 1]
        start, end = run[0].span[0], run[-1].span[1]
        factors = sorted({f for cp in run for f in cp.assessment.drivers})
        peak: dict[str, float] = {}
        for key in ("wind_mph", "gust_mph", "rain_in", "snow_in"):
            vals = [cp.weather.get(key) for cp in run if cp.weather.get(key) is not None]
            if vals:
                peak[key] = max(vals)  # type: ignore[type-var]
        stretch = Stretch(top, i, j, start, end, factors, peak)
        if best is None or stretch.miles > best.miles:
            best = stretch
        i = j + 1
    return best
