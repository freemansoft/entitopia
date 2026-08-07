"""Tests for the crash-outcome validation arithmetic.

These cover the parts that decide what a number MEANS — which band a score
falls in, whether a crash postdates registration, how strata are reweighted.
A defect in any of them produces a plausible-looking table rather than an
error, which is the failure mode this repo keeps hitting.
"""

import itertools

from utils.crash_lift import SCORE_BANDS, band_for, fleet_size_band, to_yyyymmdd


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
