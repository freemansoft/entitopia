"""A pipeline Elasticsearch refuses must fail the run, not be logged at INFO.

The third instance of a pattern this repo has already closed twice: the phase
caught BadRequestError, logged it below the level anyone scans a long run for,
and returned success. `phase_index_mappings` and `phase_enrichment_policies`
were fixed when a real run exposed each; `phase_pipelines` was missed, and it
sits upstream of both.

What makes it worse here than for a mapping is that the load still succeeds and
still looks right. A dot_number pipeline exists only to normalize a join key --
the documents index cleanly with or without it, and the damage is an enrichment
that silently matches nothing, which is the failure that has already cost this
project 546,042 and 565,299 enriched documents on two separate occasions with no
error anywhere. A refused pipeline is also the one way the join-contract fix can
be reverted at runtime without anyone editing the contract.

These tests pin the exit path rather than the log text: a pipeline that did not
apply is not a warning about the future, it is a wrong index now.
"""

from types import SimpleNamespace

import pytest
from elasticsearch import BadRequestError

from phase_providers import phase_pipelines
from phase_providers.phase_pipelines import PhasePipelines

# meta carries the status the exception's own __str__ reads; without it the
# phase's log call would raise instead of logging.
REFUSED = BadRequestError(
    "illegal_argument_exception", meta=SimpleNamespace(status=400), body={}
)


class FakeIngestClient:
    """Records the put_pipeline call, and can refuse it the way Elasticsearch does."""

    def __init__(self, refuse=False):
        self.refuse = refuse
        self.calls = []

    def delete_pipeline(self, id):
        return {"acknowledged": True}

    def put_pipeline(self, id, processors):
        self.calls.append((id, processors))
        if self.refuse:
            raise REFUSED
        return {"acknowledged": True}


def build_phase(monkeypatch, refuse):
    config = SimpleNamespace(
        name="out-of-service-orders-pipeline-000001",
        processors=[SimpleNamespace(script=SimpleNamespace(source="ctx.dot_number"))],
    )
    monkeypatch.setattr(
        phase_pipelines.file_utils, "load_from_project_file", lambda *a, **k: config
    )
    fake = FakeIngestClient(refuse=refuse)
    monkeypatch.setattr(phase_pipelines.client, "IngestClient", lambda es: fake)
    phase = PhasePipelines(
        object(),
        "AnyProject",
        "out-of-service-orders-ingestion-setup",
        SimpleNamespace(configurationDir="configuration"),
    )
    return phase, fake


def test_a_refused_pipeline_fails_the_run(monkeypatch):
    phase, _ = build_phase(monkeypatch, refuse=True)
    with pytest.raises(RuntimeError):
        phase.handle()


def test_the_failure_names_the_pipeline_so_the_operator_can_act(monkeypatch):
    # The recovery is pipeline-specific -- fix that JSON and rerun that step --
    # and a run applies several, so a message naming only the step leaves the
    # operator to work out which one Elasticsearch actually rejected.
    phase, _ = build_phase(monkeypatch, refuse=True)
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    assert "out-of-service-orders-pipeline-000001" in str(raised.value)


def test_an_accepted_pipeline_does_not_raise(monkeypatch):
    phase, fake = build_phase(monkeypatch, refuse=False)
    phase.handle()
    assert len(fake.calls) == 1


def test_no_pipeline_config_is_not_a_failure(monkeypatch):
    """Most steps have no pipelines.json and are not meant to."""
    monkeypatch.setattr(
        phase_pipelines.file_utils, "load_from_project_file", lambda *a, **k: None
    )
    phase = PhasePipelines(
        object(), "AnyProject", "any-step", SimpleNamespace(configurationDir="configuration")
    )
    phase.handle()
