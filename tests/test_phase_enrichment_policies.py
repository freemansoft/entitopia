"""A policy rebuild that cannot succeed must fail the run, not log and continue.

The phase already detected both ways enrichment goes quietly wrong — a policy
bound to a pipeline whose definition has drifted to an earlier day's index, and
a policy that executes against unrefreshed data and produces an empty enrich
index. It logged ERROR for each and carried on, so the step still exited 0.

That is the failure this project keeps paying for: a reload where every phase
reports success and the carriers come out enriched from the wrong source.
Measured on a real reload — six policies still pointing at indexes twelve days
superseded, six ERROR lines, exit code 0, and a carriers index built from them.

These tests pin the exit path rather than the log text, because the log was
never the problem.
"""

from types import SimpleNamespace

import pytest
from elasticsearch import BadRequestError, ConflictError

from phase_providers import phase_enrichment_policies
from phase_providers.phase_enrichment_policies import PhaseEnrichmentPolicies

# meta carries the status the exception's own __str__ reads; without it the
# phase's log call raises instead of logging, which would make these tests pass
# for the wrong reason.
BOUND = ConflictError("status_exception", meta=SimpleNamespace(status=409), body={})
EXISTS = BadRequestError(
    "resource_already_exists_exception", meta=SimpleNamespace(status=400), body={}
)


class FakeEnrichClient:
    """An enrich client whose policies cannot be deleted, as on any loaded cluster.

    Mirrors the real shape that causes the bug: delete is refused because a
    pipeline is bound, put then reports the policy already exists, and what
    get_policy reads back is whatever was there before — not what config asked
    for.
    """

    def __init__(self, existing):
        self.existing = existing
        self.executed = []
        self.timeout = None

    def options(self, request_timeout):
        # The real client returns a configured copy; returning self keeps the
        # recorded call list in one place.
        self.timeout = request_timeout
        return self

    def delete_policy(self, name):
        raise BOUND

    def put_policy(self, name, match):
        raise EXISTS

    def get_policy(self, name):
        return {"policies": [{"config": {"match": self.existing[name]}}]}

    def execute_policy(self, name, wait_for_completion):
        self.executed.append(name)


class FakeEs:
    """Counts used only to compare a source index against its enrich index."""

    def __init__(self, counts):
        self.counts = counts

    def count(self, index):
        return {"count": self.counts.get(index, 0)}


def build_phase(monkeypatch, configured, existing, counts):
    monkeypatch.setattr(
        phase_enrichment_policies.file_utils,
        "load_from_project_file",
        lambda *a, **k: [
            SimpleNamespace(name=name, match=SimpleNamespace(**match))
            for name, match in configured.items()
        ],
    )
    fake_enrich = FakeEnrichClient(existing)
    monkeypatch.setattr(
        phase_enrichment_policies.client, "EnrichClient", lambda es: fake_enrich
    )
    phase = PhaseEnrichmentPolicies(
        FakeEs(counts),
        "AnyProject",
        "any-step",
        SimpleNamespace(configurationDir="configuration"),
    )
    return phase, fake_enrich


def policy(indices):
    return {"indices": indices, "match_field": "dot_number", "enrich_fields": ["dot_number"]}


def test_a_policy_pointing_at_the_wrong_index_fails_the_run(monkeypatch):
    """The exact shape observed: config wants today, the bound policy has an older day."""
    phase, fake = build_phase(
        monkeypatch,
        configured={"crashes-enrichment-policy": policy("crashes-2026.08.13-000001")},
        existing={"crashes-enrichment-policy": policy(["crashes-2026.08.01-000001"])},
        counts={},
    )
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    assert "crashes-enrichment-policy" in str(raised.value)
    assert fake.executed == [], "a policy that disagrees with config must not be executed"


def test_a_matching_policy_is_executed_and_does_not_raise(monkeypatch):
    phase, fake = build_phase(
        monkeypatch,
        configured={"crashes-enrichment-policy": policy("crashes-2026.08.13-000001")},
        existing={"crashes-enrichment-policy": policy(["crashes-2026.08.13-000001"])},
        counts={"crashes-2026.08.13-000001": 10, ".enrich-crashes-enrichment-policy": 10},
    )
    phase.handle()
    assert fake.executed == ["crashes-enrichment-policy"]


def test_an_empty_enrich_index_over_a_populated_source_fails_the_run(monkeypatch):
    """Executing against unrefreshed data succeeds and enriches nothing.

    Downstream nothing errors: the enriched documents simply lack their fields
    and a sweep over them returns a plausible zero.
    """
    phase, _ = build_phase(
        monkeypatch,
        configured={"crashes-enrichment-policy": policy("crashes-2026.08.13-000001")},
        existing={"crashes-enrichment-policy": policy(["crashes-2026.08.13-000001"])},
        counts={"crashes-2026.08.13-000001": 333120, ".enrich-crashes-enrichment-policy": 0},
    )
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    assert "crashes-enrichment-policy" in str(raised.value)


def test_every_failing_policy_is_reported_not_just_the_first(monkeypatch):
    """The real run had six. Aborting on the first would hide the other five.

    Each policy is still attempted, so one failure cannot mask the health of
    the rest; the run fails once at the end with the full list.
    """
    configured = {
        "a-enrichment-policy": policy("a-2026.08.13-000001"),
        "b-enrichment-policy": policy("b-2026.08.13-000001"),
    }
    existing = {
        "a-enrichment-policy": policy(["a-2026.08.01-000001"]),
        "b-enrichment-policy": policy(["b-2026.08.01-000001"]),
    }
    phase, _ = build_phase(monkeypatch, configured, existing, counts={})
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    message = str(raised.value)
    assert "a-enrichment-policy" in message and "b-enrichment-policy" in message


def test_no_configured_policies_is_not_a_failure(monkeypatch):
    """A step with no enrichment-policies.json is normal, not an error."""
    monkeypatch.setattr(
        phase_enrichment_policies.file_utils, "load_from_project_file", lambda *a, **k: None
    )
    phase = PhaseEnrichmentPolicies(
        FakeEs({}), "AnyProject", "any-step", SimpleNamespace(configurationDir="configuration")
    )
    phase.handle()


def test_policy_execution_is_given_a_timeout_long_enough_for_a_large_source(monkeypatch):
    """A 9.6M-document policy outlives elasticsearch-py's default request timeout.

    Observed: executing the inspections-per-unit policy returned "Connection
    timed out" after the client's default while Elasticsearch went on to
    finish the job successfully. The run then reported a failure that had not
    happened, which is the mirror image of the silent-success problem and just
    as misleading -- and before this phase raised, it was swallowed entirely.
    """
    phase, fake = build_phase(
        monkeypatch,
        configured={"big-enrichment-policy": policy("big-2026.08.13-000001")},
        existing={"big-enrichment-policy": policy(["big-2026.08.13-000001"])},
        counts={"big-2026.08.13-000001": 9632353, ".enrich-big-enrichment-policy": 9632353},
    )
    phase.handle()
    assert fake.timeout == phase_enrichment_policies.EXECUTE_TIMEOUT_SECONDS
    assert fake.timeout >= 1800, "a multi-million-document enrich build needs far more than the default"
