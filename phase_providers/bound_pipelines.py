"""Find, before a run writes anything, the enrich policies a pipeline will stop it from replacing.

Every reload of a loaded cluster hit this partway through. Each run points its
enrich policies at that day's index, but Elasticsearch cannot change a policy
in place, and cannot delete one while an ingest pipeline uses it. So the
enrichment-policies phase stopped, rightly, rather than enrich from the old
index, but only after earlier steps had already created new indexes and moved
their aliases. The cure was always the same hand-run command: delete the
pipeline, which the same step's `pipelines` phase recreates moments later.

This module makes that predictable and, when asked, automatic:

- `predict_blocked` reads the cluster and the run's config, writes nothing, and
  lists every policy the run will be unable to replace. execute_project.py
  calls it before the first phase.
- `release` deletes the pipelines holding one policy, and is the only path that
  does. It refuses unless the step that owns each pipeline will recreate it
  later in the same run, so an index is never left feeding a pipeline that no
  longer exists.
"""

import json
import logging
from dataclasses import dataclass

from elasticsearch import NotFoundError

from utils import elasticsearch_utils, file_utils

# Fields of a policy's `match` block that decide what the enrich index contains.
# Elasticsearch echoes a normalized policy back, so only these are compared.
POLICY_IDENTITY_KEYS = ("indices", "match_field", "enrich_fields")

# Exit status when a predicted block stops the run: distinct from a crash (1)
# so a wrapper script can tell "refused before writing" from "failed midway".
EXIT_BLOCKED = 2


@dataclass(frozen=True)
class BlockedPolicy:
    """One policy the run cannot replace, and whether clearing it is safe.

    `reason` is empty when fixable and otherwise says why not, because the
    unfixable cases differ in what the operator has to do next.
    """

    step: str
    policy: str
    found: dict
    wanted: dict
    pipelines: tuple
    fixable: bool
    reason: str


def _as_list(value):
    return [value] if isinstance(value, str) else value


def differing_keys(wanted, found):
    """Identity keys on which an existing policy disagrees with config.

    `indices` accepts a bare string in config but always reads back as a list,
    so both sides are normalized first; otherwise every policy would differ.
    """
    return [key for key in POLICY_IDENTITY_KEYS if _as_list(wanted.get(key)) != _as_list(found.get(key))]


def _uses_policy(node, policy):
    if isinstance(node, dict):
        enrich = node.get("enrich")
        if isinstance(enrich, dict) and enrich.get("policy_name") == policy:
            return True
        return any(_uses_policy(value, policy) for value in node.values())
    if isinstance(node, list):
        return any(_uses_policy(item, policy) for item in node)
    return False


def pipelines_using_policy(pipelines, policy):
    """Names of the pipelines with an enrich processor naming the policy, at any depth.

    Searched recursively because a processor can sit inside `foreach` or
    `on_failure`, and Elasticsearch refuses the delete for those too. Missing
    one would make a fix that looks complete fail at the delete.
    """
    return sorted(name for name, body in pipelines.items() if _uses_policy(body, policy))


def assess(policy, step, step_phases, wanted, existing, pipelines, step_pipeline):
    """Whether replacing `policy` will be blocked, and whether clearing the block is safe.

    None means the phase can replace it unaided: the policy does not exist,
    already matches config, or is stale but held by no pipeline, so the delete
    succeeds. A block is fixable only when every holding pipeline is this
    step's own and the step runs `pipelines` after `enrichment-policies`,
    which is what guarantees the deleted pipeline comes back.
    """
    if existing is None or not differing_keys(wanted, existing):
        return None
    holders = tuple(pipelines_using_policy(pipelines, policy))
    if not holders:
        return None

    foreign = [name for name in holders if name != step_pipeline]
    phases = list(step_phases)
    recreates = (
        "pipelines" in phases
        and "enrichment-policies" in phases
        and phases.index("pipelines") > phases.index("enrichment-policies")
    )
    if foreign:
        reason = "held by {}, which step {} does not define, so nothing in this run would recreate it".format(
            ", ".join(foreign), step
        )
    elif not recreates:
        reason = "step {} does not run its pipelines phase after enrichment-policies in this run".format(step)
    else:
        reason = ""
    return BlockedPolicy(step, policy, existing, wanted, holders, not reason, reason)


