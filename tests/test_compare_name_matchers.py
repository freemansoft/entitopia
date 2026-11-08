"""Tests for scripts/compare_name_matchers.py.

Blog Article 1 quotes this script's AUC and recall figures to argue which
fuzzy matcher suits a name field. A wrong edit distance, an AUC that counts
ties as wins, or an edit that silently returns the name unchanged would each
publish a confident, wrong comparison, and nothing at run time would notice.
These pin the arithmetic and the edits; the Elasticsearch half is exercised by
running the script against a live cluster.

Business names here are invented and checked against the cluster: a real
name in a fixture next to a matcher is the publication CLAUDE.md forbids.

Loaded by path rather than import because scripts/ is a directory of
standalone tools, not an importable package.
"""

import importlib.util
import random
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compare_name_matchers.py"


@pytest.fixture(scope="module")
def cnm():
    spec = importlib.util.spec_from_file_location("compare_name_matchers", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_levenshtein_counts_insertions_deletions_and_substitutions(cnm):
    assert cnm.levenshtein("KITTEN", "SITTING") == 3
    assert cnm.levenshtein("", "ABC") == 3
    assert cnm.levenshtein("SMITH", "SMITH") == 0


def test_auto_fuzziness_matches_elasticsearch_budgets(cnm):
    assert [cnm.auto_fuzziness(w) for w in ("AB", "ABC", "ABCDE", "ABCDEF")] == [0, 1, 1, 2]


def test_fuzzy_token_overlap_credits_a_typo_but_uses_each_token_once(cnm):
    # One typo inside AUTO's budget scores as a full match.
    assert cnm.fuzzy_token_overlap(["SMITH", "EXPRESS"], ["SMYTH", "EXPRESS"]) == 1.0
    # A repeated word cannot claim the same partner twice.
    assert cnm.fuzzy_token_overlap(["SMITH", "SMITH"], ["SMITH"]) == pytest.approx(0.75)
    assert cnm.fuzzy_token_overlap([], ["SMITH"]) is None


def test_string_measures_return_none_for_an_empty_name(cnm):
    # None, not 0.0: an empty name is not evaluable, the repo-wide distinction.
    assert cnm.edit_similarity("", "SMITH") is None
    assert cnm.trigram_similarity("SMITH", "") is None
    assert cnm.trigram_similarity("SMITH", "SMITH") == 1.0


def test_auc_counts_ties_as_half(cnm):
    assert cnm.auc([1.0], [0.0]) == 1.0
    assert cnm.auc([0.5], [0.5]) == 0.5
    assert cnm.auc([0.0], [1.0]) == 0.0


def test_recall_credits_nothing_when_lookalikes_tie_at_the_top(cnm):
    # A matcher whose unrelated pairs reach 1.0 cannot separate a 1.0 positive.
    recall, cut = cnm.recall_at_false_match_rate([1.0, 1.0], [0.0] * 98 + [1.0] * 2)
    assert cut == 1.0
    assert recall == 0.0


@pytest.mark.parametrize(
    "edit, name",
    [
        ("typo", "QUIXBORP EXPRESS LLC"),
        ("transposition", "QUIXBORP EXPRESS LLC"),
        ("respelling", "PHAQUIX FREIGHT INC"),
        ("abbreviation", "QUIXBORPLAND EXPRESS LLC"),
        ("word_dropped", "QUIXB EXPRESS FREIGHT LLC"),
        ("words_reordered", "QUIXB EXPRESS LLC"),
        ("suffix_changed", "QUIXB EXPRESS LLC"),
    ],
)
def test_each_edit_changes_the_name(cnm, edit, name):
    edited = getattr(cnm, edit)(name, random.Random(0))
    assert edited and edited != name


def test_edits_decline_names_they_cannot_apply_to(cnm):
    rng = random.Random(0)
    assert cnm.typo("QXB TRUCKING", rng) is None  # first word too short to edit inside
    assert cnm.abbreviation("QUIXB TRUCKING", rng) is None  # first word under 7 letters
    assert cnm.word_dropped("QUIXB LLC", rng) is None  # would leave one word
    assert cnm.words_reordered("QUIXB", rng) is None
