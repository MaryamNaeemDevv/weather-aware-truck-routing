"""Boundary tests for the spec's risk table and load rules."""

import math

import pytest

from apps.planner.domain.risk import (
    Level,
    advisories,
    apply_load_rules,
    assess,
    classify_rain,
    classify_snow,
    classify_wind,
)

L, M, H, S, NT = Level.LOW, Level.MODERATE, Level.HIGH, Level.SEVERE, Level.NO_TRAVEL


@pytest.mark.parametrize(
    "mph, expected",
    [
        (0, L),
        (24.99, L),
        (25, M),
        (34.99, M),
        (35, H),
        (44.99, H),
        (45, S),
        (54.99, S),
        (55, NT),
        (80, NT),
    ],
)
def test_wind_thresholds(mph, expected):
    assert classify_wind(mph) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0, L),
        (0.099, L),
        (0.10, M),
        (0.249, M),
        (0.25, H),
        (0.499, H),
        (0.50, S),
        (1.00, S),
        (1.001, NT),
        (2.5, NT),
    ],
)
def test_rain_thresholds(rate, expected):
    assert classify_rain(rate) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (0, L),
        (0.49, L),
        (0.5, M),
        (0.99, M),
        (1.0, H),
        (1.99, H),
        (2.0, S),
        (3.0, S),
        (3.01, NT),
    ],
)
def test_snow_thresholds(rate, expected):
    assert classify_snow(rate) == expected


@pytest.mark.parametrize("fn", [classify_wind, classify_rain, classify_snow])
@pytest.mark.parametrize("missing", [None, math.nan])
def test_missing_values_are_unknown(fn, missing):
    assert fn(missing) is None


class TestLoadRules:
    def test_55_mph_is_no_travel_for_any_load(self):
        assert apply_load_rules(NT, 55, 0) == NT
        assert apply_load_rules(NT, 70, 5_000) == NT

    @pytest.mark.parametrize("mph", [45, 50, 54.99])
    def test_45_54_mph_over_30k_is_no_travel(self, mph):
        assert apply_load_rules(S, mph, 30_001) == NT

    @pytest.mark.parametrize("mph", [45, 54.99])
    def test_45_54_mph_at_exactly_30k_is_not_escalated(self, mph):
        assert apply_load_rules(S, mph, 30_000) == S

    @pytest.mark.parametrize("mph", [35, 40, 44.99])
    def test_35_44_mph_over_40k_is_severe(self, mph):
        assert apply_load_rules(H, mph, 40_001) == S

    def test_35_44_mph_at_exactly_40k_is_not_escalated(self):
        assert apply_load_rules(H, 40, 40_000) == H

    def test_35_44_mph_between_30k_and_40k_is_not_escalated(self):
        assert apply_load_rules(H, 44, 35_000) == H

    def test_below_35_mph_never_escalates(self):
        assert apply_load_rules(M, 34.99, 80_000) == M

    def test_load_rules_never_lower_a_level(self):
        assert apply_load_rules(NT, 60, 0) == NT


class TestAssess:
    def test_worst_factor_wins(self):
        a = assess(wind_mph=10, rain_in_hr=0.3, snow_in_hr=1.2, load_lb=10_000)
        assert a.level == H
        assert set(a.drivers) == {"rain", "snow"}

    def test_clear_weather_is_low_with_no_drivers(self):
        a = assess(5, 0, 0, 20_000)
        assert a.level == L and a.drivers == ()

    def test_load_escalation_flagged(self):
        a = assess(wind_mph=47, rain_in_hr=0, snow_in_hr=0, load_lb=35_000)
        assert a.level == NT and a.load_escalated and a.drivers == ("wind",)

    def test_heavy_load_moderate_wind_unchanged(self):
        a = assess(wind_mph=30, rain_in_hr=0, snow_in_hr=0, load_lb=45_000)
        assert a.level == M and not a.load_escalated

    @pytest.mark.parametrize("wind, rain, snow", [(None, 0, 0), (10, None, 0), (10, 0, None), (math.nan, 0, 0)])
    def test_any_missing_core_variable_is_unknown(self, wind, rain, snow):
        a = assess(wind, rain, snow, 10_000)
        assert a.unknown and a.code == -1

    def test_unknown_is_never_low(self):
        assert assess(None, None, None, 0).level is None


def test_advisories_are_separate_from_levels():
    assert advisories(66, 5000, 20, 10) == ["freezing_rain"]
    assert advisories(0, 200, None, None) == ["low_visibility"]
    assert advisories(0, None, 50, 30) == ["strong_gusts"]
    assert advisories(0, 10_000, 30, 25) == []
