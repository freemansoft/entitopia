"""Analyzer-level tests for the street analyzers in DOT-Commercial's carriers index.

These exist because street matching lives in Elasticsearch configuration, not in
Python: `AddressSignal` only compares token sets it is handed, so a defect in the
analyzer produces wrong scores that every Python-level test still passes. The
cases below are the three failures measured in the design spec — the `P.O. BOX`
tokenization break, missing abbreviations, and secondary-unit spelling — plus a
standing guard against a regression that a naive `pattern_replace` would cause.

Skipped rather than failed when Elasticsearch is unreachable, so the suite stays
runnable without Docker; the ES-free tests carry the bulk of the coverage.
"""

import json

import pytest
from elasticsearch import Elasticsearch

SETTINGS_PATH = "DOT-Commercial/configuration/carriers/index-settings.json"
TEST_INDEX = "test-street-analysis"

# The street analyzer blocks below are hand-mirrored across three
# index-settings.json files rather than generated from one shared source, so
# nothing stops them from drifting apart. Listed here (rather than diffing
# whole files) because the surrounding name-matching sections legitimately
# differ per project — DOT-Commercial has a carrier-suffix stop list and
# double-metaphone/beider-morse phonetics that CMS-Providers does not — so a
# whole-file comparison would fail on day one for reasons that have nothing
# to do with street matching.
OTHER_SETTINGS_PATHS = [
    "CMS-Providers/configuration/hospitals/index-settings.json",
    "CMS-Providers/configuration/doctors-clinicians/index-settings.json",
]
STREET_CHAR_FILTERS = ["po_box_canon"]
STREET_FILTERS = [
    "street_suffix_canon",
    "unit_designator_stop",
    "punct_white",
    "collapse_whitespace",
]
STREET_ANALYZERS = ["street_clean", "street_tokens"]


@pytest.fixture(scope="module")
def es_index():
    with open(SETTINGS_PATH) as handle:
        settings = json.load(handle)["settings"]

    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}],
        request_timeout=30,
    )
    try:
        reachable = client.ping()
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("Elasticsearch is not reachable on localhost:9200")

    client.options(ignore_status=404).indices.delete(index=TEST_INDEX)
    client.indices.create(index=TEST_INDEX, settings=settings)
    yield client
    client.options(ignore_status=404).indices.delete(index=TEST_INDEX)


def tokens(es_index, analyzer, text):
    response = es_index.indices.analyze(index=TEST_INDEX, analyzer=analyzer, text=text)
    return {token["token"] for token in response["tokens"]}


def _street_analysis_slice(settings_path):
    """Pull just the street-matching pieces out of an index-settings.json file.

    Parsed structures are compared rather than raw text so that formatting —
    key order, whitespace, quoting — can never register as drift; only an
    actual difference in what the analyzer does should fail this test.
    """
    with open(settings_path) as handle:
        analysis = json.load(handle)["settings"]["index"]["analysis"]
    return {
        "char_filter": {k: analysis["char_filter"][k] for k in STREET_CHAR_FILTERS},
        "filter": {k: analysis["filter"][k] for k in STREET_FILTERS},
        "analyzer": {k: analysis["analyzer"][k] for k in STREET_ANALYZERS},
    }


@pytest.mark.parametrize("other_settings_path", OTHER_SETTINGS_PATHS)
def test_street_analysis_is_identical_across_the_three_settings_files(other_settings_path):
    """Guards against the exact failure mode this branch's mirroring invites.

    Street matching lives in index-settings.json, copy-pasted by hand across
    DOT-Commercial and both CMS-Providers projects because Elasticsearch has
    no way to share analyzer config between indices. test_street_analysis's
    other tests only ever load the DOT file, so an edit that touches CMS
    alone — or a DOT edit that forgets to touch CMS — currently merges green:
    nothing else in the suite loads all three files at once. This test does,
    and needs no Elasticsearch, so it still catches drift when ES is down and
    every ES-backed test above is skipped.
    """
    dot_slice = _street_analysis_slice(SETTINGS_PATH)
    other_slice = _street_analysis_slice(other_settings_path)
    assert dot_slice == other_slice


@pytest.mark.parametrize(
    "spelling",
    ["P.O. BOX 1234", "PO BOX 1234", "P O BOX 1234", "P.O.BOX 1234"],
)
def test_po_box_spellings_all_produce_the_same_tokens(es_index, spelling):
    # The standard tokenizer keeps `P.O` as one token, so punct_white — a token
    # filter — rewrote the period in place and emitted the literal token `p o`,
    # which could never equal `po`. 43,799 mailing_street records use a
    # punctuated form and could not match their plain-form counterparts.
    assert tokens(es_index, "street_tokens", spelling) == {"pobox", "1234"}


def test_po_box_spellings_agree_on_the_exact_subfield_too(es_index):
    punctuated = tokens(es_index, "street_clean", "P.O. BOX 1234")
    plain = tokens(es_index, "street_clean", "PO BOX 1234")
    assert punctuated == plain


def test_street_suffix_contracts_to_one_canonical_token(es_index):
    # Contraction, not expansion: under expansion a matching suffix contributed
    # 2 to both the intersection and the set sizes, so the least identifying
    # part of an address counted twice.
    assert tokens(es_index, "street_tokens", "100 MAIN ST") == {"100", "main", "street"}
    assert tokens(es_index, "street_tokens", "100 MAIN STREET") == {"100", "main", "street"}


def test_previously_missing_abbreviations_now_canonicalize(es_index):
    assert tokens(es_index, "street_tokens", "100 RTE 9") == {"100", "route", "9"}
    assert tokens(es_index, "street_tokens", "100 MAIN PL") == {"100", "main", "place"}
    assert tokens(es_index, "street_tokens", "100 MAIN ST NW") == {
        "100", "main", "street", "northwest",
    }


def test_unit_designator_word_is_dropped_but_the_number_survives(es_index):
    # Dropping the word gives STE 200 == UNIT 200 == #200 without handing a
    # shared token to two unrelated addresses that merely both have a suite.
    expected = {"100", "main", "street", "200"}
    assert tokens(es_index, "street_tokens", "100 MAIN ST STE 200") == expected
    assert tokens(es_index, "street_tokens", "100 MAIN ST UNIT 200") == expected
    assert tokens(es_index, "street_tokens", "100 MAIN ST #200") == expected


def test_different_unit_numbers_stay_distinguishable(es_index):
    assert tokens(es_index, "street_tokens", "100 MAIN ST STE 200") != tokens(
        es_index, "street_tokens", "100 MAIN ST APT 400"
    )


def test_ordinal_street_names_are_not_rewritten(es_index):
    # Guards the CMS-Providers bug deleted in Task 2: an unanchored
    # pattern_replace of `(st)` -> `street` turns FIRST into FIRstreet. A
    # synonym filter matches whole tokens, so `1st` and `first` are untouched.
    assert tokens(es_index, "street_tokens", "100 1ST AVE") == {"100", "1st", "avenue"}
    assert tokens(es_index, "street_tokens", "100 FIRST AVE") == {"100", "first", "avenue"}
