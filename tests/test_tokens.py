"""Token helpers: overlap measures and identifier normalization.

Small pure functions, tested directly because every signal's score depends on
them and a subtle change here shifts every result in a sweep without erroring.
"""

import pytest

from matching.tokens import (
    auto_edit_budget,
    blended_overlap,
    containment,
    fuzzy_blended_overlap,
    jaccard,
    levenshtein,
    normalize_phone,
    normalize_text_identifier,
    parse_fuzzy_edits,
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


# Typo-tolerant word overlap. The name-token signal compared words exactly, so a
# one-letter typo in a business's distinctive word cost the whole word. Measured
# 2026-11-08 with scripts/compare_name_matchers.py on 400 carrier names: a typo
# dropped the phonetic name signals below chance (AUC 0.446, 0.328) while word
# matching within Elasticsearch's AUTO edit budget held 0.999.


def test_auto_edit_budget_follows_elasticsearch():
    # No edits up to 2 letters, one up to 5, two beyond: the same thresholds
    # as fuzziness: AUTO, so the scorer credits what a fuzzy query would.
    assert [auto_edit_budget(w) for w in ("AB", "ABC", "ABCDE", "ABCDEF")] == [0, 1, 1, 2]


def test_auto_edit_budget_takes_elasticsearch_style_thresholds():
    # AUTO:4,6 gives three-letter words no budget. On the carrier sweep of
    # 2026-11-08, 3,447 of 7,177 pairs that plain AUTO added owed their only
    # fuzzy credit to three-letter words one letter apart, usually initials.
    budgets = [auto_edit_budget(w, low=4, high=6) for w in ("ABC", "ABCD", "ABCDE", "ABCDEF")]
    assert budgets == [0, 1, 1, 2]


def test_parse_fuzzy_edits_reads_auto_and_its_thresholds():
    assert parse_fuzzy_edits("AUTO") == (3, 6)
    assert parse_fuzzy_edits("AUTO:4,6") == (4, 6)


@pytest.mark.parametrize("spec", ["AUTO:6,4", "AUTO:0,6", "AUTO:4", "auto", "2"])
def test_parse_fuzzy_edits_rejects_what_elasticsearch_would_not_mean(spec):
    # Raised when the signal is built, so a bad value stops the run before any
    # pair is scored rather than failing on every pair.
    with pytest.raises(ValueError):
        parse_fuzzy_edits(spec)


def test_fuzzy_overlap_with_a_higher_low_threshold_matches_three_letter_words_exactly():
    assert fuzzy_blended_overlap({"qxz"}, {"qyz"}) == 1.0
    assert fuzzy_blended_overlap({"qxz"}, {"qyz"}, low=4, high=6) == 0.0
    assert fuzzy_blended_overlap({"quillby"}, {"quilby"}, low=4, high=6) == 1.0


def test_levenshtein_counts_single_letter_edits():
    assert levenshtein("SMITH", "SMYTH") == 1
    assert levenshtein("KITTEN", "SITTING") == 3
    assert levenshtein("", "ABC") == 3


def test_fuzzy_overlap_credits_a_one_letter_typo_as_a_full_match():
    assert fuzzy_blended_overlap({"quillby", "freight"}, {"quilby", "freight"}) == 1.0


def test_fuzzy_overlap_matches_short_words_only_exactly():
    # "co" and "cp" are one edit apart, but two-letter words get no budget:
    # otherwise every short word collides with a dozen others.
    assert fuzzy_blended_overlap({"co"}, {"cp"}) == 0.0


def test_fuzzy_overlap_budget_follows_the_shorter_word():
    # "abc" (budget 1) against "abcde" (budget 1) is two edits: no match.
    # Using the shorter word's budget keeps the score symmetric and stops a
    # long word from licensing a loose match to a short one.
    assert fuzzy_blended_overlap({"abc"}, {"abcde"}) == 0.0
    assert fuzzy_blended_overlap({"abcde"}, {"abc"}) == 0.0


def test_fuzzy_overlap_pairs_each_word_at_most_once():
    # Two near-identical words on one side cannot both claim one word on the
    # other, or repetition would inflate the overlap.
    score = fuzzy_blended_overlap({"smith", "smyth"}, {"smith"})
    assert score == pytest.approx(0.5 * (1 / 2) + 0.5 * (1 / 1))


def test_fuzzy_overlap_is_symmetric():
    a, b = {"quillby", "freight", "inc"}, {"quilby", "frieght", "llc"}
    assert fuzzy_blended_overlap(a, b) == fuzzy_blended_overlap(b, a)


def test_fuzzy_overlap_equals_exact_overlap_when_words_match_exactly():
    a, b = {"smith", "trucking", "llc"}, {"smith", "llc"}
    assert fuzzy_blended_overlap(a, b) == blended_overlap(a, b)


def test_fuzzy_overlap_is_none_when_either_side_is_empty():
    # None, not 0.0: an empty name is not evaluable.
    assert fuzzy_blended_overlap(set(), {"smith"}) is None
    assert fuzzy_blended_overlap({"smith"}, set()) is None
