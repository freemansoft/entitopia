"""Stratified sampling for human recall review.

Exists so the sampling logic — which band gets how many pairs, what a
reviewable row looks like — is testable without a cluster. The Elasticsearch
scan that feeds it is integration-shaped and lives in the script, not here.

Loaded by path for the same reason test_dot_commercial_precision_metrics.py
does it: DOT-Commercial/ cannot be a dotted module because of the hyphen.
"""

import importlib.util
from pathlib import Path

_SAMPLE_SCRIPT = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "scripts"
    / "sample_pairs_for_review.py"
)


def _load_sample_module():
    spec = importlib.util.spec_from_file_location("sample_pairs_for_review", _SAMPLE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sample_pairs_for_review = _load_sample_module()

SAMPLE_BANDS = sample_pairs_for_review.SAMPLE_BANDS
build_review_row = sample_pairs_for_review.build_review_row
stratified_sample = sample_pairs_for_review.stratified_sample


def pair(score, dot_pred="1", dot_succ="2"):
    return {
        "total_score": score,
        "gap_days": 10,
        "matched_on": ["name-phonetic"],
        "predecessor": {"dot_number": dot_pred, "legal_name": "A"},
        "successor": {"dot_number": dot_succ, "legal_name": "B"},
    }


def band_for(score):
    for lower, upper, label in SAMPLE_BANDS:
        if lower <= score < upper:
            return label
    return SAMPLE_BANDS[-1][2] if score == SAMPLE_BANDS[-1][1] else None


def test_sample_respects_the_per_band_quota():
    rows = [pair(0.40, dot_succ=str(i)) for i in range(20)]
    sample = stratified_sample(rows, per_band=5, seed=1)
    assert len(sample) == 5


def test_sample_draws_from_every_populated_band():
    rows = [pair(0.40, dot_succ="a"), pair(0.75, dot_succ="b"), pair(0.95, dot_succ="c")]
    sample = stratified_sample(rows, per_band=1, seed=1)
    bands = {band_for(row["total_score"]) for row in sample}
    assert len(bands) == 3


def test_sample_is_deterministic_for_a_given_seed():
    rows = [pair(0.40, dot_succ=str(i)) for i in range(50)]
    first = stratified_sample(rows, per_band=10, seed=7)
    second = stratified_sample(rows, per_band=10, seed=7)
    assert [r["successor"]["dot_number"] for r in first] == [
        r["successor"]["dot_number"] for r in second
    ]


def test_review_row_carries_a_null_verdict_for_the_human_to_fill_in():
    row = build_review_row(pair(0.82))
    assert row["verdict"] is None
    assert "total_score" in row and "predecessor" in row and "successor" in row


def test_review_row_never_invents_a_verdict():
    # The whole point of this tool is that a human decides, not a heuristic.
    # A default other than None would look like an answer nobody gave.
    row = build_review_row(pair(0.99))
    assert row["verdict"] is None


def test_bands_are_anchored_to_the_triage_threshold():
    """Guards the claim SAMPLE_BANDS' comment makes about where its edges come from.

    The band edge at TRIAGE_SCORE is what makes this sample answer the same
    question the rest of the precision metrics answer. If someone retunes
    TRIAGE_SCORE and the bands keep a stale hardcoded 0.70, the sample silently
    starts straddling the triage tier instead of isolating it, and the review
    it feeds is quietly measuring something else.
    """
    edges = {lower for lower, _, _ in SAMPLE_BANDS}
    assert sample_pairs_for_review.TRIAGE_SCORE in edges


def test_pairs_below_the_lowest_band_are_not_sampled():
    """A score under the sweep's own floor is not a pair anyone should adjudicate.

    _band returns None there rather than bucketing into the lowest band, so a
    stray sub-floor pair cannot consume a reviewer's quota slot.
    """
    rows = [pair(0.10, dot_succ=str(i)) for i in range(5)]
    assert stratified_sample(rows, per_band=5, seed=1) == []
