"""Builds the configured providers once per process."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

from .base import GeocodingProvider, RoutingProvider, WeatherProvider
from .demo import DemoGeo, DemoRouter, DemoWeather


@dataclass(frozen=True)
class Providers:
    mode: str  # "live" | "demo"
    router: RoutingProvider
    geocoder: GeocodingProvider
    weather: WeatherProvider


@lru_cache(maxsize=1)
def get_providers() -> Providers:
    cfg = settings.PLANNER
    if cfg["PROVIDER"] == "demo":
        return Providers("demo", DemoRouter(), DemoGeo(), DemoWeather())
    from .here import HereClient
    from .open_meteo import OpenMeteoClient

    here = HereClient(cfg["HERE_API_KEY"], cfg["HTTP_TIMEOUT_S"])
    weather = OpenMeteoClient(cfg["OPEN_METEO_BASE_URL"], cfg["OPEN_METEO_API_KEY"], cfg["HTTP_TIMEOUT_S"])
    return Providers("live", here, here, weather)
