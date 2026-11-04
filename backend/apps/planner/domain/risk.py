"""Weather risk classification — the spec's risk table and load rules.

Pure functions, no I/O. Every threshold decision is documented here and in the
README ("Decisions & assumptions").

Interval edges
--------------
The spec's table shares endpoints between tiers (e.g. rain 0.10–0.25 and
0.25–0.50). We resolve them as lower-inclusive / upper-exclusive bands, except
where the spec's top tier is written with a strict ">" (rain >1.00, snow >3.0),
which makes the boundary value itself Severe:

    wind  : <25 Low | <35 Moderate | <45 High | <55 Severe | >=55 No Travel
    rain  : <0.10   | <0.25        | <0.50    | <=1.00     | >1.00
    snow  : <0.5    | <1.0         | <2.0     | <=3.0      | >3.0

Load rules (strict ">" on weight, exactly as written in the spec) can only
escalate the wind level, never lower it:

    wind >= 55                       -> No Travel (any load)
    45 <= wind < 55 and load > 30k   -> No Travel
    35 <= wind < 45 and load > 40k   -> Severe

A checkpoint's level is the worst of its wind, rain and snow levels.
Missing data is UNKNOWN — never silently treated as Low.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum


class Level(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self]


LEVEL_LABELS = {
    Level.LOW: "Low",
    Level.MODERATE: "Moderate",
    Level.HIGH: "High",
    Level.SEVERE: "Severe",
    Level.NO_TRAVEL: "No Travel",
}

# Wire encoding for unknown in compact arrays (heatmap strings, etc.).
UNKNOWN_CODE = -1

LOAD_NO_TRAVEL_LB = 30_000
LOAD_SEVERE_LB = 40_000


def _is_missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def classify_wind(mph: float | None) -> Level | None:
    if _is_missing(mph):
        return None
    if mph < 25:
        return Level.LOW
    if mph < 35:
        return Level.MODERATE
    if mph < 45:
        return Level.HIGH
    if mph < 55:
        return Level.SEVERE
    return Level.NO_TRAVEL


def classify_rain(in_per_hr: float | None) -> Level | None:
    if _is_missing(in_per_hr):
        return None
    if in_per_hr < 0.10:
        return Level.LOW
    if in_per_hr < 0.25:
        return Level.MODERATE
    if in_per_hr < 0.50:
        return Level.HIGH
    if in_per_hr <= 1.00:
        return Level.SEVERE
    return Level.NO_TRAVEL


def classify_snow(in_per_hr: float | None) -> Level | None:
    if _is_missing(in_per_hr):
        return None
    if in_per_hr < 0.5:
        return Level.LOW
    if in_per_hr < 1.0:
        return Level.MODERATE
    if in_per_hr < 2.0:
        return Level.HIGH
    if in_per_hr <= 3.0:
        return Level.SEVERE
    return Level.NO_TRAVEL


def apply_load_rules(wind_level: Level, wind_mph: float, load_lb: float) -> Level:
    """Escalate a wind level according to the spec's load rules (never lowers it)."""
    escalated = wind_level
    if wind_mph >= 55:
        escalated = Level.NO_TRAVEL
    elif 45 <= wind_mph < 55 and load_lb > LOAD_NO_TRAVEL_LB:
        escalated = Level.NO_TRAVEL
    elif 35 <= wind_mph < 45 and load_lb > LOAD_SEVERE_LB:
        escalated = Level.SEVERE
    return max(wind_level, escalated)


@dataclass(frozen=True)
class Assessment:
    """Risk at one point in space and time."""

    level: Level | None  # None == unknown (no forecast)
    wind_level: Level | None
    rain_level: Level | None
    snow_level: Level | None
    load_escalated: bool = False
    # Which factors produced the final level (e.g. ["wind"], ["rain", "snow"]).
    drivers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def unknown(self) -> bool:
        return self.level is None

    @property
    def code(self) -> int:
        return UNKNOWN_CODE if self.level is None else int(self.level)


def assess(
    wind_mph: float | None,
    rain_in_hr: float | None,
    snow_in_hr: float | None,
    load_lb: float,
) -> Assessment:
    """Classify one checkpoint. Any missing core variable makes the result unknown,
    because a missing value could hide a No Travel condition."""
    wind_base = classify_wind(wind_mph)
    rain_level = classify_rain(rain_in_hr)
    snow_level = classify_snow(snow_in_hr)

    wind_level = wind_base
    escalated = False
    if wind_base is not None:
        wind_level = apply_load_rules(wind_base, float(wind_mph), load_lb)
        escalated = wind_level > wind_base

    if wind_level is None or rain_level is None or snow_level is None:
        return Assessment(None, wind_level, rain_level, snow_level, escalated, ())

    level = max(wind_level, rain_level, snow_level)
    drivers = tuple(
        name
        for name, lvl in (("wind", wind_level), ("rain", rain_level), ("snow", snow_level))
        if lvl == level and level > Level.LOW
    )
    return Assessment(level, wind_level, rain_level, snow_level, escalated, drivers)


# --- Advisories --------------------------------------------------------------
# Hazards outside the spec's rules. Shown to the driver as badges, but they never
# change the risk level, so the spec's classification stays exact.

FREEZING_WEATHER_CODES = {56, 57, 66, 67}  # WMO: freezing drizzle / freezing rain
LOW_VISIBILITY_M = 400  # roughly a quarter mile


def advisories(
    weather_code: int | None, visibility_m: float | None, gust_mph: float | None, wind_mph: float | None
) -> list[str]:
    out: list[str] = []
    if weather_code in FREEZING_WEATHER_CODES:
        out.append("freezing_rain")
    if not _is_missing(visibility_m) and visibility_m < LOW_VISIBILITY_M:
        out.append("low_visibility")
    if not _is_missing(gust_mph) and not _is_missing(wind_mph) and gust_mph >= 45 and gust_mph - wind_mph >= 15:
        out.append("strong_gusts")
    return out
