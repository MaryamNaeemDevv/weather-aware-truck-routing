"""Open-Meteo hourly forecast client with multi-location batching and unit normalisation."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from ..domain.weather import HourlySeries
from .base import LatLng, ProviderError, get_json

log = logging.getLogger(__name__)

HOURLY = {
    # open-meteo variable -> our variable
    "wind_speed_10m": "wind_mph",
    "wind_gusts_10m": "gust_mph",
    "rain": "rain_in",
    "snowfall": "snow_in",
    "weather_code": "weather_code",
    "visibility": "visibility_m",
    "temperature_2m": "temp_f",
}

# Multiplier to convert the unit Open-Meteo reports into the unit we use.
UNIT_CONVERSIONS = {
    "wind_mph": {"mp/h": 1.0, "mph": 1.0, "km/h": 0.621371, "m/s": 2.236936, "kn": 1.150779},
    "gust_mph": {"mp/h": 1.0, "mph": 1.0, "km/h": 0.621371, "m/s": 2.236936, "kn": 1.150779},
    "rain_in": {"inch": 1.0, "mm": 1 / 25.4},
    "snow_in": {"inch": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4},
    "visibility_m": {"m": 1.0, "ft": 0.3048},
}

CHUNK = 100  # locations per request (keeps URLs well under common limits)


class OpenMeteoClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 15.0):
        self.base_url = base_url
        self.api_key = api_key
        self.http = httpx.Client(timeout=timeout_s, headers={"User-Agent": "weather-aware-truck-routing/1.0"})

    def hourly(self, points: list[LatLng], forecast_days: int) -> dict[LatLng, HourlySeries]:
        if not points:
            return {}
        chunks = [points[i : i + CHUNK] for i in range(0, len(points), CHUNK)]
        out: dict[LatLng, HourlySeries] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
            for result in pool.map(lambda c: self._fetch(c, forecast_days), chunks):
                out.update(result)
        return out

    def _fetch(self, points: list[LatLng], forecast_days: int) -> dict[LatLng, HourlySeries]:
        params = {
            "latitude": ",".join(f"{p[0]:.4f}" for p in points),
            "longitude": ",".join(f"{p[1]:.4f}" for p in points),
            "hourly": ",".join(HOURLY),
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "temperature_unit": "fahrenheit",
            "timezone": "GMT",
            "timeformat": "unixtime",
            "forecast_days": str(max(1, min(16, forecast_days))),
        }
        if self.api_key:
            params["apikey"] = self.api_key
        data = get_json(self.http, self.base_url, params, service="Open-Meteo")
        items = data if isinstance(data, list) else [data]
        if len(items) != len(points):
            raise ProviderError(
                "weather_unavailable", "Weather service returned an unexpected number of locations.", 502
            )
        return {p: parse_series(item) for p, item in zip(points, items)}


def parse_series(item: dict) -> HourlySeries:
    hourly = item.get("hourly") or {}
    units = item.get("hourly_units") or {}
    times = hourly.get("time") or []
    if not times:
        raise ProviderError("weather_unavailable", "Weather service returned no hourly data.", 502)
    values: dict[str, list[float | None]] = {}
    for src, dst in HOURLY.items():
        raw = hourly.get(src) or [None] * len(times)
        factor = 1.0
        if dst in UNIT_CONVERSIONS:
            unit = units.get(src)
            factor = UNIT_CONVERSIONS[dst].get(unit, 1.0) if unit else 1.0
            if unit and unit not in UNIT_CONVERSIONS[dst]:
                log.warning("Unexpected Open-Meteo unit %r for %s", unit, src)
        values[dst] = [None if v is None else float(v) * factor for v in raw]
    start = datetime.fromtimestamp(int(times[0]), tz=UTC)
    return HourlySeries(start, values)
