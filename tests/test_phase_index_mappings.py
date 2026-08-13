"""A mapping Elasticsearch refuses must fail the run, not be logged at INFO.

The phase caught BadRequestError and logged it at INFO -- below the level
anyone scans a long run for -- then let the load proceed. The result is an
index populated under whatever mapping it already had, a step that exits 0, and
a defect that only surfaces when a query needs the mapping that never applied.

Measured on the real case that prompted this: `carriers` already existed with
`out_of_service_orders` as an object, and Elasticsearch cannot convert an
existing object field to `nested`. Rerunning the step would have refused the
mapping, logged one INFO line, indexed 2,085,534 documents anyway, and reported
success -- while the chameleon sweep's nested selector went on failing with
"[nested] failed to find nested object under path".

These tests pin the exit path rather than the log text, because a mapping that
did not apply is not a warning about the future; it is a wrong index now.
"""

from types import SimpleNamespace

import pytest
from elasticsearch import BadRequestError

from phase_providers import phase_index_mappings
from phase_providers.phase_index_mappings import PhaseIndexMappings

# meta carries the status the exception's own __str__ reads; without it the
# phase's log call would raise instead of logging.
REFUSED = BadRequestError(
    "illegal_argument_exception", meta=SimpleNamespace(status=400), body={}
)


class FakeIndicesClient:
    """Records the put_mapping call, and can refuse it the way Elasticsearch does."""

    def __init__(self, refuse=False):
        self.refuse = refuse
        self.calls = []

    def put_mapping(self, index, properties):
        self.calls.append((index, properties))
        if self.refuse:
            raise REFUSED
        return {"acknowledged": True}


def build_phase(monkeypatch, refuse):
    config = SimpleNamespace(
        index="carriers-2026.08.13-000001",
        mappings=SimpleNamespace(
            properties=SimpleNamespace(out_of_service_orders=SimpleNamespace(type="nested"))
        ),
    )
    monkeypatch.setattr(
        phase_index_mappings.file_utils, "load_from_project_file", lambda *a, **k: config
    )
    fake = FakeIndicesClient(refuse=refuse)
    monkeypatch.setattr(phase_index_mappings.client, "IndicesClient", lambda es: fake)
    phase = PhaseIndexMappings(
        object(), "AnyProject", "carriers", SimpleNamespace(configurationDir="configuration")
    )
    return phase, fake


def test_a_refused_mapping_fails_the_run(monkeypatch):
    """The object-to-nested case: refused, previously logged at INFO, run exited 0."""
    phase, _ = build_phase(monkeypatch, refuse=True)
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    message = str(raised.value)
    assert "carriers-2026.08.13-000001" in message


def test_an_accepted_mapping_does_not_raise(monkeypatch):
    phase, fake = build_phase(monkeypatch, refuse=False)
    phase.handle()
    assert len(fake.calls) == 1


def test_the_failure_names_the_index_so_the_operator_can_act(monkeypatch):
    """The recovery is index-specific -- delete that index and rerun the step.

    A message naming only the step would leave the operator to work out which
    dated index is wrong, which is the part that is easy to get wrong at the
    end of a multi-hour run.
    """
    phase, _ = build_phase(monkeypatch, refuse=True)
    with pytest.raises(RuntimeError) as raised:
        phase.handle()
    assert "carriers-2026.08.13-000001" in str(raised.value)


def test_no_mapping_config_is_not_a_failure(monkeypatch):
    """A step with no index-mappings.json relies on dynamic mapping by design."""
    monkeypatch.setattr(
        phase_index_mappings.file_utils, "load_from_project_file", lambda *a, **k: None
    )
    phase = PhaseIndexMappings(
        object(), "AnyProject", "any-step", SimpleNamespace(configurationDir="configuration")
    )
    phase.handle()
