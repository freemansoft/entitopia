"""Metric arithmetic for comparing two sweeps.

Exists so the part that decides whether a re-sweep got better or worse is
testable without a cluster. A banding or sign error here would otherwise only
ever surface as a comparison table that looks reasonable and licenses a bad
change, which is the exact failure this harness is meant to catch.
"""

import pytest

from utils.sweep_compare import compare, signal_type_drift


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


def test_signal_type_drift_reports_both_names_of_a_rename():
    # The 2026.08.13 -> 2026.08.17 shape: one signal renamed, the rest stable.
    drift = signal_type_drift({"vin-overlap", "address"}, {"shared-token", "address"})
    assert drift == {"vin-overlap", "shared-token"}


def test_signal_type_drift_is_empty_when_vocabularies_match():
    assert signal_type_drift(["temporal", "address"], ["address", "temporal"]) == frozenset()


def test_within_10pct_accepts_any_rise_because_it_guards_falls_only():
    # Pinned deliberately: within_10pct is a recall tripwire, and a rise is
    # what the precision work wants. Making it symmetric would re-judge every
    # committed expect-*.json.
    deltas = compare({"vin_only": 100}, {"vin_only": 500}, {"vin_only": "within_10pct"})
    assert deltas[0].ok is True


def test_stable_within_10pct_rejects_a_large_rise():
    deltas = compare({"facilities_with_pairs": 1000}, {"facilities_with_pairs": 1200},
                     {"facilities_with_pairs": "stable_within_10pct"})
    assert deltas[0].ok is False


def test_stable_within_10pct_accepts_a_small_move_either_way():
    for after in (950, 1050):
        deltas = compare({"facilities_with_pairs": 1000}, {"facilities_with_pairs": after},
                         {"facilities_with_pairs": "stable_within_10pct"})
        assert deltas[0].ok is True


def test_stable_within_10pct_rejects_movement_off_a_zero_baseline():
    # pct is undefined at a zero baseline; treating that as "ok" is exactly
    # how a renamed signal's 0 -> 208 passed a must_not_fall guard.
    deltas = compare({"vin_only": 0}, {"vin_only": 208}, {"vin_only": "stable_within_10pct"})
    assert deltas[0].ok is False
    deltas = compare({"vin_only": 0}, {"vin_only": 0}, {"vin_only": "stable_within_10pct"})
    assert deltas[0].ok is True
