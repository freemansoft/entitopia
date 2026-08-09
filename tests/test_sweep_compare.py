"""Metric arithmetic for comparing two sweeps.

Exists so the part that decides whether a re-sweep got better or worse is
testable without a cluster. A banding or sign error here would otherwise only
ever surface as a comparison table that looks reasonable and licenses a bad
change, which is the exact failure this harness is meant to catch.
"""

import pytest

from utils.sweep_compare import compare


def test_compare_flags_a_must_not_fall_metric_that_fell():
    deltas = compare({"vin_only": 675}, {"vin_only": 600}, {"vin_only": "must_not_fall"})
    assert deltas[0].ok is False
    assert deltas[0].delta == -75


def test_compare_accepts_a_must_not_fall_metric_that_rose():
    deltas = compare({"vin_only": 675}, {"vin_only": 700}, {"vin_only": "must_not_fall"})
    assert deltas[0].ok is True


def test_compare_treats_informational_metrics_as_always_ok():
    deltas = compare({"pairs": 421846}, {"pairs": 100}, {"pairs": "informational"})
    assert deltas[0].ok is True


def test_compare_tolerance_expectation_allows_a_bounded_fall():
    deltas = compare(
        {"predecessors_with_pairs": 1000},
        {"predecessors_with_pairs": 960},
        {"predecessors_with_pairs": "within_10pct"},
    )
    assert deltas[0].ok is True


def test_compare_tolerance_expectation_rejects_an_unbounded_fall():
    deltas = compare(
        {"predecessors_with_pairs": 1000},
        {"predecessors_with_pairs": 800},
        {"predecessors_with_pairs": "within_10pct"},
    )
    assert deltas[0].ok is False


def test_compare_rejects_an_unknown_expectation_rather_than_passing_it():
    # A typo in an expectation name must not silently become "no opinion",
    # which would let a regression through wearing a green check.
    with pytest.raises(ValueError, match="unknown expectation"):
        compare({"pairs": 1}, {"pairs": 1}, {"pairs": "must_not_wobble"})


def test_compare_rejects_a_metric_missing_from_the_baseline():
    with pytest.raises(KeyError):
        compare({}, {"pairs": 1}, {"pairs": "informational"})
