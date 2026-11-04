"""Hourly weather series and how to read them at an arbitrary time.

Open-Meteo semantics (and our demo provider mirrors them):

* Instantaneous variables (wind speed, gusts, temperature, visibility) are valid
  *at* the hour label. Between hours we interpolate linearly.
* Accumulated variables (rain, snowfall, precipitation) at label T are the sum
  over the *preceding* hour (T-1h, T]. The rate a truck experiences at 14:30 is
  therefore the value labelled 15:00 — i.e. label = floor(t) + 1h. A truck at
  exactly 15:00 is starting the hour 15:00–16:00, so it uses label 16:00.

Anything outside the series is None (unknown), never zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

INSTANT_VARS = ("wind_mph", "gust_mph", "temp_f", "visibility_m")
ACCUM_VARS = ("rain_in", "snow_in")
CODE_VARS = ("weather_code",)
ALL_VARS = INSTANT_VARS + ACCUM_VARS + CODE_VARS


@dataclass
class HourlySeries:
    """Hourly values for one location. `start` is the first hour label (UTC, on the hour)."""

    start: datetime
    values: dict[str, list[float | None]]

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("series start must be timezone-aware")
        self.start = self.start.astimezone(UTC)
        self.hours = len(next(iter(self.values.values()))) if self.values else 0

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=max(self.hours - 1, 0))

    def _get(self, var: str, idx: int) -> float | None:
        if idx < 0 or idx >= self.hours:
            return None
        v = self.values.get(var, [None] * self.hours)[idx]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return float(v)

    def at(self, when: datetime) -> dict[str, float | None]:
        """Weather experienced at `when` (aware datetime)."""
        offset_h = (when.astimezone(UTC) - self.start).total_seconds() / 3600
        base = math.floor(offset_h)
        frac = offset_h - base
        out: dict[str, float | None] = {}
        for var in INSTANT_VARS:
            a, b = self._get(var, base), self._get(var, base + 1)
            if frac < 1e-9:
                out[var] = a
            elif a is None or b is None:
                # Tolerate a single missing neighbour only when we sit on it.
                out[var] = None
            else:
                out[var] = a + (b - a) * frac
        for var in ACCUM_VARS + CODE_VARS:
            out[var] = self._get(var, base + 1)
        return out

    def hour_index(self, when: datetime) -> float:
        return (when.astimezone(UTC) - self.start).total_seconds() / 3600


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def grid_key(lat: float, lng: float, step: float = 0.1) -> tuple[float, float]:
    """Snap a coordinate to a ~11 km grid, close to forecast-model resolution.
    Checkpoints that share a cell share one weather request."""
    return (round(round(lat / step) * step, 4), round(round(lng / step) * step, 4))
