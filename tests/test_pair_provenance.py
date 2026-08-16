"""Provenance carried by a scored pair: which index, analyzed which way.

A pair outlives the run that made it and is routinely read on its own, so
"which analyzers produced this score?" has to be answerable from the document
rather than from a log line or from the source index still existing. These
tests pin that the answer is recorded when it is known and left absent — not
faked — when it is not, because a placeholder fingerprint would later read as
a real one and be quoted as evidence.
"""

import logging

from matching.documents import CarrierDoc
from matching.scorer import ScoredPair
from phase_providers.phase_entity_match import PhaseEntityMatch, RunProvenance


class FakeBody(dict):
    """A get_mapping response: a dict that also answers .body, as the client does."""

    @property
    def body(self):
        return self


class FakeIndices:
    """Records put_mapping calls, or raises, standing in for a live cluster."""

    def __init__(self, fail=False, mappings=None):
        self.calls = []
        self.fail = fail
        self.mappings = mappings or {}

    def put_mapping(self, **kwargs):
        if self.fail:
            raise RuntimeError("cluster said no")
        self.calls.append(kwargs)
        return {"acknowledged": True}

    def refresh(self, index):
        return {"_shards": {}}

    def get_mapping(self, index):
        return FakeBody(self.mappings)


class FakeEs:
    def __init__(self, fail=False, mappings=None):
        self.indices = FakeIndices(fail=fail, mappings=mappings)

    def count(self, index):
        return {"count": 10}


def match_phase(es=None):
    return PhaseEntityMatch(
        es=es, project="DOT-Commercial", one_step="chameleon-detection", project_config=None
    )


def pair():
    pred = CarrierDoc(
        dot_number="1",
        source={"out_of_service_orders": {"oos_date": "2025-01-01", "oos_reason": "X"}},
    )
    succ = CarrierDoc(dot_number="2", source={"add_date": "2025-02-01"})
    return ScoredPair(predecessor=pred, successor=succ, total_score=0.9, signals_present=2)


def provenance(**overrides):
    base = {
        "run_id": "run1",
        "generated_at": "2026-08-15T00:00:00+00:00",
        "source_index": "carriers-2026.08.13-000001",
        "source_alias": "carriers-000001",
        "analysis_fingerprint": "abc123",
    }
    base.update(overrides)
    return RunProvenance(**base)


def source_of(**overrides):
    return match_phase()._to_action(
        pair(), "chameleon-candidates-000001", provenance(**overrides)
    )["_source"]


def test_pair_records_the_index_and_fingerprint_it_was_scored_from():
    document = source_of()
    assert document["source_index"] == "carriers-2026.08.13-000001"
    assert document["source_alias"] == "carriers-000001"
    assert document["analysis_fingerprint"] == "abc123"


def test_unstamped_source_index_leaves_the_field_absent_rather_than_null():
    # An index predating the stamp is unknown, not wrong. Writing a null (or
    # worse, the fingerprint computed from config, which is not what tokenized
    # this data) would let a reader quote a fingerprint the pair never had.
    document = source_of(analysis_fingerprint=None)
    assert "analysis_fingerprint" not in document
    assert document["source_index"] == "carriers-2026.08.13-000001"


def test_pair_with_no_alias_omits_the_field_rather_than_repeating_the_index():
    document = source_of(source_alias=None)
    assert "source_alias" not in document
    assert document["source_index"] == "carriers-2026.08.13-000001"


def test_pair_still_carries_run_id_and_generated_at():
    # The provenance fields are additions, not replacements — existing queries
    # and the review sampler key off these two.
    document = source_of()
    assert document["run_id"] == "run1"
    assert document["generated_at"] == "2026-08-15T00:00:00+00:00"


def test_candidates_index_is_stamped_with_source_provenance():
    es = FakeEs()
    match_phase(es)._stamp_provenance("chameleon-candidates-000001", provenance())
    (call,) = es.indices.calls
    assert call["index"] == "chameleon-candidates-000001"
    assert call["meta"] == {
        "source_index": "carriers-2026.08.13-000001",
        "source_alias": "carriers-000001",
        "source_analysis_fingerprint": "abc123",
    }


def test_borrowed_fingerprint_is_not_stored_as_this_index_s_own():
    # The candidates index has no analyzers, so a bare `analysis_fingerprint`
    # key here would be compared against the wrong config by anything reading
    # an index's own stamp and report a mismatch on every run.
    es = FakeEs()
    match_phase(es)._stamp_provenance("chameleon-candidates-000001", provenance())
    assert "analysis_fingerprint" not in es.indices.calls[0]["meta"]


def test_unstamped_source_index_records_the_index_name_alone():
    es = FakeEs()
    match_phase(es)._stamp_provenance(
        "chameleon-candidates-000001",
        provenance(source_alias=None, analysis_fingerprint=None),
    )
    assert es.indices.calls[0]["meta"] == {"source_index": "carriers-2026.08.13-000001"}


def test_a_failed_stamp_logs_but_does_not_abort_the_sweep(caplog):
    # The sweep takes hours and every pair carries the same value in its own
    # document; throwing away the run over the index-level stamp costs far more
    # than it saves.
    es = FakeEs(fail=True)
    with caplog.at_level(logging.ERROR):
        match_phase(es)._stamp_provenance("chameleon-candidates-000001", provenance())
    assert "could not stamp" in caplog.text.lower()


def test_configured_alias_is_resolved_to_the_concrete_index_it_points_at():
    # The whole point: entity-match.json names an alias, and a rebuild repoints
    # it, so stamping the configured name would identify a different index next
    # month. get_mapping is keyed by concrete name even when queried through an
    # alias, which is where the real name comes from.
    es = FakeEs(mappings={"carriers-2026.08.13-000001": {"mappings": {"properties": {}}}})
    ok, resolved, _ = match_phase(es)._preflight("carriers-000001", set(), None)
    assert ok is True
    assert resolved == "carriers-2026.08.13-000001"


def test_provenance_keeps_the_configured_name_when_it_differs():
    got = RunProvenance.for_sweep("carriers-000001", "carriers-2026.08.13-000001", "abc")
    assert got.source_index == "carriers-2026.08.13-000001"
    assert got.source_alias == "carriers-000001"


def test_provenance_does_not_repeat_a_concretely_configured_index_as_an_alias():
    got = RunProvenance.for_sweep("carriers-2026.08.13-000001", "carriers-2026.08.13-000001", "a")
    assert got.source_alias is None


def test_unresolvable_source_falls_back_to_the_configured_name():
    # A wrong-but-present name still tells a reader what was asked for; a blank
    # one leaves the pair unable to say even that.
    got = RunProvenance.for_sweep("carriers-000001", None, None)
    assert got.source_index == "carriers-000001"
    assert got.source_alias is None


def test_an_alias_spanning_several_indexes_warns_that_attribution_is_partial(caplog):
    # A fan-out alias is normally left over from a previous load. The sweep
    # reads every index behind it but can attribute its pairs to only one, so
    # the stamp is quietly incomplete unless this says so.
    es = FakeEs(
        mappings={
            "carriers-2026.08.13-000001": {"mappings": {"properties": {}}},
            "carriers-2026.08.05-000001": {"mappings": {"properties": {}}},
        }
    )
    with caplog.at_level(logging.WARNING):
        match_phase(es)._preflight("carriers-000001", set(), None)
    assert "2 indexes" in caplog.text
