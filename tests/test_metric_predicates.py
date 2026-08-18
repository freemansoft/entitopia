"""The closed predicate menu, and the two rules that carry a decision.

A null gap never matches a gap predicate: an unparseable date is "not
evaluable", which is not the same as "outside the window", and counting it as
inside inflates the one metric this whole harness exists to move.

fields_equal treats null as not-equal: two records that both lack a name are not
"the same name". A naive == would count every pair of nameless records as an
identical-name match, inflating the metric that anchors the README's sanity
check. This is the same blank-never-matches-blank rule stated in
docs/adding-a-dataset.md.
"""

import pytest

from utils.metric_predicates import PREDICATES, evaluate


def _pair(**overrides):
    base = {
        "total_score": 0.8,
        "gap_days": 30,
        "matched_on": ["name-token", "shared-token"],
        "predecessor": {"legal_name": "ACME EXAMPLE", "entity_key": "1"},
        "successor": {"legal_name": "ACME EXAMPLE", "entity_key": "2"},
        "signals": [
            {"signal_type": "shared-token", "score": 1.0},
            {"signal_type": "name-token", "score": 0.5},
        ],
    }
    base.update(overrides)
    return base


def test_score_gte():
    assert evaluate({"score_gte": 0.7}, _pair()) is True
    assert evaluate({"score_gte": 0.9}, _pair()) is False


def test_score_lt():
    assert evaluate({"score_lt": 0.9}, _pair()) is True
    assert evaluate({"score_lt": 0.7}, _pair()) is False


def test_a_missing_score_is_treated_as_zero():
    # A pair with no total_score is malformed, not high-scoring. Treating it as
    # missing-and-therefore-passing would let junk into every guarded band.
    assert evaluate({"score_gte": 0.1}, _pair(total_score=None)) is False


def test_gap_between_is_inclusive_on_both_ends():
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=365)) is True
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=-180)) is True
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=366)) is False
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=-181)) is False


def test_a_null_gap_never_matches_a_gap_predicate():
    for predicate in ({"gap_between": [-180, 365]}, {"gap_lte": 365}, {"gap_gte": 0}):
        assert evaluate(predicate, _pair(gap_days=None)) is False


def test_gap_lte_and_gte_are_inclusive():
    assert evaluate({"gap_lte": 30}, _pair()) is True
    assert evaluate({"gap_gte": 30}, _pair()) is True


def test_has_signal_type_is_an_intersection():
    assert (
        evaluate({"has_signal_type": ["shared-token", "exact-identifier"]}, _pair())
        is True
    )
    assert evaluate({"has_signal_type": ["exact-identifier"]}, _pair()) is False


def test_matched_on_equals_is_exact_set_equality():
    assert evaluate({"matched_on_equals": ["shared-token"]}, _pair()) is False
    assert (
        evaluate({"matched_on_equals": ["name-token", "shared-token"]}, _pair()) is True
    )


def test_matched_on_equals_ignores_order():
    assert (
        evaluate({"matched_on_equals": ["shared-token", "name-token"]}, _pair()) is True
    )


def test_matched_identity_equals_intersects_identity_types_first():
    # temporal is not an identity type, so a pair matching on shared-token and
    # temporal still counts as identity-only-shared-token. This distinction was
    # worth 156 pairs on the shipped baseline, which is why the project keeps
    # both readings as separate metrics.
    pair = _pair(matched_on=["shared-token", "temporal"])
    assert evaluate({"matched_identity_equals": ["shared-token"]}, pair) is True
    assert evaluate({"matched_on_equals": ["shared-token"]}, pair) is False


def test_fields_equal_compares_both_sides():
    assert evaluate({"fields_equal": "legal_name"}, _pair()) is True
    pair = _pair(successor={"legal_name": "OTHER", "entity_key": "2"})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_fields_equal_treats_null_as_not_equal():
    # Blank must never match blank -- otherwise every pair of nameless records
    # counts as an identical-name match.
    pair = _pair(predecessor={"legal_name": None}, successor={"legal_name": None})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_fields_equal_with_one_side_missing_is_false():
    pair = _pair(successor={"entity_key": "2"})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_signal_count_gte_counts_evaluated_signals():
    assert evaluate({"signal_count_gte": 2}, _pair()) is True
    assert evaluate({"signal_count_gte": 3}, _pair()) is False


def test_all_requires_every_clause():
    assert evaluate({"all": [{"score_gte": 0.7}, {"gap_lte": 365}]}, _pair()) is True
    assert evaluate({"all": [{"score_gte": 0.7}, {"gap_lte": 10}]}, _pair()) is False


def test_any_requires_one_clause():
    assert evaluate({"any": [{"score_gte": 0.99}, {"gap_lte": 365}]}, _pair()) is True
    assert evaluate({"any": [{"score_gte": 0.99}, {"gap_lte": 10}]}, _pair()) is False


def test_not_inverts():
    assert evaluate({"not": {"score_gte": 0.99}}, _pair()) is True
    assert evaluate({"not": {"score_gte": 0.7}}, _pair()) is False


def test_an_empty_predicate_matches_everything():
    # The `pairs` metric has no filter at all; an empty predicate is how a
    # metric says "every pair".
    assert evaluate({}, _pair()) is True


def test_an_unknown_predicate_raises():
    # A closed menu, for the same reason the selector clause menu is closed: a
    # typo that quietly matches nothing reports a metric of zero as though it
    # had been measured.
    with pytest.raises(ValueError, match="unknown predicate"):
        evaluate({"score_greater": 0.7}, _pair())


def test_a_predicate_declaring_two_keys_raises():
    with pytest.raises(ValueError, match="exactly one"):
        evaluate({"score_gte": 0.7, "gap_lte": 10}, _pair())


def test_the_menu_is_enumerated_for_the_schema():
    # The schema's enum is generated from this set, so the two cannot drift.
    assert "score_gte" in PREDICATES
    assert "matched_identity_equals" in PREDICATES
    assert "all" in PREDICATES
