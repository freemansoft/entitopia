"""Analyzer-level guards for the German additions to GLEIF-Entities' index settings.

The GLEIF index serves every country from one set of analyzers, which were
first measured on US names and addresses. Two German gaps were measured on
2026-11-27 (GLEIF-Entities README, "German sweeps"):

- German legal forms and filler words (`mbH`, `UG (haftungsbeschränkt)`,
  `e.V.`, `und`) were not stopped. Every German name carried them into its
  phonetic codes, so unrelated German entities scored as alike.
- A German street name carries its suffix inside one word, spelled
  `Straße`, `Strasse` or `Str.`. The US suffix rules never fold those, so one
  headquarters written two ways compared as two streets. 190 German
  registered successions match exactly only once the spellings are folded.

Both live in Elasticsearch configuration, so a Python-level test cannot see
them break: the address and name signals compare whatever tokens they are
handed, and a sweep run on a broken analyzer logs success. The US cases pin
that the folding leaves US streets as they were.

Skipped rather than failed when Elasticsearch is unreachable, like
tests/test_street_analysis.py.
"""

import json

import pytest
from elasticsearch import Elasticsearch

SETTINGS_PATH = "GLEIF-Entities/configuration/entities/index-settings.json"
TEST_INDEX = "test-gleif-analysis"


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


def tokens(client, analyzer, text):
    response = client.indices.analyze(index=TEST_INDEX, analyzer=analyzer, text=text)
    return [token["token"] for token in response["tokens"]]


@pytest.mark.parametrize("analyzer", ["street_clean", "street_tokens", "street_local_tokens"])
@pytest.mark.parametrize(
    "spellings",
    [
        ("Hauptstraße 5", "Hauptstrasse 5", "Hauptstr. 5", "HAUPTSTR 5"),
        ("Berliner Straße 12", "Berliner Strasse 12", "Berliner Str. 12"),
    ],
)
def test_german_street_suffix_spellings_fold_together(es_index, analyzer, spellings):
    produced = {tuple(tokens(es_index, analyzer, text)) for text in spellings}
    assert len(produced) == 1, produced


def test_folded_standalone_suffix_joins_the_us_street_token(es_index):
    # A standalone `Straße` folds to `str`, which the US suffix rules already
    # map to `street`, so the token analyzers agree on one canonical word.
    assert tokens(es_index, "street_tokens", "Berliner Straße 12") == ["berliner", "street", "12"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("100 Main St", ["100", "main", "street"]),
        ("100 Main Street", ["100", "main", "street"]),
        ("1 Astor Place", ["1", "astor", "place"]),
    ],
)
def test_us_streets_are_unchanged(es_index, text, expected):
    assert tokens(es_index, "street_tokens", text) == expected


@pytest.mark.parametrize(
    "name",
    [
        "mbH",
        "UG (haftungsbeschränkt)",
        "e.V.",
        "GbR",
        "eG",
        "AG",
        "Gesellschaft mit beschränkter Haftung",
        "und für der",
    ],
)
@pytest.mark.parametrize("analyzer", ["name_phonetic", "name_phonetic_bm"])
def test_german_legal_forms_and_fillers_are_stopped(es_index, analyzer, name):
    assert tokens(es_index, analyzer, name) == []


def test_german_legal_form_variants_share_phonetic_codes(es_index):
    # The distinctive word must be all that is left, so one company written
    # with two legal forms keeps identical codes.
    codes = {
        tuple(sorted(tokens(es_index, "name_phonetic", name)))
        for name in ("Quuxwerk GmbH", "Quuxwerk UG (haftungsbeschränkt)", "Quuxwerk mbH", "Quuxwerk AG")
    }
    assert len(codes) == 1, codes
