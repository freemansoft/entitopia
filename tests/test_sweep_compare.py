"""Metric arithmetic for comparing two sweeps.

Exists so the part that decides whether a re-sweep got better or worse is
testable without a cluster. A banding or sign error here would otherwise only
ever surface as a comparison table that looks reasonable and licenses a bad
change, which is the exact failure this harness is meant to catch.
"""

import pytest

from utils.sweep_compare import METRICS, compare, summarize


def pair(score=0.8, gap=10, matched_on=None, pred="1", succ="2",
         pred_name="ALPHA", succ_name="BETA"):
    return {
        "total_score": score,
        "gap_days": gap,
        "matched_on": matched_on if matched_on is not None else ["name-phonetic"],
        "predecessor": {"dot_number": pred, "legal_name": pred_name},
        "successor": {"dot_number": succ, "legal_name": succ_name},
    }


def test_summarize_reports_every_declared_metric():
    result = summarize([pair()])
    assert set(result) == set(METRICS)


def test_pairs_counts_every_row():
    assert summarize([pair(), pair(succ="3")])["pairs"] == 2


def test_coherent_window_is_inclusive_at_both_edges():
    # -180 is BACKWARD_WINDOW_DAYS, 365 is the temporal signal's max_gap_days.
    # A pair exactly on either edge is inside the model's own claim.
    rows = [pair(gap=-180), pair(gap=365, succ="3"), pair(gap=-181, succ="4"),
            pair(gap=366, succ="5")]
    assert summarize(rows)["coherent_ge_070"] == 2


def test_coherent_ignores_pairs_below_the_triage_threshold():
    assert summarize([pair(score=0.69, gap=10)])["coherent_ge_070"] == 0


def test_coherent_share_is_zero_not_error_when_no_pair_reaches_threshold():
    # None would propagate into the diff as an unorderable value; 0.0 keeps a
    # sweep that emitted nothing comparable against one that did.
    assert summarize([pair(score=0.4)])["coherent_share_ge_070"] == 0.0


def test_gap_days_none_is_not_coherent():
    # A pair with an unparseable date on either side cannot be judged
    # temporally, and counting it as coherent would inflate the goal metric.
    assert summarize([pair(gap=None)])["coherent_ge_070"] == 0


def test_vin_only_requires_vin_to_be_the_sole_evidence():
    rows = [
        pair(matched_on=["vin-overlap"]),
        pair(matched_on=["vin-overlap", "address"], succ="3"),
    ]
    assert summarize(rows)["vin_only"] == 1


def test_vin_only_identity_tolerates_corroborating_signals():
    # A pair carrying temporal or agent alongside the VIN is still reachable
    # only because vin-overlap is conclusive — neither can lift it over the
    # 0.35 floor. The strict metric excludes it; this one does not, and the
    # two disagreed by 156 pairs on the baseline index.
    rows = [
        pair(matched_on=["vin-overlap", "temporal"]),
        pair(matched_on=["vin-overlap", "address"], succ="3"),
    ]
    result = summarize(rows)
    assert result["vin_only"] == 0
    assert result["vin_only_identity"] == 1


def test_triage_unbounded_admits_pre_shutdown_pairs():
    # The 906-style filter as actually run: bounded above only.
    rows = [pair(score=0.7, gap=-2000, matched_on=["vin-overlap"])]
    assert summarize(rows)["triage_unbounded"] == 1
    assert summarize(rows)["triage_bounded"] == 0


def test_triage_requires_a_corroborating_identifier():
    rows = [pair(score=0.9, gap=10, matched_on=["name-phonetic", "address"])]
    assert summarize(rows)["triage_unbounded"] == 0


def test_identical_name_triage_compares_exact_bytes():
    rows = [
        pair(score=0.9, gap=1, matched_on=["exact-identifier"],
             pred_name="ALPHA", succ_name="ALPHA"),
        pair(score=0.9, gap=1, matched_on=["exact-identifier"], succ="3",
             pred_name="ALPHA", succ_name="ALPHA CO"),
    ]
    assert summarize(rows)["identical_name_triage"] == 1


def test_canary_counts_near_perfect_immediate_renames():
    # The README's sanity anchor: a byte-identical legal name re-registering
    # within days of shutdown at ~0.9998. If a config change stops surfacing
    # this shape, the change is wrong.
    rows = [pair(score=0.9998, gap=1, matched_on=["exact-identifier"],
                 pred_name="ALPHA", succ_name="ALPHA")]
    assert summarize(rows)["canary"] == 1


def test_predecessors_with_pairs_deduplicates():
    rows = [pair(pred="1", succ="2"), pair(pred="1", succ="3")]
    assert summarize(rows)["predecessors_with_pairs"] == 1


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
