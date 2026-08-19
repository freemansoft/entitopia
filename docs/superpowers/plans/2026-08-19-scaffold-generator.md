# Scaffold Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the mechanical half of onboarding a dataset — directory layout, per-column mappings, boilerplate — into one command, while leaving every judgement call visibly unresolved.

**Architecture:** `scripts/new_project.py` profiles each CSV, infers what measurement can decide, and writes a project tree. Anything measurement cannot decide is emitted as a `__TODO_*__` key, which existing `additionalProperties: false` schemas reject — so a scaffolded project fails `validate` until a human has resolved every marker.

**Tech Stack:** Python 3.11+, jsonschema 4.26.0, pytest, ruff.

This is Plan 4 of five, covering rollout step 9 of [the spec](../specs/2026-08-16-config-driven-analysis-portability-design.md). It depends on [Plan 2](2026-08-17-config-validation.md) for the schemas that make the markers fatal.

## Global Constraints

- **Everything runs from `.venv`.** Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/python -m ruff check .`
- **`ruff check .` must print `All checks passed!`** before any commit.
- **Comments explain why, not what.**
- **Never name a real flagged entity.** Generated project READMEs record column names and distinct counts, never row values from an identity field.
- **No new dependencies.** The profiler and `json` are enough.
- **Do not change the schemas.** The markers must be rejected by machinery that already exists; needing a schema change would mean the marker mechanism is wrong.
- **Branch:** `scaffold-generator`, cut from `main`.

---

## The design decision this rests on

**A marker is a key, not a value.**

The obvious approach is `"id_field": "TODO: choose a key"`. It does not work: `id_field` is typed `string`, so that validates cleanly and a scaffolded project would sweep with a literal `TODO` as its document key.

Instead the generator omits the real key and writes a marker key beside it:

```json
{
  "alias": "hospitals-000001",
  "index": "hospitals-{now/d}-000001",
  "source": "Hospital_General_Information.csv",
  "__TODO_choose_id_field__": "No single column was unique across 5,432 rows. Test candidates with: .venv/bin/python scripts/profile_dataset.py <csv> --key col_a --key col_b"
}
```

Every schema already sets `additionalProperties: false`, so this is rejected with a message naming the marker:

```
index-config.json: (root): Additional properties are not allowed
('__TODO_choose_id_field__' was unexpected)
```

Three properties fall out of that, and they are why this shape was chosen over adding scaffold vocabulary to the schemas:

- **It needs no schema change.** The mechanism that makes markers fatal is the same one that catches a renamed key, already tested and already trusted.
- **The marker cannot be satisfied by accident.** A `TODO` string can be left in place and still run. A marker key must be deleted, and deleting it forces the operator to write the real key or fail on a missing required property.
- **The name carries the instruction**, so the validation message is useful without the schema knowing anything about scaffolding.

## What is inferred, and what is refused

The dividing line is the one `docs/adding-a-dataset.md` draws: measurement decides shape, judgement decides meaning.

| Inferred from the profile                                                                                 | Refused, emitted as a marker                                                                |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Column names and their Elasticsearch types                                                                | `id_field` — the profiler tests a candidate key, it does not choose one                     |
| `keyword` for anything with leading zeros, or integer-shaped and short (an identifier, not a measurement) | Analyzers — only worth configuring if this dataset carries identity fields to match on      |
| `keyword` for date-shaped columns, never `date`                                                           | Every part of `entity-match.json`: which fields are signals, at what weight, which may seed |
| `text` + `keyword` subfield for high-cardinality free text                                                | Whether a high-cardinality column is a _fingerprint_ or merely varied                       |
| Step layout, phase lists, aliases, index name stamps                                                      |                                                                                             |

**Two inferences are deliberately conservative**, both because the README records the incident behind them:

- **A date-shaped column is mapped `keyword`, never `date`.** A single malformed value in a `date`-mapped field throws `document_parsing_exception` and Elasticsearch drops the **entire document**, not just that field. The generator cannot know whether every value parses, so it picks the shape that cannot lose a record and leaves a note.
- **An integer-shaped column with leading zeros is `keyword`.** Dynamic inference reads it as `long` and destroys the padding — the measured case being ZIP codes where `00602` became `602`.

## File Structure

| File                        | Responsibility                                                                                     |
| --------------------------- | -------------------------------------------------------------------------------------------------- |
| `scripts/new_project.py`    | CLI, project tree writing, marker placement                                                        |
| `utils/scaffold.py`         | Pure functions: profile → field type, profile → mapping, marker construction                       |
| `tests/test_scaffold.py`    | The inference rules, one test per rule                                                             |
| `tests/test_new_project.py` | End-to-end: generate into a tmp dir, assert it FAILS validation, resolve markers, assert it passes |

The pure/integration split mirrors `utils/crash_lift.py` versus its measure script: the part that decides what a number means is testable without touching a filesystem.

---

### Task 1: Type inference from a column profile

**Files:**

- Create: `utils/scaffold.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**

