"""Guards for two re-runs that hid or degraded earlier work without an error.

1. A phase run on its own (`--phase=index-populate`, say) on a later day than
   its step's index-create resolves to today's index, which does not exist.
   Elasticsearch auto-creates an index on first write
   (`action.auto_create_index` is true on the local cluster), so the load
   landed in an index with dynamic mappings and none of the configured
   analyzers. The writing phases now refuse instead.

2. measure_crash_lift.py writes every validation run through the
   chameleon-validation alias. Re-running that step's index-create, which a
   full DOT-Commercial run does, made a fresh empty index and moved the alias
   onto it, so every recorded run dropped out of view. An index-config marked
   `persistent` now keeps the index its alias already names.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from elasticsearch import NotFoundError

from phase_providers import (
    phase_entity_match,
    phase_index_creation,
    phase_index_mappings,
    phase_index_populate,
)
from phase_providers.phase_entity_match import PhaseEntityMatch
from phase_providers.phase_index_creation import PhaseindexCreate
from phase_providers.phase_index_mappings import PhaseIndexMappings
from phase_providers.phase_index_populate import PhaseIndexingPopulate
from utils.elasticsearch_utils import resolve_existing_index

PROJECT = SimpleNamespace(configurationDir="configuration")
TEMPLATE = "carriers-2026.11.08-000001"


class FakeIndices:
    """Existing indexes and alias membership, plus a record of every write."""

    def __init__(self, existing=(), aliases=None):
        self.existing = set(existing)
        self.aliases = aliases or {}
        self.created = []
        self.alias_actions = []
        self.mapped = []

    def get(self, index, **kwargs):
        prefix = index.rstrip("*")
        return {name: {} for name in self.existing if name.startswith(prefix)}

    def get_alias(self, name):
        if name not in self.aliases:
            raise NotFoundError("alias [{}] missing".format(name), None, None)
        return {index: {"aliases": {name: {}}} for index in self.aliases[name]}

    def create(self, index, **kwargs):
        self.existing.add(index)
        self.created.append(index)
        return {"acknowledged": True}

    def update_aliases(self, actions):
        self.alias_actions.append(actions)
        return {"acknowledged": True}

    def put_mapping(self, index, properties):
        self.mapped.append(index)
        return {"acknowledged": True}


def _loader(monkeypatch, module, files):
    monkeypatch.setattr(
        module.file_utils, "load_from_project_file", lambda project, d, step, name: files.get(name)
    )


# --- 1. a lone phase must not write into an index that does not exist ---


def test_resolve_existing_refuses_when_todays_index_was_never_created():
    with pytest.raises(RuntimeError) as raised:
        resolve_existing_index(FakeIndices(["carriers-2026.11.06-000001"]), TEMPLATE)
    message = str(raised.value)
    assert TEMPLATE in message
    assert "index-create" in message and "--step" in message


def test_resolve_existing_returns_todays_newest_generation():
    indices = FakeIndices([TEMPLATE, "carriers-2026.11.08-000002"])
    assert resolve_existing_index(indices, TEMPLATE) == "carriers-2026.11.08-000002"


def test_populate_refuses_before_loading_anything(monkeypatch):
    _loader(monkeypatch, phase_index_populate, {"index-config.json": SimpleNamespace(index=TEMPLATE, alias="c")})
    es = SimpleNamespace(indices=FakeIndices())
    with pytest.raises(RuntimeError, match="index-create"):
        PhaseIndexingPopulate(es, "DOT-Commercial", "carriers", PROJECT).handle()


def test_entity_match_refuses_before_sweeping(monkeypatch):
    sweep = "chameleon-candidates-2026.11.08-000001"
    _loader(
        monkeypatch,
        phase_entity_match,
        {"entity-match.json": SimpleNamespace(), "index-config.json": SimpleNamespace(index=sweep, alias="c")},
    )
    es = SimpleNamespace(indices=FakeIndices())
    with pytest.raises(RuntimeError, match="index-create"):
        PhaseEntityMatch(es, "DOT-Commercial", "chameleon-detection", PROJECT).handle()


def test_index_map_refuses_with_the_same_explanation(monkeypatch):
    mapping = SimpleNamespace(index=TEMPLATE, mappings=SimpleNamespace(properties=SimpleNamespace()))
    _loader(monkeypatch, phase_index_mappings, {"index-mappings.json": mapping})
    indices = FakeIndices()
    monkeypatch.setattr(phase_index_mappings.client, "IndicesClient", lambda es: indices)
    with pytest.raises(RuntimeError, match="index-create"):
        PhaseIndexMappings(object(), "DOT-Commercial", "carriers", PROJECT).handle()
    assert indices.mapped == []


# --- 2. a persistent results index keeps its alias ---

VALIDATION = "chameleon-validation-{now/d}-000001"
VALIDATION_ALIAS = "chameleon-validation-000001"
RECORDED = "chameleon-validation-2026.08.07-000001"


def _create(monkeypatch, indices, persistent=True):
    config = SimpleNamespace(index=VALIDATION, alias=VALIDATION_ALIAS, persistent=persistent)
    _loader(monkeypatch, phase_index_creation, {"index-config.json": config})
    monkeypatch.setattr(phase_index_creation.client, "IndicesClient", lambda es: indices)
    PhaseindexCreate(object(), "DOT-Commercial", "chameleon-validation", PROJECT).handle()


def test_a_persistent_index_keeps_the_index_its_alias_names(monkeypatch):
    indices = FakeIndices([RECORDED], aliases={VALIDATION_ALIAS: [RECORDED]})
    _create(monkeypatch, indices)
    assert indices.created == []
    assert indices.alias_actions == []


def test_a_persistent_index_is_created_when_its_alias_does_not_exist_yet(monkeypatch):
    indices = FakeIndices()
    _create(monkeypatch, indices)
    assert len(indices.created) == 1
    assert indices.alias_actions[-1][-1]["add"]["alias"] == VALIDATION_ALIAS


def test_a_non_persistent_index_still_moves_its_alias(monkeypatch):
    indices = FakeIndices([RECORDED], aliases={VALIDATION_ALIAS: [RECORDED]})
    _create(monkeypatch, indices, persistent=False)
    assert len(indices.created) == 1


def test_index_map_updates_the_kept_index_not_todays(monkeypatch):
    mapping = SimpleNamespace(index=VALIDATION, mappings=SimpleNamespace(properties=SimpleNamespace()))
    config = SimpleNamespace(index=VALIDATION, alias=VALIDATION_ALIAS, persistent=True)
    _loader(monkeypatch, phase_index_mappings, {"index-mappings.json": mapping, "index-config.json": config})
    indices = FakeIndices([RECORDED], aliases={VALIDATION_ALIAS: [RECORDED]})
    monkeypatch.setattr(phase_index_mappings.client, "IndicesClient", lambda es: indices)
    PhaseIndexMappings(object(), "DOT-Commercial", "chameleon-validation", PROJECT).handle()
    assert indices.mapped == [RECORDED]


def test_the_shipped_validation_index_is_persistent():
    # The guard only protects what is marked. measure_crash_lift.py and
    # measure_chameleon_shape.py both write their runs through this alias.
    path = Path("DOT-Commercial/configuration/chameleon-validation/index-config.json")
    assert json.loads(path.read_text(encoding="utf-8"))["persistent"] is True
