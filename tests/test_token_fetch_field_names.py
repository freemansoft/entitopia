"""Analyzed subfields must be fetched even when the column name contains a space.

A top-level `fields` list on _mtermvectors is comma-joined into a request
parameter, and any field name containing a space is then silently dropped — no
error, no warning, just an empty token set. A signal reads that as "not
evaluable", drops out, and the pair scores on whatever else remains.

Measured 2026-08-20 against a project whose columns are named "Facility Name"
and "Telephone Number": every name signal returned nothing for every pair, the
sweep reported 0 errors, and 116 plausible-looking pairs were emitted having
matched on address alone. Nothing in the output said the name signal had never
run. DOT-Commercial could not surface this, because its columns are all
snake_case.

These tests assert the request SHAPE rather than mocking a response, because
the shape is the defect: a fake that echoes back whatever it is asked for would
pass under both the broken and the fixed call.
"""

from types import SimpleNamespace

from matching.candidates import CandidateFinder


class _RecordingES:
    """Captures the mtermvectors call and returns an empty but valid response."""

    def __init__(self):
        self.calls = []

    def mtermvectors(self, **kwargs):
        self.calls.append(kwargs)
        return {"docs": []}


def _finder(es, signal_configs):
    return CandidateFinder(
        es=es,
        source_index="hospitals-000001",
        candidates_config=SimpleNamespace(max_candidates=100, seed_signals=[]),
        signal_configs=signal_configs,
    )


_NAME_SIGNAL = SimpleNamespace(
    type="name-phonetic",
    weight=0.4,
    fields=["Facility Name"],
    subfield="phonetic",
)


def test_the_request_carries_fields_per_document_not_as_a_top_level_list():
    # The whole fix. A top-level `fields` list is comma-joined and loses any
    # name containing a space.
    es = _RecordingES()
    _finder(es, [_NAME_SIGNAL])._fetch_tokens(["a", "b"])
    (call,) = es.calls
    assert "fields" not in call, "a top-level fields list drops space-containing names"
    assert "docs" in call


def test_a_space_containing_subfield_is_requested_intact():
    es = _RecordingES()
    _finder(es, [_NAME_SIGNAL])._fetch_tokens(["a"])
    (call,) = es.calls
    requested = call["docs"][0]["fields"]
    assert "Facility Name.phonetic" in requested


def test_every_document_is_asked_for_in_one_call():
    # Batching is why this is one round trip per predecessor rather than one
    # per candidate; a fix that broke it would cost a sweep dearly.
    es = _RecordingES()
    _finder(es, [_NAME_SIGNAL])._fetch_tokens(["a", "b", "c"])
    assert len(es.calls) == 1
    assert [d["_id"] for d in es.calls[0]["docs"]] == ["a", "b", "c"]


def test_the_expensive_statistics_stay_switched_off():
    # term/field statistics and positions are what make term vectors costly,
    # and none of them are read. Losing these defaults in the move to per-doc
    # would slow every sweep without changing a single score.
    es = _RecordingES()
    _finder(es, [_NAME_SIGNAL])._fetch_tokens(["a"])
    doc = es.calls[0]["docs"][0]
    for flag in ("term_statistics", "field_statistics", "positions", "offsets", "payloads"):
        assert doc[flag] is False


def test_no_call_is_made_when_no_signal_reads_tokens():
    # exact-identifier reads raw _source, so there is nothing to fetch.
    es = _RecordingES()
    phone = SimpleNamespace(
        type="exact-identifier", weight=0.2, phone_fields=["Telephone Number"]
    )
    assert _finder(es, [phone])._fetch_tokens(["a"]) == {}
    assert es.calls == []
