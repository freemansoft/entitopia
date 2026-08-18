"""Three metric kinds, and the arithmetic that decides what a number means.

The zero-denominator case is the one worth pinning hardest: a ratio over an
empty band must be 0.0, not NaN. A NaN serializes into a baseline file as the
bare token NaN, which is not valid JSON, so it would poison every later
comparison against that baseline rather than failing at the point it arose.
"""

import json

import pytest

from utils.metric_runner import summarize


def _pair(score=0.8, gap=30, matched=None, pred_key="1", name="ACME EXAMPLE"):
    return {
        "total_score": score,
        "gap_days": gap,
        "matched_on": matched if matched is not None else ["name-token"],
        "predecessor": {"legal_name": name, "entity_key": pred_key},
        "successor": {"legal_name": name, "entity_key": "9"},
        "signals": [{"signal_type": "name-token", "score": 0.5}],
    }


def test_a_metric_with_no_filter_counts_every_pair():
    metrics = [{"name": "pairs"}]
    assert summarize(metrics, [_pair(), _pair()]) == {"pairs": 2}


def test_a_filter_counts_only_matching_pairs():
    metrics = [{"name": "high", "filter": {"score_gte": 0.7}}]
    result = summarize(metrics, [_pair(score=0.9), _pair(score=0.5)])
    assert result["high"] == 1


def test_distinct_counts_unique_values_of_a_dotted_path():
    metrics = [{"name": "preds", "distinct": "predecessor.entity_key"}]
    pairs = [_pair(pred_key="1"), _pair(pred_key="1"), _pair(pred_key="2")]
    assert summarize(metrics, pairs)["preds"] == 2


def test_distinct_ignores_pairs_missing_the_path():
    # A missing value must not become a None bucket that inflates the count by
    # one for every malformed pair.
    metrics = [{"name": "preds", "distinct": "predecessor.nope"}]
    assert summarize(metrics, [_pair(), _pair()])["preds"] == 0


def test_distinct_respects_its_own_filter():
    metrics = [
        {"name": "preds", "distinct": "predecessor.entity_key", "filter": {"score_gte": 0.7}}
    ]
    pairs = [_pair(score=0.9, pred_key="1"), _pair(score=0.1, pred_key="2")]
    assert summarize(metrics, pairs)["preds"] == 1


def test_a_ratio_divides_two_named_metrics():
    metrics = [
        {"name": "all_pairs"},
        {"name": "high", "filter": {"score_gte": 0.7}},
        {"name": "share", "ratio": {"numerator": "high", "denominator": "all_pairs"}},
    ]
    result = summarize(metrics, [_pair(score=0.9), _pair(score=0.1)])
    assert result["share"] == 0.5


def test_a_zero_denominator_yields_zero_not_nan():
    # NaN would serialize into a baseline as the bare token NaN, which is not
    # valid JSON and would poison every later comparison against it.
    metrics = [
        {"name": "none_at_all", "filter": {"score_gte": 99}},
        {"name": "high", "filter": {"score_gte": 0.7}},
        {"name": "share", "ratio": {"numerator": "high", "denominator": "none_at_all"}},
    ]
    result = summarize(metrics, [_pair()])
    assert result["share"] == 0.0
    json.dumps(result)  # must remain serializable


def test_a_ratio_may_name_a_metric_declared_after_it():
    # Counts are computed in one pass and ratios afterwards, so declaration
    # order does not constrain what a ratio can reference.
    metrics = [
        {"name": "share", "ratio": {"numerator": "high", "denominator": "all_pairs"}},
        {"name": "all_pairs"},
        {"name": "high", "filter": {"score_gte": 0.7}},
    ]
    assert summarize(metrics, [_pair(score=0.9), _pair(score=0.1)])["share"] == 0.5


def test_a_ratio_naming_an_undefined_metric_raises():
    # Caught by a coherence rule before a run in practice; raising here means a
    # direct caller cannot get a silently absent number either.
    metrics = [{"name": "share", "ratio": {"numerator": "nope", "denominator": "also"}}]
    with pytest.raises(KeyError):
        summarize(metrics, [_pair()])


def test_every_metric_appears_even_when_it_counts_nothing():
    # A metric absent from the record breaks compare(), which raises on a
    # missing baseline key. Zero is a measurement; absence is not.
    metrics = [{"name": "never", "filter": {"score_gte": 99}}]
    assert summarize(metrics, [_pair()]) == {"never": 0}


def test_summarize_consumes_a_generator_without_materializing_it():
    # A real population is hundreds of thousands of pairs streamed from a scan;
    # holding them all would defeat the point of scanning.
    consumed = []

    def _stream():
        for index in range(3):
            consumed.append(index)
            yield _pair(pred_key=str(index))

    result = summarize([{"name": "pairs"}], _stream())
    assert result["pairs"] == 3
    assert consumed == [0, 1, 2]


def test_one_pass_serves_every_metric():
    # Each pair must be visited once regardless of how many metrics are
    # configured; a pass per metric would make a twelve-metric config twelve
    # scans of a 75,000-pair index.
    seen = []

    def _stream():
        for index in range(4):
            seen.append(index)
            yield _pair(score=0.9)

    metrics = [
        {"name": "a", "filter": {"score_gte": 0.7}},
        {"name": "b", "filter": {"score_gte": 0.8}},
        {"name": "c", "distinct": "predecessor.entity_key"},
    ]
    summarize(metrics, _stream())
    assert seen == [0, 1, 2, 3]
