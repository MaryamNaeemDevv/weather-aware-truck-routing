"""Cached, deduplicated weather lookup.

Points are snapped to a ~0.1° grid, so checkpoints from all three routes (and
the heatmap) share requests. Each grid cell's whole hourly series is cached for
WEATHER_TTL_S; keys include the current UTC hour, so a new model run is picked
up within the hour.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache import cache

from ..domain.weather import HourlySeries, grid_key
from ..providers.base import LatLng, WeatherProvider


def forecast_days_for(until: datetime) -> int:
    """Days of forecast (from 00:00 UTC today) needed to cover `until`."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, min(16, math.ceil((until - today).total_seconds() / 86400) + 1))


def _cache_key(mode: str, key: LatLng) -> str:
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    return f"wx:{mode}:{hour}:{key[0]:.2f}:{key[1]:.2f}"


def _dump(s: HourlySeries, days: int) -> dict:
    return {"start": s.start.timestamp(), "days": days, "values": s.values}


def _load(d: dict) -> HourlySeries:
    return HourlySeries(datetime.fromtimestamp(d["start"], tz=UTC), d["values"])


class WeatherLookup:
    """Fetches all series up front; `__call__(lat, lng)` then answers from memory."""

    def __init__(self, provider: WeatherProvider, mode: str, points: list[LatLng], until: datetime):
        days = forecast_days_for(until)
        keys = sorted({grid_key(lat, lng) for lat, lng in points})
        self.series: dict[LatLng, HourlySeries] = {}
        missing: list[LatLng] = []
        cached = cache.get_many([_cache_key(mode, k) for k in keys])
        for k in keys:
            hit = cached.get(_cache_key(mode, k))
            if hit and hit["days"] >= days:
                self.series[k] = _load(hit)
            else:
                missing.append(k)
        if missing:
            fetched = provider.hourly(missing, days)
            self.series.update(fetched)
            cache.set_many(
                {_cache_key(mode, k): _dump(s, days) for k, s in fetched.items()},
                timeout=settings.PLANNER["WEATHER_TTL_S"],
            )
        self.fetched = len(missing)
        self.cached = len(keys) - len(missing)

    def __call__(self, lat: float, lng: float) -> HourlySeries | None:
        return self.series.get(grid_key(lat, lng))
