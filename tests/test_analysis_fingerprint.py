"""Tests for the analysis fingerprint that detects scoring against stale tokens.

The failure this guards is invisible by construction: the subfield still
exists and still has data, so every other check passes while the scores are
computed from tokens the current analyzers would no longer produce. These tests
pin the two properties that make the guard work — insensitive to key order and
formatting, sensitive to any change in analyzer behavior.
"""

import logging

from phase_providers.phase_entity_match import PhaseEntityMatch
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


def test_changed_analyzer_binding_changes_the_hash():
    # This is the F4 regression: repointing which analyzer a subfield uses is
    # invisible to a fingerprint that only hashes the analysis block, because
    # the analyzer itself may be untouched — only the binding in
    # index-mappings.json moved. That is exactly the change a reindex-skip
    # would leave undetected.
    settings = settings_with({"analyzer": {"street_tokens": {"tokenizer": "standard"}}})
    a = {"phy_street": {"fields": {"tokens": {"type": "text", "analyzer": "street_tokens"}}}}
    b = {"phy_street": {"fields": {"tokens": {"type": "text", "analyzer": "street_clean"}}}}
    assert fingerprint_analysis(settings, a) != fingerprint_analysis(settings, b)


def test_unrelated_new_field_does_not_change_the_hash():
    # A mapping change that adds no new analyzer binding — a plain keyword
    # field, say — must not move the fingerprint. Otherwise every unrelated
    # schema edit would look like a staleness event and operators would learn
    # to ignore the warning.
    settings = settings_with({"analyzer": {"street_tokens": {"tokenizer": "standard"}}})
    a = {"phy_street": {"fields": {"tokens": {"type": "text", "analyzer": "street_tokens"}}}}
    b = {
        "phy_street": {"fields": {"tokens": {"type": "text", "analyzer": "street_tokens"}}},
        "phy_zip": {"type": "keyword"},
    }
    assert fingerprint_analysis(settings, a) == fingerprint_analysis(settings, b)


def test_mappings_absent_falls_back_to_settings_only_hash():
    # mapping_properties defaults to None so every caller that predates F4 —
    # and any future one that genuinely has no mappings in hand — keeps
    # getting the same value it always did.
    settings = settings_with({"analyzer": {"street_tokens": {"tokenizer": "standard"}}})
    assert fingerprint_analysis(settings) == fingerprint_analysis(settings, None)


def test_nested_fields_and_properties_are_both_walked_for_bindings():
    # Multi-fields (`fields`) and nested objects (`properties`) both hide
    # analyzer bindings a shallow top-level scan would miss — this repo uses
    # both shapes (legal_name.phonetic is a multi-field, boc3_agents.co_name
    # is nested), so a fingerprint that only checked one shape would be blind
    # to drift in the other.
    settings = settings_with({})
    multi_field = {"legal_name": {"fields": {"phonetic": {"analyzer": "name_phonetic"}}}}
    nested = {"boc3_agents": {"properties": {"co_name": {"analyzer": "name_clean"}}}}
    assert fingerprint_analysis(settings, multi_field) != fingerprint_analysis(settings, nested)
    assert fingerprint_analysis(settings, multi_field) != fingerprint_analysis(settings, {})


def match_phase():
    return PhaseEntityMatch(es=None, project="DOT-Commercial", one_step="x", project_config=None)


def test_mismatched_fingerprint_logs_an_error_and_returns_true(caplog):
    # Returns True because the sweep must still run: an operator comparing
    # against an older index is doing something legitimate. Only silence is
    # unacceptable.
    with caplog.at_level(logging.ERROR):
        result = match_phase()._check_analysis_fingerprint("carriers-000001", "aaaa", "bbbb")
    assert result is True
    assert "aaaa" in caplog.text and "bbbb" in caplog.text


def test_matching_fingerprint_logs_no_error(caplog):
    with caplog.at_level(logging.ERROR):
        match_phase()._check_analysis_fingerprint("carriers-000001", "aaaa", "aaaa")
    assert caplog.text == ""


def test_index_predating_the_stamp_warns_rather_than_claiming_a_mismatch(caplog):
    # An index built before the fingerprint existed carries no _meta. That is
    # unknown, not wrong — reporting it as a mismatch would train the operator
    # to ignore the message.
    with caplog.at_level(logging.WARNING):
        match_phase()._check_analysis_fingerprint("carriers-000001", None, "bbbb")
    assert "no analysis fingerprint" in caplog.text.lower()
    assert "does not match" not in caplog.text.lower()
