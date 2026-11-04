"""Tests for sampling, ETA/HOS timeline, weather timing and recommendation ranking."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.planner.domain import geo, hos
from apps.planner.domain.risk import Level
from apps.planner.domain.scoring import (
    RouteScore,
    compare,
    evaluate_checkpoints,
    rank,
    score_route,
    worst_stretch,
)
from apps.planner.domain.weather import HourlySeries, grid_key

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def straight_line(miles: float, drive_s: float | None = None) -> geo.MeasuredLine:
    end = geo.offset_point((40.0, -100.0), 90, miles)
    return geo.measure([(40.0, -100.0), end], total_drive_s=drive_s or miles / 60 * 3600)


# --- Sampling ------------------------------------------------------------------


class TestSampling:
    def test_includes_origin_and_destination(self):
        assert geo.sample_miles(100, 25) == [0, 25, 50, 75, 100]

    def test_non_multiple_length_has_short_final_segment(self):
        assert geo.sample_miles(110, 25) == [0, 25, 50, 75, 100, 110]

    def test_interval_longer_than_route(self):
        assert geo.sample_miles(7, 50) == [0, 7]

    def test_tiny_stub_merges_into_destination(self):
        marks = geo.sample_miles(100.1, 25)
        assert marks[-1] == 100.1 and 100 not in marks and len(marks) == 5

    def test_zero_length(self):
        assert geo.sample_miles(0, 10) == [0.0]

    @pytest.mark.parametrize("length, interval", [(100, 25), (110, 25), (7, 50), (1234.5, 10)])
    def test_owned_miles_sum_to_route_length(self, length, interval):
        marks = geo.sample_miles(length, interval)
        assert sum(geo.owned_miles(marks, length)) == pytest.approx(length)

    def test_midpoint_attribution(self):
        assert geo.owned_miles([0, 25, 50], 50) == [12.5, 25, 12.5]


class TestMeasuredLine:
    def test_haversine_known_distance(self):
        # New York -> Los Angeles is ~2445 mi great-circle.
        assert geo.haversine_mi((40.7128, -74.0060), (34.0522, -118.2437)) == pytest.approx(2445, rel=0.01)

    def test_drive_time_uses_per_vertex_speeds(self):
        # 2 equal-length segments: first takes 1h, second 3h (slow mountain stretch).
        a, b = (40.0, -100.0), geo.offset_point((40.0, -100.0), 90, 60)
        c = geo.offset_point(b, 90, 60)
        line = geo.measure([a, b, c], vertex_drive_s=[0, 3600, 4 * 3600])
        half = line.length_mi / 2
        assert line.drive_s_at(half) == pytest.approx(3600, rel=1e-3)
        assert line.drive_s_at(half * 1.5) == pytest.approx(2.5 * 3600, rel=1e-3)
        assert line.mile_at_drive_s(2.5 * 3600) == pytest.approx(half * 1.5, rel=1e-3)

    def test_non_monotonic_provider_times_are_clamped(self):
        line = geo.measure([(40, -100), (40, -99), (40, -98)], vertex_drive_s=[0, 100, 90])
        assert line.cum_drive_s == [0, 100, 100]

    def test_overlap_of_identical_routes_is_one(self):
        line = straight_line(200)
        assert geo.overlap_fraction(line, line) == pytest.approx(1.0)

    def test_overlap_of_distant_routes_is_zero(self):
        a = straight_line(200)
        b = geo.measure([(42.0, -100.0), (42.0, -96.0)], total_drive_s=10000)
        assert geo.overlap_fraction(a, b) == 0.0


# --- HOS -----------------------------------------------------------------------

H = 3600


class TestHos:
    def test_short_trip_needs_no_stops(self):
        assert hos.plan_stops(7.9 * H) == []

    def test_exactly_8h_trip_has_no_break_at_arrival(self):
        assert hos.plan_stops(8 * H) == []

    def test_break_after_8h(self):
        stops = hos.plan_stops(10 * H)
        assert [(s.kind, s.at_drive_s) for s in stops] == [("break", 8 * H)]

    def test_rest_after_11h_then_new_shift(self):
        stops = hos.plan_stops(25 * H)
        assert [(s.kind, s.at_drive_s / H) for s in stops] == [
            ("break", 8),
            ("rest", 11),
            ("break", 19),
            ("rest", 22),
        ]

    def test_disabled(self):
        assert hos.plan_stops(30 * H, enabled=False) == []

    def test_elapsed_includes_completed_stops_only(self):
        stops = hos.plan_stops(20 * H)
        assert hos.elapsed_at(8 * H, stops) == 8 * H  # arriving at the break point
        assert hos.elapsed_at(9 * H, stops) == 9.5 * H
        assert hos.elapsed_at(12 * H, stops) == 22.5 * H  # 12h drive + 0.5 + 10

    def test_drive_at_elapsed_inverse(self):
        stops = hos.plan_stops(20 * H)
        assert hos.drive_at_elapsed(8.25 * H, stops, 20 * H) == (8 * H, "break")
        assert hos.drive_at_elapsed(15 * H, stops, 20 * H) == (11 * H, "rest")
        driven, state = hos.drive_at_elapsed(9.5 * H, stops, 20 * H)
        assert driven == pytest.approx(9 * H) and state is None
        assert hos.drive_at_elapsed(999 * H, stops, 20 * H) == (20 * H, None)


# --- Weather timing ------------------------------------------------------------


def series(**values) -> HourlySeries:
    n = len(next(iter(values.values())))
    base = {
        k: [0.0] * n for k in ("wind_mph", "gust_mph", "temp_f", "visibility_m", "rain_in", "snow_in", "weather_code")
    }
    base.update(values)
    return HourlySeries(T0, base)


class TestWeatherTiming:
    def test_wind_interpolates_between_hours(self):
        s = series(wind_mph=[10.0, 30.0, 30.0])
        assert s.at(T0 + timedelta(minutes=30))["wind_mph"] == pytest.approx(20)

    def test_precip_uses_hour_covering_the_eta(self):
        # Label 13:00 = rain during 12:00-13:00; label 14:00 = 13:00-14:00.
        s = series(rain_in=[0.0, 0.05, 0.6, 0.0])
        assert s.at(T0 + timedelta(minutes=30))["rain_in"] == 0.05
        assert s.at(T0 + timedelta(hours=1, minutes=30))["rain_in"] == 0.6
        # Exactly on the hour: the truck is starting the next hour.
        assert s.at(T0 + timedelta(hours=1))["rain_in"] == 0.6

    def test_outside_series_is_unknown_not_zero(self):
        s = series(wind_mph=[10.0, 10.0])
        assert s.at(T0 + timedelta(hours=5))["wind_mph"] is None
        # T0-1h is covered by label T0 (index 0); T0-2h has no covering label.
        assert s.at(T0 - timedelta(hours=1))["rain_in"] == 0.0
        assert s.at(T0 - timedelta(hours=2))["rain_in"] is None

    def test_missing_neighbour_is_unknown(self):
        s = series(wind_mph=[10.0, None, 10.0])
        assert s.at(T0 + timedelta(minutes=30))["wind_mph"] is None
        assert s.at(T0)["wind_mph"] == 10.0

    def test_grid_key_snaps(self):
        assert grid_key(40.04, -100.06) == (40.0, -100.1)


# --- Evaluation & ranking ------------------------------------------------------


def score(nt=0.0, sev=0.0, high=0.0, mod=0.0, low=100.0, unknown=0.0, elapsed=3600.0, dist=100.0):
    by = {Level.NO_TRAVEL: nt, Level.SEVERE: sev, Level.HIGH: high, Level.MODERATE: mod, Level.LOW: low}
    return RouteScore(by, unknown, None, None, elapsed, dist)


class TestRanking:
    TOL = 5.0

    def test_no_travel_gate_beats_severe(self):
        a = score(nt=1, low=99)
        b = score(sev=80, low=20)
        assert compare(a, b, self.TOL).criterion == "no_travel_gate"
        assert rank({"a": a, "b": b}, self.TOL) == ["b", "a"]

    def test_fewest_severe_first(self):
        assert rank({"a": score(sev=40, low=60), "b": score(sev=10, high=90, low=0)}, self.TOL) == ["b", "a"]

    def test_then_high_miles(self):
        assert rank({"a": score(high=30), "b": score(high=10, mod=50)}, self.TOL) == ["b", "a"]

    def test_then_avg_risk(self):
        a = score(mod=80, low=20)
        b = score(mod=10, low=90)
        assert compare(a, b, self.TOL).criterion == "avg_risk"
        assert rank({"a": a, "b": b}, self.TOL) == ["b", "a"]

    def test_clear_day_fastest_wins_within_tolerance(self):
        a = score(mod=3, low=97, elapsed=5 * H)  # avg risk 0.03
        b = score(mod=0, low=100, elapsed=6 * H)
        d = compare(a, b, self.TOL)
        assert d.criterion == "time" and d.sign == -1

    def test_small_mile_differences_are_ties(self):
        a = score(sev=12, low=88, elapsed=5 * H)
        b = score(sev=10, low=90, elapsed=7 * H)
        assert compare(a, b, self.TOL).criterion == "time"

    def test_unknown_miles_count_as_high_for_ranking(self):
        a = score(unknown=50, low=50)
        b = score(mod=50, low=50, elapsed=99 * H)
        assert compare(a, b, self.TOL).criterion == "high_miles"
        assert rank({"a": a, "b": b}, self.TOL) == ["b", "a"]

    def test_full_tie_is_deterministic(self):
        assert rank({"z": score(), "a": score()}, self.TOL) == ["a", "z"]


class TestEvaluate:
    def test_checkpoint_etas_and_levels(self):
        line = straight_line(120, drive_s=2 * H)  # 60 mph
        windy = series(wind_mph=[50.0] * 6, gust_mph=[60.0] * 6)
        cps = evaluate_checkpoints(line, T0, 60, 35_000, [], lambda lat, lng: windy)
        assert [round(c.mile) for c in cps] == [0, 60, 120]
        assert cps[1].eta == T0 + timedelta(hours=1)
        assert all(c.assessment.level == Level.NO_TRAVEL for c in cps)  # 50 mph + >30k lb
        s = score_route(cps, 2 * H, line.length_mi)
        assert s.no_travel_miles == pytest.approx(line.length_mi)

    def test_missing_series_is_unknown(self):
        line = straight_line(50)
        cps = evaluate_checkpoints(line, T0, 25, 10_000, [], lambda lat, lng: None)
        s = score_route(cps, 3600, line.length_mi)
        assert s.unknown_miles == pytest.approx(line.length_mi) and s.max_level is None

    def test_hos_shifts_late_etas(self):
        line = straight_line(600, drive_s=10 * H)
        stops = hos.plan_stops(line.drive_s)
        calm = series(wind_mph=[5.0] * 24)
        cps = evaluate_checkpoints(line, T0, 600, 0, stops, lambda *_: calm)
        assert cps[-1].eta == T0 + timedelta(hours=10.5)

    def test_worst_stretch(self):
        line = straight_line(100, drive_s=2 * H)
        rain = series(rain_in=[0.3] * 6)
        cps = evaluate_checkpoints(line, T0, 25, 0, [], lambda *_: rain)
        ws = worst_stretch(cps)
        assert ws is not None and ws.level == Level.HIGH and ws.factors == ["rain"]
        assert ws.miles == pytest.approx(line.length_mi)

    def test_no_worst_stretch_when_all_low(self):
        line = straight_line(100)
        cps = evaluate_checkpoints(line, T0, 25, 0, [], lambda *_: series(wind_mph=[5.0] * 6))
        assert worst_stretch(cps) is None
