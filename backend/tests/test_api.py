"""API and planner integration tests with deterministic fake providers."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import flexpolyline
import pytest
from django.core.cache import cache
from django.test import Client

from apps.planner.domain.weather import HourlySeries
from apps.planner.providers import registry
from apps.planner.providers.demo import DemoGeo, DemoRouter
from apps.planner.providers.here import HereClient, _vertex_times
from apps.planner.providers.open_meteo import parse_series

DENVER = {"lat": 39.739, "lng": -104.99, "label": "Denver, CO"}
OMAHA = {"lat": 41.257, "lng": -95.935, "label": "Omaha, NE"}


class FieldWeather:
    """Uniform weather defined by a function of hours-from-now."""

    def __init__(self, fn):
        self.fn = fn

    def hourly(self, points, forecast_days):
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        now = datetime.now(UTC)
        hours = forecast_days * 24
        out = {}
        for p in points:
            rows = [self.fn((start + timedelta(hours=h) - now).total_seconds() / 3600) for h in range(hours)]
            keys = ("wind_mph", "gust_mph", "rain_in", "snow_in", "weather_code", "visibility_m", "temp_f")
            out[p] = HourlySeries(start, {k: [r.get(k, 0.0) for r in rows] for k in keys})
        return out


@pytest.fixture
def use_weather(monkeypatch, settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()

    def apply(fn):
        p = registry.Providers("demo", DemoRouter(), DemoGeo(), FieldWeather(fn))
        monkeypatch.setattr(registry, "get_providers", lambda: p)
        import apps.planner.api.views as v
        import apps.planner.services.heatmap as hm
        import apps.planner.services.planner as pl

        for mod in (v, hm, pl):
            monkeypatch.setattr(mod, "get_providers", lambda: p)
        return p

    return apply


def post_plan(**overrides):
    body = {"origin": DENVER, "destination": OMAHA, "load_lb": 35_000, "interval_mi": 25}
    body.update(overrides)
    return Client(HTTP_HOST="localhost").post("/api/plan", data=json.dumps(body), content_type="application/json")


CALM = lambda h: {"wind_mph": 8.0, "gust_mph": 12.0, "visibility_m": 20000.0}  # noqa: E731


class TestPlan:
    def test_three_routes_and_go_status_in_calm_weather(self, use_weather):
        use_weather(CALM)
        r = post_plan()
        assert r.status_code == 200
        d = r.json()
        assert len(d["routes"]) == 3
        assert {rt["source"] for rt in d["routes"]} == {"provider", "via"}  # fallback filled the gap
        rec = d["recommendation"]
        assert rec["status"] == "go"
        # Clear day: identical risk, so the fastest route wins.
        fastest = min(d["routes"], key=lambda rt: rt["elapsed_s"])["id"]
        assert rec["route_id"] == fastest
        assert all(x["criterion"] == "time" for x in rec["reasons"])
        for rt in d["routes"]:
            total = sum(rt["summary"]["miles_by_level"].values()) + rt["summary"]["unknown_mi"]
            assert total == pytest.approx(rt["distance_mi"], abs=0.5)
            assert rt["checkpoints"][0]["mile"] == 0 and rt["checkpoints"][-1]["mile"] == rt["distance_mi"]
            assert rt["checkpoints"][0]["tz"] == "America/Denver"
            assert rt["checkpoints"][-1]["tz"] == "America/Chicago"

    def test_no_travel_everywhere_says_dont_depart(self, use_weather):
        use_weather(lambda h: {"wind_mph": 60.0, "gust_mph": 75.0, "visibility_m": 20000.0})
        d = post_plan().json()
        assert d["recommendation"]["status"] == "no_travel"
        assert d["recommendation"]["headline"].startswith("Don't depart")
        assert d["leave_later"] is None

    def test_leave_later_found_when_storm_passes(self, use_weather):
        use_weather(lambda h: {"wind_mph": 50.0 if h < 14 else 8.0, "visibility_m": 20000.0})
        d = post_plan().json()
        assert d["recommendation"]["status"] == "no_travel"  # 50 mph + 35k lb
        ll = d["leave_later"]
        assert ll is not None and ll["summary"]["miles_by_level"]["no_travel"] == 0
        assert 8 <= ll["offset_h"] <= 16

    def test_load_rules_reach_the_plan(self, use_weather):
        use_weather(lambda h: {"wind_mph": 40.0, "visibility_m": 20000.0})
        heavy = post_plan(load_lb=45_000).json()["routes"][0]["summary"]["miles_by_level"]
        light = post_plan(load_lb=20_000).json()["routes"][0]["summary"]["miles_by_level"]
        assert heavy["severe"] > 0 and heavy["high"] == 0
        assert light["high"] > 0 and light["severe"] == 0

    def test_hos_toggle_changes_elapsed(self, use_weather):
        use_weather(CALM)
        long_trip = {"lat": 41.878, "lng": -87.63, "label": "Chicago"}
        with_hos = post_plan(destination=long_trip).json()["routes"][0]
        without = post_plan(destination=long_trip, hos_enabled=False).json()["routes"][0]
        assert with_hos["stops"] and not without["stops"]
        assert with_hos["elapsed_s"] > without["elapsed_s"] + 10 * 3600

    def test_departure_is_local_time_at_origin(self, use_weather):
        use_weather(CALM)
        local = (datetime.now(ZoneInfo("America/Denver")) + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        d = post_plan(departure_local=local.strftime("%Y-%m-%dT%H:%M")).json()
        dep = datetime.strptime(d["inputs"]["departure_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert dep == local.astimezone(UTC)

    def test_past_departure_uses_now_with_warning(self, use_weather):
        use_weather(CALM)
        d = post_plan(departure_local="2020-01-01T08:00").json()
        assert any("past" in w for w in d["warnings"])

    def test_far_future_departure_rejected(self, use_weather):
        use_weather(CALM)
        far = (datetime.now(UTC) + timedelta(days=9)).strftime("%Y-%m-%dT%H:%M")
        r = post_plan(departure_local=far)
        assert r.status_code == 422 and r.json()["error"]["code"] == "departure_too_far"

    def test_overweight_warning(self, use_weather):
        use_weather(CALM)
        assert any("permit" in w for w in post_plan(load_lb=50_000).json()["warnings"])

    def test_heatmap(self, use_weather):
        use_weather(lambda h: {"wind_mph": 30.0 if h < 24 else 10.0, "visibility_m": 20000.0})
        d = post_plan().json()
        h = Client(HTTP_HOST="localhost").get(f"/api/plan/{d['plan_id']}/heatmap").json()
        assert h["hours"] == 49 and h["cells"]
        cell = h["cells"][0]
        assert all(len(s) == 49 for s in cell[1:])
        assert cell[1][0] == "1" and cell[1][-1] == "0"  # Moderate now, Low at +48h
        assert len(h["cells"]) <= 900


class TestValidation:
    @pytest.mark.parametrize(
        "override, field",
        [
            ({"load_lb": 0}, "load_lb"),
            ({"load_lb": 500_000}, "load_lb"),
            ({"interval_mi": 15}, "interval_mi"),
            ({"destination": DENVER}, "destination"),
            ({"origin": {"lat": 200, "lng": 0}}, "origin"),
            ({"departure_local": "tomorrow"}, "departure_local"),
        ],
    )
    def test_bad_input_returns_field_errors(self, use_weather, override, field):
        use_weather(CALM)
        r = post_plan(**override)
        assert r.status_code == 400
        assert field in r.json()["error"]["fields"]

    def test_expired_plan_heatmap_404(self, use_weather):
        use_weather(CALM)
        assert Client(HTTP_HOST="localhost").get("/api/plan/nope/heatmap").status_code == 404

    def test_geocode(self, use_weather):
        use_weather(CALM)
        r = Client(HTTP_HOST="localhost").get("/api/geocode", {"q": "den"}).json()
        assert r["results"][0]["label"] == "Denver, CO"


# --- Live provider parsing (no network) ------------------------------------------


def test_here_route_parsing_with_spans():
    pts = [(40.0, -100.0), (40.0, -99.5), (40.0, -99.0), (40.0, -98.0)]
    route = {
        "sections": [
            {
                "polyline": flexpolyline.encode(pts),
                "summary": {"duration": 7200},
                "spans": [{"offset": 0, "duration": 1800}, {"offset": 2, "duration": 5400}],
            }
        ]
    }
    raw = HereClient._parse_route(route)
    assert raw.coords[0] == pytest.approx((40.0, -100.0))
    assert raw.vertex_drive_s[2] == pytest.approx(1800, abs=1)
    assert raw.vertex_drive_s[-1] == pytest.approx(7200, abs=1)


def test_here_multi_section_routes_are_joined():
    a = [(40.0, -100.0), (40.0, -99.0)]
    b = [(40.0, -99.0), (40.0, -98.0)]
    route = {
        "sections": [
            {"polyline": flexpolyline.encode(a), "summary": {"duration": 3600}, "type": "vehicle"},
            {"polyline": flexpolyline.encode(b), "summary": {"duration": 3600}, "type": "ferry"},
        ]
    }
    raw = HereClient._parse_route(route)
    assert len(raw.coords) == 3 and raw.vertex_drive_s == pytest.approx([0, 3600, 7200], abs=1)
    assert "Route includes a ferry crossing." in raw.notices


def test_vertex_times_without_spans_spread_by_distance():
    times = _vertex_times([(40, -100), (40, -99), (40, -97)], [], 3000)
    assert times == pytest.approx([0, 1000, 3000], rel=1e-3)


def test_open_meteo_units_are_normalised():
    item = {
        "hourly_units": {"wind_speed_10m": "km/h", "snowfall": "cm", "rain": "inch", "visibility": "m"},
        "hourly": {
            "time": [1762214400, 1762218000],
            "wind_speed_10m": [100.0, None],
            "wind_gusts_10m": [0, 0],
            "rain": [0.2, 0.0],
            "snowfall": [2.54, 0.0],
            "weather_code": [0, 0],
            "visibility": [1000, 1000],
            "temperature_2m": [30, 31],
        },
    }
    s = parse_series(item)
    assert s.values["wind_mph"][0] == pytest.approx(62.1371)
    assert s.values["wind_mph"][1] is None
    assert s.values["snow_in"][0] == pytest.approx(1.0)
    assert s.start == datetime(2025, 11, 4, tzinfo=UTC)
