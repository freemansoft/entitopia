"""Tests for the crash-outcome validation arithmetic.

These cover the parts that decide what a number MEANS — which band a score
falls in, whether a crash postdates registration, how strata are reweighted.
A defect in any of them produces a plausible-looking table rather than an
error, which is the failure mode this repo keeps hitting.
"""

import itertools

from utils.crash_lift import (
    SCORE_BANDS,
    band_for,
    crashed_after_registration,
    fleet_size_band,
    months_between,
    rate,
    to_yyyymmdd,
)


def test_band_edges_are_half_open_so_no_score_lands_in_two_bands():
    assert band_for(0.70) == "0.70-0.80"
    assert band_for(0.6999) == "0.60-0.70"


def test_perfect_score_lands_in_the_top_band_rather_than_falling_off_the_end():
    # The top band is the only closed interval; 1.0 is attainable and must not
    # silently drop out of the denominator.
    assert band_for(1.0) == "0.90-1.00"


def test_scores_below_the_emit_floor_have_no_band():
    # The sweep cannot emit below 0.35, so a lower value means the caller is
    # passing something that is not a pair score. Returning None makes that
    # visible instead of inventing a bucket for it.
    assert band_for(0.30) is None


def test_bands_are_contiguous_and_ordered():
    for (_, upper, _), (lower, _, _) in itertools.pairwise(SCORE_BANDS):
        assert upper == lower


def test_fleet_size_bands_group_the_long_tail():
    assert fleet_size_band(1) == "1"
    assert fleet_size_band(5) == "2-5"
    assert fleet_size_band(20) == "6-20"
    assert fleet_size_band(101) == "100+"


def test_missing_fleet_size_is_its_own_band_not_zero():
    # A carrier that never filed a power-unit count is not a carrier with zero
    # trucks. Folding it into "1" would move real carriers between strata.
    assert fleet_size_band(None) == "unknown"


def test_add_date_becomes_an_integer_comparable_to_report_date():
    # report_date is a long in YYYYMMDD form, so comparison happens in that
    # space rather than by parsing report_date into a date.
    assert to_yyyymmdd("2014-05-29") == 20140529


def test_add_date_with_a_time_component_still_coerces():
    assert to_yyyymmdd("2014-05-29T00:00:00Z") == 20140529


def test_missing_add_date_is_none_so_the_carrier_can_be_excluded():
    assert to_yyyymmdd(None) is None
    assert to_yyyymmdd("") is None


def test_a_crash_before_registration_does_not_count():
    # The whole causal claim rests on this. A crash the predecessor had before
    # the successor existed says nothing about the successor.
    assert crashed_after_registration(20250101, [20241201]) is False


def test_a_crash_after_registration_counts():
    assert crashed_after_registration(20250101, [20250102]) is True


def test_a_crash_on_the_registration_date_does_not_count():
    # Strictly after. Same-day is ambiguous and rare; excluding it is the
    # conservative direction, biasing against finding an effect.
    assert crashed_after_registration(20250101, [20250101]) is False


def test_any_qualifying_crash_is_enough():
    assert crashed_after_registration(20250101, [20240101, 20250601]) is True


def test_a_carrier_with_no_registration_date_never_counts():
    # Cannot establish the crash postdates registration, so it is excluded
    # rather than assumed.
    assert crashed_after_registration(None, [20250601]) is False


def test_months_between_is_fractional_so_short_exposure_is_not_rounded_away():
    # 181 days / 30.4375 = ~5.95 months (Jan 1 to July 1)
    assert round(months_between(20250101, 20250701), 1) == 5.9
    assert round(months_between(20250101, 20250116), 1) == 0.5


def test_rate_of_an_empty_band_is_none_not_zero():
    # None means "no carriers in this band"; 0.0 means "carriers, none crashed".
    # Printing 0.0% for an empty band invents a measurement.
    assert rate(0, 0) is None
    assert rate(0, 10) == 0.0


def test_rate_is_a_proportion():
    assert rate(3, 12) == 0.25
