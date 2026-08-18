# Metrics Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project declare what it measures about its own pairs in JSON, so a new dataset gets precision measurement without writing Python.

**Architecture:** A closed predicate menu evaluated against pair documents, three metric kinds built on it, and a runner that produces the same metric record `utils.sweep_compare.compare()` already consumes. The existing hand-written implementation stays until the config-driven one reproduces its numbers exactly on the real pair population.

**Tech Stack:** Python 3.11+, jsonschema 4.26.0, Elasticsearch 9.4.1, pytest, ruff.

This is Plan 3 of five, covering rollout step 8 of [the spec](../specs/2026-08-16-config-driven-analysis-portability-design.md). It depends on [Plan 1](2026-08-16-matcher-generalization.md) for the pair document shape and [Plan 2](2026-08-17-config-validation.md) for the schema machinery.

## Global Constraints

- **Everything runs from `.venv`.** Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/python -m ruff check .`
- **`ruff check .` must print `All checks passed!`** before any commit.
- **Comments explain why, not what.**
- **Never name a real flagged entity.** Metric _names_ containing `vin` are fine — they name a metric, not a carrier.
- **Config objects:** schema validation reads plain dicts via `json.load`; see Plan 2's `utils/config_schema.py`.
- **Do not change matcher behavior.** This plan reads pair documents; it must not alter how they are produced.
- **Do not renumber or rename existing metrics.** `vin_only`, `vin_only_identity` and the rest are keys in `DOT-Commercial/data/precision/baseline-post-reload.json` and in four `expect-*.json` files. A rename breaks every committed comparison for a cosmetic gain.
- **Branch:** `metrics-harness`, cut from `config-validation`.

---

## What reading the existing implementation changed

`DOT-Commercial/precision_metrics.py` was read end to end before this plan was written, and two things it does are not what the spec assumed.

**There are three metric kinds, not two.** The spec named count-with-filter and distinct-count. `coherent_share_ge_070` is neither: it is `coherent_ge_070 / pairs_ge_070`, guarding against division by zero. A ratio is the metric the guarded comparisons actually lean on — three of the four shipped `expect-*.json` files mark it `must_not_fall`, more than any other metric. Omitting it would leave the config-driven runner unable to express the project's most-guarded number.

**`fields_equal` must treat null as not-equal.** `identical_name_triage` reads:

```python
pred.get("legal_name") is not None and pred.get("legal_name") == succ.get("legal_name")
```

Two records that both lack a name are not "the same name". This is the same rule stated in `docs/adding-a-dataset.md` — blank must never match blank — and a naive `==` would count every pair of nameless records as an identical-name match, inflating the metric that anchors the README's sanity check.

**Metric interdependence is real.** `canary` is a subset of `triage_bounded`, which is a subset of `triage_unbounded`, which requires `score >= 0.70` and a corroborating signal. In the Python these are nested `continue` statements; in config each metric declares its full filter independently. That is more verbose and it is the right trade: a reader of `metrics.json` can see what one metric means without tracing control flow, and a change to one cannot silently move another.

---

## File Structure

| File                                                            | Responsibility                                                              |
| --------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `utils/metric_predicates.py`                                    | The closed predicate menu: evaluate one predicate against one pair document |
| `utils/metric_runner.py`                                        | Three metric kinds; turn a metrics config plus pairs into a metric record   |
| `schema/metrics.schema.json`                                    | Structural validation of `metrics.json`                                     |
| `scripts/run_metrics.py`                                        | CLI: scan an index, produce the record, optionally write it as a baseline   |
| `DOT-Commercial/configuration/chameleon-detection/metrics.json` | DOT's eleven metrics, expressed in config                                   |

**Not deleted by this plan:** `DOT-Commercial/precision_metrics.py`. It is the reference the config version is checked against, and Task 5 decides its fate on evidence rather than in advance.

---

### Task 1: The predicate menu

**Files:**

- Create: `utils/metric_predicates.py`
- Test: `tests/test_metric_predicates.py`

**Interfaces:**

- Produces: `utils.metric_predicates.evaluate(predicate: dict, pair: dict) -> bool`, and `PREDICATES: frozenset[str]` naming every supported key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metric_predicates.py`. Cover every predicate plus the two semantics that carry reasoning:

```python
"""The closed predicate menu, and the two rules that carry a decision.

A null gap never matches a gap predicate: an unparseable date is "not
evaluable", which is not the same as "outside the window", and counting it as
inside inflates the one metric this whole harness exists to move.

fields_equal treats null as not-equal: two records that both lack a name are
not "the same name". A naive == would count every pair of nameless records as
an identical-name match.
"""

import pytest

from utils.metric_predicates import PREDICATES, evaluate


def _pair(**overrides):
    base = {
        "total_score": 0.8,
        "gap_days": 30,
        "matched_on": ["name-token", "shared-token"],
        "predecessor": {"legal_name": "ACME EXAMPLE", "entity_key": "1"},
        "successor": {"legal_name": "ACME EXAMPLE", "entity_key": "2"},
        "signals": [{"signal_type": "shared-token", "score": 1.0}],
    }
    base.update(overrides)
    return base


def test_score_gte():
    assert evaluate({"score_gte": 0.7}, _pair()) is True
    assert evaluate({"score_gte": 0.9}, _pair()) is False


def test_score_lt():
    assert evaluate({"score_lt": 0.9}, _pair()) is True


def test_a_missing_score_is_treated_as_zero():
    # A pair with no total_score is malformed, not high-scoring. Treating it as
    # missing-and-therefore-passing would let junk into every guarded band.
    assert evaluate({"score_gte": 0.1}, _pair(total_score=None)) is False


def test_gap_between_is_inclusive_on_both_ends():
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=365)) is True
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=-180)) is True
    assert evaluate({"gap_between": [-180, 365]}, _pair(gap_days=366)) is False


def test_a_null_gap_never_matches_a_gap_predicate():
    for predicate in ({"gap_between": [-180, 365]}, {"gap_lte": 365}, {"gap_gte": 0}):
        assert evaluate(predicate, _pair(gap_days=None)) is False


def test_has_signal_type_is_an_intersection():
    assert evaluate({"has_signal_type": ["shared-token", "exact-identifier"]}, _pair()) is True
    assert evaluate({"has_signal_type": ["exact-identifier"]}, _pair()) is False


def test_matched_on_equals_is_exact_set_equality():
    assert evaluate({"matched_on_equals": ["shared-token"]}, _pair()) is False
    assert evaluate(
        {"matched_on_equals": ["name-token", "shared-token"]}, _pair()
    ) is True


def test_matched_identity_equals_intersects_identity_types_first():
    # temporal is not an identity type, so a pair matching on shared-token and
    # temporal still counts as identity-only-shared-token. This distinction is
    # worth 156 pairs on the shipped baseline.
    pair = _pair(matched_on=["shared-token", "temporal"])
    assert evaluate({"matched_identity_equals": ["shared-token"]}, pair) is True
    assert evaluate({"matched_on_equals": ["shared-token"]}, pair) is False


def test_fields_equal_compares_both_sides():
    assert evaluate({"fields_equal": "legal_name"}, _pair()) is True
    pair = _pair(successor={"legal_name": "OTHER", "entity_key": "2"})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_fields_equal_treats_null_as_not_equal():
    # Blank must never match blank -- otherwise every pair of nameless records
    # counts as an identical-name match.
    pair = _pair(predecessor={"legal_name": None}, successor={"legal_name": None})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_fields_equal_with_one_side_missing_is_false():
    pair = _pair(successor={"entity_key": "2"})
    assert evaluate({"fields_equal": "legal_name"}, pair) is False


def test_signal_count_gte():
    assert evaluate({"signal_count_gte": 2}, _pair()) is True
    assert evaluate({"signal_count_gte": 3}, _pair()) is False


def test_all_requires_every_clause():
    assert evaluate({"all": [{"score_gte": 0.7}, {"gap_lte": 365}]}, _pair()) is True
    assert evaluate({"all": [{"score_gte": 0.7}, {"gap_lte": 10}]}, _pair()) is False


def test_any_requires_one_clause():
    assert evaluate({"any": [{"score_gte": 0.99}, {"gap_lte": 365}]}, _pair()) is True


def test_not_inverts():
    assert evaluate({"not": {"score_gte": 0.99}}, _pair()) is True


def test_an_empty_predicate_matches_everything():
    # The `pairs` metric has no filter; an empty predicate is how that is said.
    assert evaluate({}, _pair()) is True


def test_an_unknown_predicate_raises():
    # A closed menu: a typo must fail loudly rather than quietly matching
    # nothing and reporting a metric of zero as though it were measured.
    with pytest.raises(ValueError, match="unknown predicate"):
        evaluate({"score_greater": 0.7}, _pair())


def test_a_predicate_declaring_two_keys_raises():
    with pytest.raises(ValueError, match="exactly one"):
        evaluate({"score_gte": 0.7, "gap_lte": 10}, _pair())


def test_the_menu_is_enumerated_for_the_schema():
    # The schema's enum is generated from this, so drift is impossible.
    assert "score_gte" in PREDICATES
    assert "matched_identity_equals" in PREDICATES
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_metric_predicates.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `utils/metric_predicates.py`**

One function per predicate in a dispatch dict keyed by name, `PREDICATES` derived from that dict's keys so the schema cannot drift from the implementation. `evaluate` requires exactly one key per predicate object and raises on an unknown name — the menu is closed for the same reason the selector clause menu is: a typo that quietly matches nothing reports a metric of zero as though it had been measured.

Import `IDENTITY_SIGNAL_TYPES` from `matching.scorer` for `matched_identity_equals` rather than restating the set.

- [ ] **Step 4: Run the test, lint, commit**

---

### Task 2: The three metric kinds and the runner

**Files:**

- Create: `utils/metric_runner.py`
- Test: `tests/test_metric_runner.py`

**Interfaces:**

- Consumes: `evaluate` from Task 1.
- Produces: `utils.metric_runner.summarize(metrics: list[dict], pairs) -> dict`, taking any iterable of pair `_source` dicts and returning name → number.

- [ ] **Step 1: Write the failing test**

Cover each kind and the interactions that matter:

- **count** — a metric with a filter counts matching pairs; a metric with no filter counts all.
- **distinct** — counts unique values of a dotted field path over matching pairs, and a pair missing that path contributes nothing rather than a `None` bucket.
- **ratio** — `numerator / denominator` by metric name, and **a zero denominator yields 0.0 rather than raising or producing NaN**, matching what `precision_metrics.py` does. A NaN would serialize into a baseline JSON file as `NaN`, which is not valid JSON and would poison every later comparison.
- Ratio referring to a metric defined _later_ in the list still works, or is rejected clearly — pick one, test it, and say which in the docstring.
- `summarize` streams: it must accept a generator and not materialize the pairs, since a real population is hundreds of thousands.

- [ ] **Step 2–4: Run failing, implement, verify**

Order of evaluation matters: counts and distincts are computed in one pass over the pairs, then ratios are computed from the resulting record. That ordering is what lets a ratio name any count regardless of declaration order.

- [ ] **Step 5: Lint, test, commit**

---

### Task 3: `metrics.json`, its schema, and DOT's eleven metrics

**Files:**

- Create: `schema/metrics.schema.json`
- Create: `DOT-Commercial/configuration/chameleon-detection/metrics.json`
- Modify: `phase_providers/phase_validate.py` (add `metrics` to `STEP_CONFIG_FILES`)
- Test: `tests/test_metrics_schema.py`

**Interfaces:**

- Consumes: `PREDICATES` from Task 1, `config_schema` from Plan 2.
- Produces: schema kind `metrics`.

- [ ] **Step 1: Write the failing test**

Assert: the shipped `metrics.json` validates; an unknown predicate name is rejected; a metric with no `name` is rejected; a ratio naming a metric that does not exist is rejected by the coherence rule in Task 4, not here — say so in a comment so nobody adds it twice.

Include a test comparing the schema's predicate enum against `metric_predicates.PREDICATES`, mirroring the signal-type drift test from Plan 2 Task 3.

- [ ] **Step 2: Write DOT's `metrics.json`**

All eleven, transcribed from `precision_metrics.py` — not invented. The interdependent ones spell out their full filter:

```json
{
  "baseline": "data/precision/baseline-post-reload.json",
  "metrics": [
    { "name": "pairs" },
    { "name": "pairs_ge_070", "filter": { "score_gte": 0.7 } },
    {
      "name": "coherent_ge_070",
      "filter": { "all": [{ "score_gte": 0.7 }, { "gap_between": [-180, 365] }] }
    },
    {
      "name": "coherent_share_ge_070",
      "ratio": { "numerator": "coherent_ge_070", "denominator": "pairs_ge_070" }
    },
    { "name": "vin_only", "filter": { "matched_on_equals": ["shared-token"] } },
    {
      "name": "vin_only_identity",
      "filter": { "matched_identity_equals": ["shared-token"] }
    },
    {
      "name": "triage_unbounded",
      "filter": {
        "all": [
          { "score_gte": 0.7 },
          { "has_signal_type": ["shared-token", "exact-identifier"] },
          { "gap_lte": 365 }
        ]
      }
    },
    {
      "name": "triage_bounded",
      "filter": {
        "all": [
          { "score_gte": 0.7 },
          { "has_signal_type": ["shared-token", "exact-identifier"] },
          { "gap_between": [0, 365] }
        ]
      }
    },
    {
      "name": "identical_name_triage",
      "filter": {
        "all": [
          { "score_gte": 0.7 },
          { "has_signal_type": ["shared-token", "exact-identifier"] },
          { "gap_lte": 365 },
          { "fields_equal": "legal_name" }
        ]
      }
    },
    {
      "name": "canary",
      "filter": {
        "all": [
          { "score_gte": 0.99 },
          { "has_signal_type": ["shared-token", "exact-identifier"] },
          { "gap_between": [0, 7] },
          { "fields_equal": "legal_name" }
        ]
      }
    },
    { "name": "predecessors_with_pairs", "distinct": "predecessor.entity_key" }
  ]
}
```

**Check each against the Python before moving on.** `triage_bounded` is `triage_unbounded` plus `gap >= 0`, which becomes `gap_between: [0, 365]`. `canary` additionally requires `score >= 0.99` and `gap <= 7`, so its band is `[0, 7]`. Getting one of these subtly wrong is the failure Task 5 exists to catch — but catching it there costs a full comparison run.

**`predecessors_with_pairs` uses `predecessor.entity_key`, not `dot_number`.** Both are emitted, and the generic name is the one a second project will have.

- [ ] **Step 3: Add `metrics` to the validate phase's file list**

One line in `STEP_CONFIG_FILES`. The phase then schema-checks `metrics.json` for free.

- [ ] **Step 4: Run, lint, commit**

---

### Task 4: Coherence rules for metrics

**Files:**

- Modify: `utils/config_coherence.py`
- Modify: `phase_providers/phase_validate.py` (run metric coherence when `metrics.json` is present)
- Test: `tests/test_config_coherence.py`

- [ ] **Step 1: Write the failing tests**

Three rules, each catching a metrics config that a schema accepts and that produces a wrong number rather than an error:

- A `ratio` naming a metric that is not defined. Silently produces nothing or raises deep in the runner.
- Two metrics sharing a `name`. The later silently overwrites the earlier, so one declared metric never appears and nothing says so.
- A `baseline` path that does not exist, **reported as a warning rather than a failure**: a project that has not taken its first baseline yet is in a legitimate state, and failing would make the metric harness unusable until one exists.

- [ ] **Step 2–4: Implement, verify, commit**

---

### Task 5: Verify against the existing implementation, then decide its fate

This is the task the plan exists for. Everything above is unproven until the config-driven runner reproduces the hand-written numbers on the real population.

**Files:**

- Create: `scripts/run_metrics.py`
- Test: `tests/test_run_metrics.py`

- [ ] **Step 1: Implement `scripts/run_metrics.py`**

Scans an index and prints the metric record. Follows `scripts/compare_sweeps.py`'s argument conventions (`--index`, `--host`, `--project`), and restricts `_source` to the fields the configured predicates actually read — a full pair document carries the per-signal contribution array, roughly ten times the bytes and unused by most metrics.

Deriving the needed `_source` fields from the predicates rather than hardcoding them is the point: a hardcoded list is how `compare_sweeps.py` would silently stop feeding a newly added predicate.

- [ ] **Step 2: Run both implementations over the same real population**

```bash
.venv/bin/python scripts/run_metrics.py \
    --project DOT-Commercial --step chameleon-detection \
    --index chameleon-candidates-2026.08.17-000001