- Produces: `utils.scaffold.field_type(profile) -> dict` returning an Elasticsearch field mapping, and `utils.scaffold.mapping_properties(fieldnames, columns) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
"""Type inference, and the two rules that exist because of a recorded incident.

A date-shaped column maps `keyword`, never `date`: one malformed value in a
date-mapped field throws document_parsing_exception and Elasticsearch drops the
ENTIRE document. The generator cannot know whether every value parses, so it
picks the shape that cannot lose a record.

An integer-shaped column with leading zeros maps `keyword`: dynamic inference
reads it as `long` and destroys the padding, the measured case being ZIP codes
where 00602 became 602.
"""

import csv

from scripts_profile import profile_columns  # see Step 3 for the loader helper
from utils import scaffold


def _profile_of(values, name="col"):
    """Build a real ColumnProfile by feeding it values, not a stub.

    Using the profiler's own class rather than a fake means these tests fail if
    its accounting changes, which is the point -- the inference reads its
    counters.
    """
    column = profile_columns(name)
    for value in values:
        column.add(value)
    return column


def test_a_date_shaped_column_is_keyword_not_date():
    column = _profile_of(["2021-01-01", "2021-06-30", "2020-12-31"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_legacy_date_shaped_column_is_also_keyword():
    column = _profile_of(["01-JUN-74", "15-MAR-99"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_leading_zero_identifier_is_keyword_not_long():
    column = _profile_of(["00602", "00603", "01234"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_plain_integer_column_is_keyword_when_short():
    # Identifiers and codes are keyword even when numeric -- the project rule.
    column = _profile_of(["23680", "99123", "10001"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_low_cardinality_text_is_keyword():
    column = _profile_of(["ACTIVE"] * 50 + ["INACTIVE"] * 50)
    assert scaffold.field_type(column)["type"] == "keyword"


def test_high_cardinality_free_text_gets_a_keyword_subfield():
    column = _profile_of(["NAME {}".format(i) for i in range(300)])
    mapping = scaffold.field_type(column)
    assert mapping["type"] == "text"
    assert mapping["fields"]["keyword"]["type"] == "keyword"


def test_an_all_blank_column_is_keyword_rather_than_guessed():
    # Nothing to infer from. keyword cannot drop a document, so it is the safe
    # floor; guessing a type from no evidence is how dynamic inference goes
    # wrong in the first place.
    column = _profile_of(["", "", ""])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_mapping_properties_covers_every_column_in_order():
    ...
```

- [ ] **Step 2: Run it to confirm it fails**

- [ ] **Step 3: Resolve how tests reach `ColumnProfile`**

`scripts/profile_dataset.py` is not an importable package module. Load it by path with `importlib`, exactly as `tests/test_profile_dataset.py` already does — read that file first and copy its loader rather than inventing a second one. Replace the placeholder `scripts_profile` import in the test above with whatever that convention produces.

- [ ] **Step 4: Implement `utils/scaffold.py`**

Each rule gets its own small function with the incident in its docstring. `field_type` dispatches; do not write one long `if` chain with the reasoning in comments beside it.

- [ ] **Step 5: Run, lint, commit**

---

### Task 2: Generating the project tree

**Files:**

- Create: `scripts/new_project.py`
- Test: `tests/test_new_project.py`

**Interfaces:**

- Consumes: `utils.scaffold` from Task 1.
- Produces: `scripts/new_project.py` with `--project`, `--csv` (repeatable, `step=path` form), `--rows` to cap profiling, and `--force`.

- [ ] **Step 1: Write the failing test**

Generate into `tmp_path` and assert the tree:

```
<project>/
  configuration.json
  configuration/<step>/index-config.json
  configuration/<step>/index-mappings.json
  configuration/chameleon-detection-equivalent/entity-match.json   # see Step 4
  data/<step>/                                                     # empty, for the CSV
  README.md
```

Assert `configuration.json` lists one step per CSV with `validate` first in its phases, and that the generated index name carries a `{now/d}` stamp — a reload landing in the same index as the same day's earlier run is a hazard the layout should not invite.

- [ ] **Step 2: Run it to confirm it fails**

- [ ] **Step 3: Implement the tree writer**

Refuse to overwrite an existing project directory unless `--force`. Print every file written and every marker placed, because the marker list _is_ the operator's work queue.

