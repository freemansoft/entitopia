"""A second run on the same day must get its own index, not write into the first.

Every index-config.json names its index `<step>-{now/d}-000001`, so two runs on
one day resolve to the same name. The second create failed with
resource_already_exists_exception, which index-create logged as a warning, and
every later phase then wrote into the earlier run's index. For a sweep that
meant the new pairs overwrote matching _ids and every pair the new run no
longer emitted stayed behind, so the index held a union of two sweeps under
one name. Nothing errored. Found 2026-11-07 when a re-sweep had to be
compared against one made that morning; the earlier index had to be cloned
away by hand first.

The fix treats the trailing number as a generation. Creating takes the next
free one; every other phase reads the newest one for today, which is the one
this run's create just made.
"""

from types import SimpleNamespace

import pytest
from elasticsearch import BadRequestError

from phase_providers import bound_pipelines, phase_index_creation
from phase_providers.phase_index_creation import PhaseindexCreate
from utils.elasticsearch_utils import resolve_current_index, resolve_new_index

TODAY = "chameleon-candidates-2026.11.08-000001"


class FakeIndices:
    """Answers wildcard index lookups from a fixed set of existing names."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.alias_actions = []

    def get(self, index, **kwargs):
        prefix = index.rstrip("*")
        return {name: {} for name in self.existing if name.startswith(prefix)}

    def create(self, index, **kwargs):
        if index in self.existing:
            raise BadRequestError(
                "resource_already_exists_exception", meta=SimpleNamespace(status=400), body={}
            )
        self.existing.add(index)
        self.created.append(index)
        return {"acknowledged": True}

    def get_alias(self, name):
        return {}

    def update_aliases(self, actions):
        self.alias_actions.append(actions)
        return {"acknowledged": True}


def test_the_first_run_of_the_day_keeps_the_configured_name():
    assert resolve_new_index(FakeIndices(), TODAY) == TODAY


def test_a_second_run_the_same_day_gets_the_next_generation():
    indices = FakeIndices([TODAY])
    assert resolve_new_index(indices, TODAY) == "chameleon-candidates-2026.11.08-000002"


def test_the_next_generation_follows_the_highest_not_the_count():
    # A generation deleted by hand leaves a gap; reusing it would be fine, but
    # following the highest keeps names in creation order, which is the order
    # an operator reads them in.
    indices = FakeIndices([TODAY, "chameleon-candidates-2026.11.08-000003"])
    assert resolve_new_index(indices, TODAY) == "chameleon-candidates-2026.11.08-000004"


def test_other_days_and_hand_made_copies_do_not_count():
    # A clone kept aside for comparison matches the wildcard lookup but is not
    # a generation; another day's index shares the step but not the date.
    indices = FakeIndices(
        [
            "chameleon-candidates-2026.11.07-000005",
            "chameleon-candidates-2026.11.08-pretiebreak-000009",
        ]
    )
    assert resolve_new_index(indices, TODAY) == TODAY


def test_the_date_placeholder_is_resolved_before_the_lookup():
    name = resolve_new_index(FakeIndices(), "chameleon-candidates-{now/d}-000001")
    assert "{now/d}" not in name
    assert name.startswith("chameleon-candidates-2") and name.endswith("-000001")


def test_a_name_with_no_generation_to_advance_refuses_rather_than_reusing():
    # Without a trailing number there is no next name, and reusing the
    # existing index is the defect itself.
    with pytest.raises(RuntimeError, match="already exists"):
        resolve_new_index(FakeIndices(["fixed-name"]), "fixed-name")


def test_later_phases_read_the_newest_generation():
    indices = FakeIndices([TODAY, "chameleon-candidates-2026.11.08-000002"])
    assert resolve_current_index(indices, TODAY) == "chameleon-candidates-2026.11.08-000002"


def test_later_phases_fall_back_to_the_configured_name_when_none_exists():
    # Unchanged from before: a phase run before its create targets the name
    # the config gives, and fails or auto-creates exactly as it always did.
    assert resolve_current_index(FakeIndices(), TODAY) == TODAY


def _create_phase(monkeypatch, indices):
    config = SimpleNamespace(index="chameleon-candidates-{now/d}-000001", alias="chameleon-candidates-000001")
    monkeypatch.setattr(
        phase_index_creation.file_utils,
        "load_from_project_file",
        lambda *a, **k: config if a[-1] == "index-config.json" else None,
    )
    monkeypatch.setattr(phase_index_creation.client, "IndicesClient", lambda es: indices)
    return PhaseindexCreate(object(), "DOT-Commercial", "chameleon-detection", SimpleNamespace(configurationDir="configuration"))


def test_a_same_day_rerun_creates_a_fresh_index_and_moves_the_alias(monkeypatch):
    indices = FakeIndices()
    _create_phase(monkeypatch, indices).handle()
    _create_phase(monkeypatch, indices).handle()
    first, second = indices.created
    assert second == first[: -len("000001")] + "000002"
    assert indices.alias_actions[-1][-1] == {"add": {"index": second, "alias": "chameleon-candidates-000001"}}


def test_a_refused_create_fails_the_run(monkeypatch):
    # With the name collision gone, a refused create means bad settings, and
    # carrying on would let the next phase auto-create the index with dynamic
    # mappings and none of the analyzers.
    class Refusing(FakeIndices):
        def create(self, index, **kwargs):
            raise BadRequestError("illegal_argument_exception", meta=SimpleNamespace(status=400), body={})

    with pytest.raises(RuntimeError, match="chameleon-candidates-"):
        _create_phase(monkeypatch, Refusing()).handle()


def _policy_config(monkeypatch, steps):
    """A project whose crashes step creates crashes-{now/d}-000001 and whose
    carriers-ingestion-setup step builds a policy over it."""
    policy = SimpleNamespace(
        name="crashes-policy",
        match=SimpleNamespace(indices="crashes-2026.11.08-000001", match_field="dot_number", enrich_fields=["x"]),
    )

    def load(project, config_dir, step, filename):
        if filename == "enrichment-policies.json" and step == "carriers-ingestion-setup":
            return [policy]
        if filename == "index-config.json" and step == "crashes":
            return SimpleNamespace(index="crashes-2026.11.08-000001", alias="crashes-000001")
        return None

    monkeypatch.setattr(bound_pipelines.file_utils, "load_from_project_file", load)
    return SimpleNamespace(configurationDir="configuration", steps=steps)


def _policy_indices(monkeypatch, steps, existing):
    config = _policy_config(monkeypatch, steps)
    es = SimpleNamespace(indices=FakeIndices(existing))
    return {name: match["indices"] for name, match in bound_pipelines.configured_policies(
        es, "DOT-Commercial", config, "carriers-ingestion-setup")}


def test_the_preflight_predicts_the_generation_this_runs_create_will_make(monkeypatch):
    # A same-day full reload: crashes will create -000002 before the policy
    # phase runs, so the policy will be asked for -000002. Predicting -000001
    # would miss the block and let the run stop midway, which is what the
    # preflight exists to prevent.
    steps = [
        SimpleNamespace(name="crashes", phases=["index-create", "index-map", "index-populate"]),
        SimpleNamespace(name="carriers-ingestion-setup", phases=["enrichment-policies", "pipelines"]),
    ]
    found = _policy_indices(monkeypatch, steps, ["crashes-2026.11.08-000001"])
    assert found == {"crashes-policy": "crashes-2026.11.08-000002"}


def test_the_preflight_uses_the_newest_existing_index_when_this_run_creates_none(monkeypatch):
    steps = [SimpleNamespace(name="carriers-ingestion-setup", phases=["enrichment-policies", "pipelines"])]
    found = _policy_indices(
        monkeypatch, steps, ["crashes-2026.11.08-000001", "crashes-2026.11.08-000002"]
    )
    assert found == {"crashes-policy": "crashes-2026.11.08-000002"}