```

and the existing path, over the same index. Compare all eleven for **exact equality**.

- [ ] **Step 3: If any metric differs, the config is wrong — fix it, do not adjust the baseline**

The hand-written implementation is the reference here. A difference means a filter was transcribed incorrectly, and the differing metric names which one.

- [ ] **Step 4: Record the comparison**

Write the two records and their agreement into the commit message. This is the evidence the harness works; a later reader should not have to re-derive it.

- [ ] **Step 5: Decide whether `precision_metrics.py` is retired**

The spec says retire it once they agree. Make that call **on the evidence**, and state the reasoning either way:

- `scripts/compare_sweeps.py` imports `METRICS` and `summarize` from it, so retiring means pointing that at `metric_runner` — a real change to a working comparison tool.
- `tests/test_dot_commercial_precision_metrics.py` tests it directly and would be deleted or rewritten.
- Against retiring: it is the only independent check that the DSL means what the Python meant, and deleting it makes any future divergence undetectable.

Whichever is chosen, say why in the commit. **Do not retire it in the same commit that first proves agreement** — one commit should establish the evidence, a separate one act on it.

- [ ] **Step 6: Lint, full test run, commit**

---

## Self-Review

**Spec coverage.** Rollout step 8 asks for `metrics.json`, a closed predicate menu, the two null-handling semantics pinned in the schema, and verification against `precision_metrics.py` before retirement. Task 1 covers the menu and both semantics, Tasks 2–3 the kinds and config, Task 4 coherence, Task 5 the verification and the retirement decision.

**One spec correction, made deliberately.** The spec says two metric kinds. There are three: `coherent_share_ge_070` is a ratio, and three of the four shipped `expect-*.json` files mark it `must_not_fall` — more than any other metric. A two-kind runner could not express the project's most-guarded number. Recorded here rather than silently added.

**Placeholder scan.** Tasks 2, 4 and 5 describe test coverage and structure rather than giving literal test bodies, because each depends on signatures established in the task before it and on numbers that only exist once the runner runs. Every one names its acceptance condition. Tasks 1 and 3 carry literal content.

**Type consistency.** `evaluate(predicate, pair) -> bool` (Task 1) is called only by `metric_runner.summarize(metrics, pairs) -> dict` (Task 2), which is called by `scripts/run_metrics.py` (Task 5) and produces the same `name -> number` shape `utils.sweep_compare.compare()` already consumes — that compatibility is what makes the four shipped `expect-*.json` files keep working.

**Ordering hazard.** Task 3's `metrics.json` cannot be validated until Task 1 exists, because the schema's predicate enum is generated from `PREDICATES`. Task 3 before Task 1 leaves the schema unwritable.
