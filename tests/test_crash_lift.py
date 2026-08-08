"""Tests for the crash-outcome validation arithmetic.

These cover the parts that decide what a number MEANS — which band a score
falls in, whether a crash postdates registration, how strata are reweighted.
A defect in any of them produces a plausible-looking table rather than an
error, which is the failure mode this repo keeps hitting.
"""

import importlib.util
import itertools
from pathlib import Path

import pytest
from elasticsearch import Elasticsearch

from utils.crash_lift import (
    SCORE_BANDS,
    band_for,
    crashed_after_registration,
    fleet_size_band,
    gap_band,
    months_between,
    rate,
    recency_cohort,
    standardize,
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


def test_months_between_approximates_calendar_months():
    # Jan 1 to Jul 1 is six calendar months; 30.4375-day average months put it
    # at ~5.95. The tolerance is what makes this a test of intent rather than
    # of the divisor — it still fails if the function returns days, weeks, or
    # divides by 365.
    assert 5.8 < months_between(20250101, 20250701) < 6.1


def test_months_between_is_fractional_so_short_exposure_is_not_rounded_away():
    assert round(months_between(20250101, 20250116), 1) == 0.5


def test_months_between_is_zero_when_the_dates_are_equal():
    # A carrier registered on the window end has no exposure. The caller
    # divides by this, so it must be exactly 0.0 rather than a small negative.
    assert months_between(20250101, 20250101) == 0.0


def test_recency_cohorts_are_measured_back_from_the_crash_window_end():
    # Boundaries are months before the newest crash in the data, not before
    # today, so the cohorts stay stable as the fetch window rolls forward.
    assert recency_cohort(20260101, 20260301) == "under-1y"
    assert recency_cohort(20240101, 20260301) == "1-3y"
    assert recency_cohort(20200101, 20260301) == "3y-plus"


def test_recency_cohort_boundaries_are_half_open():
    # Exactly 12 months back belongs to the older cohort, so no carrier lands
    # in two columns and the columns sum to the population. "12 months back"
    # is measured through months_between's existing 30.4375-day-average
    # divisor (Tasks 1-5), not a calendar year: a non-leap calendar year is
    # 365 days = 11.99 of those months, one day short of the boundary, so the
    # probe date here is 2025-02-28 (366 days before window_end) rather than
    # 2025-03-01.
    assert recency_cohort(20250228, 20260301) == "1-3y"
    assert recency_cohort(20230301, 20260301) == "3y-plus"


def test_recency_cohort_is_none_without_a_registration_date():
    # Same rule as everywhere else here: absent input is excluded, never
    # guessed into a bucket.
    assert recency_cohort(None, 20260301) is None


def test_rate_of_an_empty_band_is_none_not_zero():
    # None means "no carriers in this band"; 0.0 means "carriers, none crashed".
    # Printing 0.0% for an empty band invents a measurement.
    assert rate(0, 0) is None
    assert rate(0, 10) == 0.0


def test_rate_is_a_proportion():
    assert rate(3, 12) == 0.25


def test_standardized_rate_reweights_controls_to_the_flagged_mix():
    # Controls are 50/50 across strata; flagged are 90/10. Standardizing must
    # answer "what would the control rate be if controls had the flagged
    # population's mix", which is 0.9*0.10 + 0.1*0.50 = 0.14 — NOT the crude
    # control rate of 0.30. Getting this backwards is the whole reason the
    # comparison exists.
    flagged = {"a": (0, 90), "b": (0, 10)}
    control = {"a": (10, 100), "b": (50, 100)}
    standardized, skipped = standardize(flagged, control)
    assert round(standardized, 4) == 0.14
    assert skipped == []


def test_strata_with_no_controls_are_reported_not_silently_dropped():
    # Dropping them quietly redefines the comparison population, which would
    # make the lift describe a different set of carriers than the headline.
    flagged = {"a": (0, 50), "orphan": (0, 50)}
    control = {"a": (10, 100)}
    standardized, skipped = standardize(flagged, control)
    assert standardized == 0.10
    assert skipped == ["orphan"]


def test_no_overlapping_strata_gives_none_rather_than_zero():
    standardized, skipped = standardize({"a": (0, 10)}, {"b": (5, 10)})
    assert standardized is None
    assert skipped == ["a"]


def test_empty_flagged_population_gives_none():
    assert standardize({}, {"a": (5, 10)}) == (None, [])


def test_standardize_tolerates_a_none_valued_stratum_element():
    # Found against live data, not hypothesized: a real stratum is
    # (cohort_int, fleet_band, state), and `state` is None for any carrier
    # that never filed phy_state. sorted() on the raw tuples raised
    # TypeError: '<' not supported between instances of 'NoneType' and 'str'
    # the first time a None-state stratum tied a real-state stratum on the
    # other two elements — Python can order two strings or two Nones, but not
    # one of each. The two strata below share cohort=2020 so they collide on
    # exactly that comparison.
    flagged = {(2020, "1", None): (1, 10), (2020, "1", "TX"): (2, 10)}
    control = {(2020, "1", None): (5, 100), (2020, "1", "TX"): (5, 100)}
    standardized, skipped = standardize(flagged, control)
    assert round(standardized, 4) == 0.05
    assert skipped == []


def test_gap_bands_split_on_the_shutdown_date():
    # The sign of gap_days is the whole point: negative means the successor
    # already existed when the predecessor was shut down, so it cannot be that
    # predecessor reincarnated.
    assert gap_band(-1200) == "3y+ before"
    assert gap_band(-30) == "0-3y before"
    assert gap_band(200) == "under 1y after"
    assert gap_band(500) == "1y+ after"


def test_gap_band_zero_is_after_not_before():
    # Same-day re-registration is the strongest chameleon shape there is, so it
    # must not fall into a "before" bucket.
    assert gap_band(0) == "under 1y after"


def test_gap_band_is_none_when_the_gap_is_unknown():
    assert gap_band(None) is None


# Loaded by path, mirroring test_profile_dataset.py's convention, because
# scripts/ is a directory of standalone tools rather than an importable
# package. A function-scoped `sys.path.insert` + import (as originally
# sketched) trips ruff's PLC0415, which this repo does not exempt for new
# code — this sidesteps that without mutating sys.path at all.
_MEASURE_CRASH_LIFT = Path(__file__).parent.parent / "scripts" / "measure_crash_lift.py"


def _load_measure_crash_lift():
    spec = importlib.util.spec_from_file_location("measure_crash_lift", _MEASURE_CRASH_LIFT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measure_crash_lift = _load_measure_crash_lift()


@pytest.fixture
def live_client():
    """Real cluster, skipped when unreachable.

    The pure functions above assert what we compute; only this asserts the
    queries retrieve what we think. The dot_number type mismatch between
    indexes (long on crashes, keyword elsewhere) is invisible to a unit test.
    """
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=120
    )
    try:
        reachable = client.ping()
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("Elasticsearch is not reachable on localhost:9200")
    return client


def test_crash_dates_join_across_the_dot_number_type_mismatch(live_client):
    scores = measure_crash_lift.successor_scores(
        live_client, "chameleon-candidates-000001", limit=500
    )
    assert scores, "no successors read from the candidates index"

    found = measure_crash_lift.crash_dates(live_client, "crashes-000001", list(scores))
    # Not every successor crashed, but across 500 the intersection must not be
    # empty — an empty result here is the signature of the keyword/long
    # mismatch silently intersecting to nothing.
    assert found, "crash join returned nothing; check dot_number str/int coercion"
    for dates in found.values():
        assert all(isinstance(d, int) for d in dates)
    # Elasticsearch coerces numeric-looking terms in a `terms` query either
    # way, so a non-empty `found` above proves the QUERY worked but not that
    # the two sides agree on key TYPE — a crash_dates that forgot str() would
    # still retrieve rows, just keyed by int, and every assertion above this
    # one would still pass. This is the one that actually depends on both
    # sides being normalized: found's keys can only be a subset of scores'
    # keys if both are the same type, since int("123") != "123".
    assert set(found) <= set(scores), (
        "crash keys are not a subset of successor keys; check str() normalization on both sides"
    )
