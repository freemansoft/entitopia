"""Predicting, and optionally clearing, the pipeline that blocks a stale enrich policy.

Every reload of a loaded cluster hit the same wall partway through: an enrich
policy still pointing at the previous day's index cannot be replaced while an
ingest pipeline uses it, so the step stopped after earlier steps had already
moved their aliases. The fix had to be done by hand, deleting the pipeline the
same step was about to recreate anyway.

These pin the two decisions that make the automatic version safe: that a block
is predicted before anything is written, and that a pipeline is only deleted
when the step that owns it will recreate it in the same run.
"""

from types import SimpleNamespace

from phase_providers import bound_pipelines

STALE = {"indices": ["crashes-2026.08.13-000001"], "match_field": "dot_number", "enrich_fields": ["x"]}
WANTED = {"indices": "crashes-2026.11.06-000001", "match_field": "dot_number", "enrich_fields": ["x"]}


def enrich(policy):
    return {"enrich": {"policy_name": policy, "field": "dot_number", "target_field": "t"}}


PIPELINES = {
    "carrier-pipeline": {"processors": [enrich("crashes-policy"), enrich("other-policy")]},
    "unrelated": {"processors": [{"set": {"field": "a", "value": 1}}]},
}

STEP_PHASES = ["enrichment-policies", "pipelines"]


def assess(**overrides):
    args = {"policy": "crashes-policy", "step": "carriers-ingestion-setup", "step_phases": STEP_PHASES,
            "wanted": WANTED, "existing": STALE, "pipelines": PIPELINES, "step_pipeline": "carrier-pipeline"}
    args.update(overrides)
    return bound_pipelines.assess(**args)


def test_finds_a_policy_used_inside_a_nested_processor():
    nested = {"p": {"processors": [{"foreach": {"field": "f", "processor": enrich("deep-policy")}}],
                    "on_failure": [enrich("fallback-policy")]}}
    assert bound_pipelines.pipelines_using_policy(nested, "deep-policy") == ["p"]
    assert bound_pipelines.pipelines_using_policy(nested, "fallback-policy") == ["p"]
    assert bound_pipelines.pipelines_using_policy(nested, "absent") == []


def test_a_stale_policy_held_by_this_steps_own_pipeline_is_blocked_and_fixable():
    blocked = assess()
    assert blocked.pipelines == ("carrier-pipeline",)
    assert blocked.fixable, blocked.reason


def test_no_block_when_the_policy_does_not_exist_yet():
    assert assess(existing=None) is None


def test_no_block_when_the_existing_policy_already_matches_config():
    # indices reads back as a list; config may give a bare string.
    assert assess(existing=dict(WANTED, indices=[WANTED["indices"]])) is None


def test_no_block_when_a_stale_policy_is_held_by_no_pipeline():
    # Deletion will succeed, so the phase replaces it without help.
    assert assess(pipelines={"unrelated": PIPELINES["unrelated"]}) is None


def test_not_fixable_when_another_step_owns_the_blocking_pipeline():
    blocked = assess(step_pipeline="some-other-pipeline")
    assert not blocked.fixable
    assert "carrier-pipeline" in blocked.reason


def test_not_fixable_when_the_step_will_not_recreate_its_pipeline():
    # --phase=enrichment-policies alone: deleting the pipeline would leave the
    # index it feeds with no pipeline at all.
    blocked = assess(step_phases=["enrichment-policies"])
    assert not blocked.fixable
    assert "pipelines" in blocked.reason


def test_not_fixable_when_the_pipeline_phase_runs_before_the_policies():
    assert not assess(step_phases=["pipelines", "enrichment-policies"]).fixable


class FakeIngest:
    def __init__(self, pipelines):
        self.pipelines = dict(pipelines)
        self.deleted = []

    def get_pipeline(self):
        return self.pipelines

    def delete_pipeline(self, id):
        self.deleted.append(id)
        self.pipelines.pop(id)


def project_config(phases=STEP_PHASES):
    return SimpleNamespace(configurationDir="configuration",
                           steps=[SimpleNamespace(name="carriers-ingestion-setup", phases=phases)])


def test_release_deletes_only_the_pipelines_holding_the_policy(monkeypatch):
    monkeypatch.setattr(bound_pipelines.file_utils, "load_from_project_file",
                        lambda *a, **k: SimpleNamespace(name="carrier-pipeline"))
    ingest = FakeIngest(PIPELINES)
    released = bound_pipelines.release(ingest, "DOT-Commercial", project_config(), "carriers-ingestion-setup",
                                       "crashes-policy", STALE, WANTED)
    assert released is True
    assert ingest.deleted == ["carrier-pipeline"]


def test_release_refuses_when_the_step_will_not_recreate_the_pipeline(monkeypatch):
    monkeypatch.setattr(bound_pipelines.file_utils, "load_from_project_file",
                        lambda *a, **k: SimpleNamespace(name="carrier-pipeline"))
    ingest = FakeIngest(PIPELINES)
    released = bound_pipelines.release(ingest, "DOT-Commercial", project_config(["enrichment-policies"]),
                                       "carriers-ingestion-setup", "crashes-policy", STALE, WANTED)
    assert released is False
    assert ingest.deleted == []


def blocked(fixable):
    return bound_pipelines.BlockedPolicy(step="s", policy="p", found=STALE, wanted=WANTED,
                                         pipelines=("x",), fixable=fixable, reason="" if fixable else "why")


def test_preflight_lets_a_clean_run_proceed():
    assert bound_pipelines.preflight_exit_code([], fix=False) is None


def test_preflight_stops_a_blocked_run_unless_asked_to_fix():
    assert bound_pipelines.preflight_exit_code([blocked(True)], fix=False) == 2
    assert bound_pipelines.preflight_exit_code([blocked(True)], fix=True) is None


def test_preflight_stops_an_unfixable_block_even_when_asked_to_fix():
    assert bound_pipelines.preflight_exit_code([blocked(True), blocked(False)], fix=True) == 2
