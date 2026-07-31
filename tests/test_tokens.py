import pytest

from matching.tokens import (
    blended_overlap,
    containment,
    jaccard,
    normalize_phone,
    normalize_text_identifier,
)


def test_jaccard_identical_sets_is_one():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_set_is_zero():
    assert jaccard(set(), {"a"}) == 0.0


def test_containment_rewards_subset():
    # "SMITH LLC" tokens are a subset of "SMITH TRUCKING LLC" tokens
    assert containment({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"}) == 1.0


def test_jaccard_punishes_the_same_subset():
    assert jaccard({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"}) == pytest.approx(2 / 3)


def test_blended_overlap_abbreviation_scores_above_five_eighths():
    # This is the abbreviation case the design exists to catch.
    score = blended_overlap({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"})
    assert score == pytest.approx(0.5 * (2 / 3) + 0.5 * 1.0)
    assert score > 0.8


def test_blended_overlap_returns_none_for_empty_set():
    # A carrier named literally "TRUCKING LLC" reduces to zero tokens after
    # the corporate-suffix stop filter. That is "no signal", not "no match".
    assert blended_overlap(set(), {"SM0"}) is None
    assert blended_overlap({"SM0"}, set()) is None


def test_normalize_phone_strips_formatting():
    assert normalize_phone("(503) 289-5558") == "5032895558"


def test_normalize_phone_rejects_repeated_digit_placeholders():
    assert normalize_phone("(000) 000-0000") is None
    assert normalize_phone("1111111111") is None


def test_normalize_phone_rejects_blank_and_short():
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
    assert normalize_phone("12345") is None


def test_normalize_text_identifier_lowercases_and_trims():
    assert normalize_text_identifier("  Joe@Example.COM ") == "joe@example.com"


def test_normalize_text_identifier_rejects_blank():
    assert normalize_text_identifier("   ") is None
    assert normalize_text_identifier(None) is None