def _step_pipeline(project, project_config, step):
    config = file_utils.load_from_project_file(project, project_config.configurationDir, step, "pipelines.json")
    return getattr(config, "name", None) if config else None


def _step_phases(project_config, step):
    for one_step in getattr(project_config, "steps", []):
        if one_step.name == step:
            return list(one_step.phases)
    return []


def _indexes_this_run_creates(project, project_config):
    """The configured index names of every step in this run that has index-create."""
    created = set()
    for one_step in getattr(project_config, "steps", []):
        if "index-create" not in one_step.phases:
            continue
        config = file_utils.load_from_project_file(
            project, project_config.configurationDir, one_step.name, "index-config.json"
        )
        if config:
            created.add(config.index)
    return created


def configured_policies(es, project, project_config, step):
    """(name, match dict) for each policy the step configures, with the index the run will ask for.

    The policy phase reads the newest generation of today's index, so when an
    earlier step in this same run creates that index, the policy will be asked
    for the generation that create is about to make, not the one that exists
    now. A same-day reload is where they differ: predicting the existing
    generation would miss the block and let the run stop midway.
    """
    configs = file_utils.load_from_project_file(
        project, project_config.configurationDir, step, "enrichment-policies.json"
    )
    created = _indexes_this_run_creates(project, project_config)
    for config in configs or []:
        template = config.match.indices
        resolve = (
            elasticsearch_utils.resolve_new_index
            if template in created
            else elasticsearch_utils.resolve_current_index
        )
        config.match.indices = resolve(es.indices, template)
        yield config.name, json.loads(json.dumps(config.match, default=vars))


def _existing_match(es, name):
    try:
        policies = es.enrich.get_policy(name=name).get("policies") or []
    except NotFoundError:
        return None
    return policies[0].get("config", {}).get("match") if policies else None


def predict_blocked(es, project, project_config):
    """Every policy this run will be unable to replace. Reads only."""
    pipelines = dict(es.ingest.get_pipeline())
    blocked = []
    for one_step in project_config.steps:
        if "enrichment-policies" not in one_step.phases:
            continue
        step_pipeline = _step_pipeline(project, project_config, one_step.name)
        for name, wanted in configured_policies(es, project, project_config, one_step.name):
            found = assess(name, one_step.name, one_step.phases, wanted, _existing_match(es, name),
                           pipelines, step_pipeline)
            if found:
                blocked.append(found)
    return blocked


def preflight_exit_code(blocked, fix):
    """None to let the run proceed, or the status to stop it with before anything is written."""
    if not blocked:
        return None
    if not fix or any(not b.fixable for b in blocked):
        return EXIT_BLOCKED
    return None


def report(blocked, fix, logger=None):
    """Log each predicted block, and what the operator can do about it."""
    logger = logger or logging.getLogger(__name__)
    for b in blocked:
        detail = "policy {} (step {}) points at {} but this run needs {}; held by pipeline {}".format(
            b.policy, b.step, _as_list(b.found.get("indices")), _as_list(b.wanted.get("indices")),
            ", ".join(b.pipelines))
        if not b.fixable:
            logger.error("Cannot replace %s. Not fixable automatically: %s.", detail, b.reason)
        elif fix:
            logger.warning("Will release %s: the pipeline is deleted and its step recreates it.", detail)
        else:
            logger.error("Cannot replace %s. Rerun with --fix-bound-pipelines to delete the pipeline "
                         "and let its step recreate it.", detail)


def release(ingest_client, project, project_config, step, policy, found, wanted, logger=None):
    """Delete the pipelines holding `policy`, only if the run will recreate them.

    Returns whether the block was cleared. Re-assessed here rather than trusted
    from the preflight, because the phase can also be reached without it (a
    test, a caller other than execute_project.py), and a wrong delete leaves an
    index whose default pipeline no longer exists.
    """
    logger = logger or logging.getLogger(__name__)
    blocked = assess(policy, step, _step_phases(project_config, step), wanted, found,
                     dict(ingest_client.get_pipeline()), _step_pipeline(project, project_config, step))
    if blocked is None:
        return True
    if not blocked.fixable:
        logger.error("Not releasing policy %s: %s.", policy, blocked.reason)
        return False
    for name in blocked.pipelines:
        ingest_client.delete_pipeline(id=name)
        logger.warning("Deleted pipeline %s so policy %s can be replaced; step %s recreates it.",
                       name, policy, step)
    return True
