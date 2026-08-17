"""Rarity weighting is a general mechanism, not a BOC-3 one.

The arithmetic is load-bearing and deliberately unchanged by the rename:
normalized IDF rather than 1 - share, because with only 89 distinct values the
largest share is 9.4%, so 1 - share would compress every value into
[0.906, 1.0] and leave the signal no discriminating power at all.

The 0.0 floor is the other thing worth pinning. It is the bottom of the range,
not "neutral" and emphatically not the 1.0 "unseen" placeholder -- scoring an
unmeasurable value as novel would misrepresent it, and inventing a mid-range
number would fabricate precision the data cannot support.
"""

import math

import pytest

from matching.documents import FieldRarityTable, ScoringContext


def test_unseen_value_scores_one():
    table = FieldRarityTable(counts={"common": 90}, total=100)
    assert table.rarity("never-seen") == 1.0


def test_dominant_value_scores_below_a_rare_one():
    table = FieldRarityTable(counts={"common": 90, "rare": 1}, total=100)
    assert table.rarity("common") < table.rarity("rare")


def test_uses_normalized_idf_not_one_minus_share():
    # 1 - share would give 0.10 here. Normalized IDF spreads the population
    # far wider, which is the entire reason for the choice.
    table = FieldRarityTable(counts={"common": 90}, total=100)
    expected = math.log(100 / 90) / math.log(100)
    assert table.rarity("common") == pytest.approx(expected)
    assert table.rarity("common") < 0.10


def test_tiny_corpus_floors_to_zero():
    # log(N) is 0 or undefined below two records, so there is no defensible
    # rarity to compute.
    assert FieldRarityTable(counts={}, total=1).rarity("x") == 0.0
    assert FieldRarityTable(counts={}, total=0).rarity("x") == 0.0


def test_lookup_is_case_and_whitespace_insensitive():
    # Must match how the signal normalizes before intersecting, or every
    # lookup silently misses and rarity weighting turns itself off with no
    # error anywhere.
    table = FieldRarityTable(counts={"acme filings": 5}, total=100)
    assert table.rarity("  ACME Filings ") == table.rarity("acme filings")


def test_context_rarity_is_keyed_by_field_path():
    # Keyed by field because the same string can be a dominant value on one
    # field and a rare one on another.
    ctx = ScoringContext(
        rarity_tables={
            "a.name": FieldRarityTable({"shared": 50}, 100),
            "b.name": FieldRarityTable({"shared": 1}, 100),
        }
    )
    assert ctx.rarity("a.name", "shared") < ctx.rarity("b.name", "shared")


def test_context_rarity_without_a_table_scores_zero():
    # No table means frequencies were never gathered. A shared value is still
    # real evidence, but calling it novel on no data would overstate it.
    assert ScoringContext().rarity("some.field", "a") == 0.0


def test_ignored_values_still_work_after_the_rename():
    # is_ignored shares the normalizer with the rarity lookup; a rename that
    # split them would silently disable every configured ignore list.
    ctx = ScoringContext(ignored_values={"vin": {"UNKNOWN"}})
    assert ctx.is_ignored("vin", "unknown") is True
    assert ctx.is_ignored("vin", "1FUJGLDR0CSBP9784") is False


def test_wildcard_ignore_still_applies_to_every_field():
    ctx = ScoringContext(ignored_values={"*": {"N/A"}})
    assert ctx.is_ignored("anything", "n/a") is True
