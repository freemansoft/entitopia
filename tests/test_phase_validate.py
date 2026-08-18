"""The validate phase: what it refuses to let start, and what it reports first.

These cover the behaviours that matter rather than the plumbing — that every
finding in a tier is reported together, that a later tier does not run once an
earlier one failed, and that the phase raises rather than logging and
continuing. The last is the repo's own rule: a phase raises when it cannot fix
a problem, and this one never can.
"""

import json
from types import SimpleNamespace

import pytest

from phase_providers.phase_validate import ConfigurationInvalid, PhaseValidate


class _FakeIndices:
    def __init__(self, mapping):
        self._mapping = mapping
        self.calls = []

    def get_mapping(self, index=None):
        self.calls.append(index)
        return self._mapping


class _FakeES:
    def __init__(self, mapping=None):
        self.indices = _FakeIndices(
            mapping
            if mapping is not None
            else {
                "idx": {
                    "mappings": {
                        "properties": {
                            "key_col": {"type": "keyword"},
                            "name_col": {
                                "type": "text",
                                "fields": {"phonetic": {"type": "text"}},
                            },
                        }
                    }
                }
            }
        )


def _valid_entity_match():
    return {
        "source_index": "idx",
        "entity": {"key": "key_col"},
        "population": {"mode": "all-entities", "sort_field": "key_col"},
        "candidates": {"max_candidates": 100, "seed_signals": ["name-phonetic"]},
        "signals": [
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["name_col"],
                "subfield": "phonetic",
            }
        ],
        "scoring": {"min_total_score": 0.5, "min_signals": 1},
    }


def _project(tmp_path, entity_match, step="analysis"):
    """Write a minimal project tree and return a phase bound to it."""
    step_dir = tmp_path / "proj" / "configuration" / step
    step_dir.mkdir(parents=True)
    (step_dir / "entity-match.json").write_text(json.dumps(entity_match))
    return PhaseValidate(
        es=_FakeES(),
        project=str(tmp_path / "proj"),
        one_step=step,
        project_config=SimpleNamespace(configurationDir="configuration"),
    )


def test_a_clean_config_does_not_raise(tmp_path):
    _project(tmp_path, _valid_entity_match()).handle()


def test_a_schema_failure_raises(tmp_path):
    raw = _valid_entity_match()
    raw["signals"][0]["max_shared_carriers"] = 5
    with pytest.raises(ConfigurationInvalid, match="schema"):
        _project(tmp_path, raw).handle()


def test_a_coherence_failure_raises(tmp_path):
    raw = _valid_entity_match()
    raw["candidates"]["seed_signals"].append("shared-token")
    with pytest.raises(ConfigurationInvalid, match="coherence"):
        _project(tmp_path, raw).handle()


def test_a_liveness_failure_raises(tmp_path):
    raw = _valid_entity_match()
    raw["entity"]["key"] = "no_such_column"
    with pytest.raises(ConfigurationInvalid, match="liveness"):
        _project(tmp_path, raw).handle()


def test_a_later_tier_does_not_run_once_an_earlier_one_failed(tmp_path):
    """Tier 3 asks the cluster about paths a tier-1 failure may mean are junk.

    Running it anyway buries the real problem under a page of findings that are
    consequences of it. Proven by the mapping never being fetched.
    """
    raw = _valid_entity_match()
    raw["signals"][0]["max_shared_carriers"] = 5
    phase = _project(tmp_path, raw)
    with pytest.raises(ConfigurationInvalid, match="schema"):
        phase.handle()
    assert phase.es.indices.calls == []


def test_every_finding_in_a_tier_is_reported_not_just_the_first(tmp_path, caplog):
    raw = _valid_entity_match()
    raw["candidates"]["seed_signals"] = ["nope-one", "nope-two"]
    phase = _project(tmp_path, raw)
    with caplog.at_level("ERROR"), pytest.raises(ConfigurationInvalid):
        phase.handle()
    assert sum("nope-one" in r.message for r in caplog.records) == 1
    assert sum("nope-two" in r.message for r in caplog.records) == 1


def test_an_unparseable_file_is_a_finding_not_a_crash(tmp_path):
    step_dir = tmp_path / "proj" / "configuration" / "analysis"
    step_dir.mkdir(parents=True)
    (step_dir / "entity-match.json").write_text("{ not json")
    phase = PhaseValidate(
        es=_FakeES(),
        project=str(tmp_path / "proj"),
        one_step="analysis",
        project_config=SimpleNamespace(configurationDir="configuration"),
    )
    with pytest.raises(ConfigurationInvalid, match="schema"):
        phase.handle()


def test_a_step_with_no_config_files_warns_rather_than_failing(tmp_path, caplog):
    # A step may legitimately carry no configuration of these kinds. Failing
    # would make `validate` unusable in `all_phases` for a whole project.
    (tmp_path / "proj" / "configuration" / "empty").mkdir(parents=True)
    phase = PhaseValidate(
        es=_FakeES(),
        project=str(tmp_path / "proj"),
        one_step="empty",
        project_config=SimpleNamespace(configurationDir="configuration"),
    )
    with caplog.at_level("WARNING"):
        phase.handle()
    assert any("nothing to validate" in r.message for r in caplog.records)


def test_a_step_without_entity_match_still_validates_its_other_files(tmp_path):
    # An ingestion step carries index-config and no entity-match; tiers 2 and 3
    # have nothing to say, and tier 1 must still apply.
    step_dir = tmp_path / "proj" / "configuration" / "loader"
    step_dir.mkdir(parents=True)
    (step_dir / "index-config.json").write_text(
        json.dumps({"alias": "a-000001", "index": "a-000001", "id_field": "x"})
    )
    phase = PhaseValidate(
        es=_FakeES(),
        project=str(tmp_path / "proj"),
        one_step="loader",
        project_config=SimpleNamespace(configurationDir="configuration"),
    )
    # id_field without source is the dependentRequired rule from Task 1.
    with pytest.raises(ConfigurationInvalid, match="schema"):
        phase.handle()