- [ ] **Step 4: Decide the entity-match stub, and record the decision**

The spec says emit a skeleton `entity-match.json`. There is a real argument against: that file is almost entirely judgement, so a stub is mostly markers, and now that `schema/entity-match.schema.json` documents the shape and an editor can complete against it, a stub adds little.

**Emit it anyway, and say why in the module docstring:** its value is not the shape but the forcing function. A generated project that carries no `entity-match.json` validates clean and looks finished; one that carries a marker-filled stub fails `validate` until a human has made every decision, which is the behavior the spec asked for. Keep it minimal — `source_index`, an `entity` block with a marker for `key`, and a marker for `signals`.

- [ ] **Step 5: Run, lint, commit**

---

### Task 3: The markers must actually fail validation

This is the task that decides whether the mechanism works. Everything above is untested until a generated project is fed to the real validator.

**Files:**

- Modify: `tests/test_new_project.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_freshly_scaffolded_project_fails_validation(tmp_path):
    """A scaffold that validates clean would be the whole point defeated.

    The markers are the operator's work queue. If validation passes with them
    in place, a project can be swept with a literal placeholder as its
    document key and nothing will have said so.
    """
    ...generate...
    errors = config_schema.validate_file("index-config", str(index_config_path))
    assert errors
    assert any("__TODO_" in e for e in errors)


def test_resolving_every_marker_makes_it_validate(tmp_path):
    """The other half: the markers must be the ONLY thing blocking it.

    A scaffold that still fails after every marker is resolved would send an
    operator hunting for a defect in generated output rather than writing
    their config.
    """
    ...generate, then replace each marker key with a plausible real value...
    assert config_schema.validate_file("index-config", str(path)) == []
```

Cover the same pair for `entity-match.json` and `configuration.json`.

- [ ] **Step 2–4: Run failing, adjust the generator until both halves hold, commit**

If a marker turns out **not** to fail validation, the marker is in a position the schema does not police — fix the marker's placement, not the schema.

---

### Task 4: Generate a real project from a real CSV

**Files:**

- No new files; this is a measurement.

- [ ] **Step 1: Generate a scratch project from a shipped CSV**

Use a real extract — `CMS-Providers/data/hospitals/Hospital_General_Information.csv` is the smallest — writing into the scratch directory, never into the repo.

- [ ] **Step 2: Run the validate phase against it and confirm it fails, naming the markers**

- [ ] **Step 3: Resolve the markers by hand and confirm it then validates clean**

Record how many markers there were and what they asked for. That count is the honest measure of how much of the work this actually removes — if it is one marker and forty inferred fields, say so; if it is fifteen markers, the generator is doing less than it appears.

- [ ] **Step 4: Compare the generated mappings against the hand-written ones**

`CMS-Providers/configuration/hospitals/index-mappings.json` already exists for this exact CSV. Diff the generated mapping against it and **record every difference in the commit message**, deciding for each whether the generator or the hand-written file is right. This is the only check that the inference rules produce something a human would have written.

Expect differences around analyzers — the generator does not emit them, by design.

- [ ] **Step 5: Document and commit**

Add a short section to `docs/adding-a-dataset.md` § "Measure before configuring" pointing at the generator as the step _after_ profiling, and state plainly what it does not decide.

---

## Self-Review

**Spec coverage.** Rollout step 9 asks for a generator that emits a skeleton, runs the profiler, and produces config that cannot run until a human resolves every marker. Tasks 1–2 cover generation, Task 3 the forcing function, Task 4 proves it against real data.

**One spec deviation, with reasoning.** The spec lists `index-settings` among the generated files. This plan does not emit one: settings exist almost entirely to declare analyzers, and analyzers are worth configuring only if the dataset carries identity fields to match on — a judgement the generator refuses. Emitting an empty settings file would be scaffolding that looks like a decision. The project README it writes says so instead.

**Placeholder scan.** Task 1's test carries a deliberate unresolved import (`scripts_profile`), which Step 3 exists to resolve against the convention already in `tests/test_profile_dataset.py`; inventing a second loader in this plan would be the mistake. Tasks 2–4 describe assertions rather than literal bodies where the shape depends on the CLI settled in Task 2.

**Type consistency.** `field_type(profile) -> dict` (Task 1) is called only by `mapping_properties` (Task 1), which is called by `scripts/new_project.py` (Task 2).

**Ordering hazard.** Task 3 is not optional polish — it is the task that tests the central mechanism. Running Tasks 1, 2 and 4 without it would produce a generator whose markers might validate clean, which is worse than no generator: it would hand an operator a project that appears ready and is not.
