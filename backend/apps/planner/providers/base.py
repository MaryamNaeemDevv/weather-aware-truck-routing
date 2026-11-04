"""Provider interfaces. The planner depends only on these, so live APIs and the
deterministic demo providers are interchangeable (and testable)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from ..domain.weather import HourlySeries

log = logging.getLogger(__name__)

LatLng = tuple[float, float]


class ProviderError(Exception):
    """A third-party call failed in a way the user should hear about."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class Place:
    lat: float
    lng: float
    label: str


@dataclass
class RawRoute:
    coords: list[LatLng]
    vertex_drive_s: list[float]
    notices: list[str] = field(default_factory=list)
    source: str = "provider"  # "provider" | "via"


class RoutingProvider(Protocol):
    def routes(
        self,
        origin: LatLng,
        destination: LatLng,
        departure: datetime,
        gross_weight_lb: float,
        alternatives: int = 2,
        via: LatLng | None = None,
    ) -> list[RawRoute]: ...


class GeocodingProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[Place]: ...

    def reverse(self, lat: float, lng: float) -> str | None: ...


class WeatherProvider(Protocol):
    def hourly(self, points: list[LatLng], forecast_days: int) -> dict[LatLng, HourlySeries]:
        """Hourly series keyed by the exact (lat, lng) tuples passed in."""
        ...


def get_json(
    client: httpx.Client,
    url: str,
    params: dict | list,
    *,
    service: str,
    retries: int = 2,
) -> dict | list:
    """GET with retry/backoff on 429 and 5xx; maps failures to ProviderError."""
    slug = service.lower().replace(" ", "_").replace("-", "_")
    delay = 0.6
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            last_exc = exc
            log.warning("%s timeout (attempt %s)", service, attempt + 1)
        except httpx.HTTPError as exc:
            last_exc = exc
            log.warning("%s network error: %s", service, exc)
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403):
                raise ProviderError(
                    f"{slug}_auth", f"{service} rejected the API key (HTTP {resp.status_code}).", 503
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = ProviderError(
                    f"{slug}_unavailable",
                    f"{service} is rate-limited or unavailable (HTTP {resp.status_code}).",
                    503,
                )
            else:
                detail = _error_detail(resp)
                raise ProviderError(f"{slug}_bad_request", f"{service} could not handle the request: {detail}", 422)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    if isinstance(last_exc, ProviderError):
        raise last_exc
    raise ProviderError(f"{slug}_unavailable", f"{service} did not respond. Please try again.", 503)


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("title") or body.get("reason") or body.get("message") or body)[:200]
    return str(body)[:200]
