"""Tests for the analysis fingerprint that detects scoring against stale tokens.

The failure this guards is invisible by construction: the subfield still
exists and still has data, so every other check passes while the scores are
computed from tokens the current analyzers would no longer produce. These tests
pin the two properties that make the guard work — insensitive to key order and
formatting, sensitive to any change in analyzer behavior.
"""

from utils.analysis_fingerprint import fingerprint_analysis


def settings_with(analysis):
    return {"index": {"number_of_shards": 1, "analysis": analysis}}


def test_same_analysis_hashes_the_same_regardless_of_key_order():
    a = settings_with({"filter": {"x": {"type": "stop"}}, "analyzer": {"y": {"tokenizer": "standard"}}})
    b = settings_with({"analyzer": {"y": {"tokenizer": "standard"}}, "filter": {"x": {"type": "stop"}}})
    assert fingerprint_analysis(a) == fingerprint_analysis(b)


def test_changing_a_synonym_changes_the_hash():
    a = settings_with({"filter": {"s": {"type": "synonym", "synonyms": ["st, street"]}}})
    b = settings_with({"filter": {"s": {"type": "synonym", "synonyms": ["st => street"]}}})
    assert fingerprint_analysis(a) != fingerprint_analysis(b)


def test_unrelated_index_settings_do_not_change_the_hash():
    # Shard count has no effect on tokens, so bumping it must not look like an
    # analyzer change — otherwise the warning cries wolf and gets ignored.
    a = {"index": {"number_of_shards": 1, "analysis": {"filter": {"x": {"type": "stop"}}}}}
    b = {"index": {"number_of_shards": 5, "analysis": {"filter": {"x": {"type": "stop"}}}}}
    assert fingerprint_analysis(a) == fingerprint_analysis(b)


def test_missing_analysis_block_is_none_not_a_hash_of_nothing():
    # None means "this index declares no analyzers, there is nothing to
    # compare"; a hash would make every analyzer-free index look mismatched
    # against every other one.
    assert fingerprint_analysis({"index": {"number_of_shards": 1}}) is None
    assert fingerprint_analysis({}) is None
    assert fingerprint_analysis(None) is None


def test_analysis_at_the_top_level_is_accepted():
    nested = {"index": {"analysis": {"filter": {"x": {"type": "stop"}}}}}
    flat = {"analysis": {"filter": {"x": {"type": "stop"}}}}
    assert fingerprint_analysis(flat) == fingerprint_analysis(nested)


def test_present_but_empty_analysis_block_is_none_not_a_hash_of_empty_dict():
    # An `analysis` key that exists but is `{}` declares nothing to fingerprint,
    # same as a missing key — Task 4 treats None from either shape as "no
    # comparison to make." Pinned separately from the missing-key case because
    # a truthiness-preserving refactor of the `or` chain (e.g. switching to an
    # `in` check) could stop treating `{}` as absent while this test file still
    # passed, silently starting to hash `{}` into a real fingerprint.
    assert fingerprint_analysis({"index": {"analysis": {}}}) is None
    assert fingerprint_analysis({"analysis": {}}) is None
