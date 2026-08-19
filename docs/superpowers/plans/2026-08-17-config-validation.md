# Config Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch a broken project configuration before a sweep runs, instead of after it produces quietly wrong output.

**Architecture:** A `validate` phase with three tiers, cheapest first, each fatal. Tier 1 is JSON Schema over the config files entitopia itself defines. Tier 2 is cross-file coherence in plain Python. Tier 3 asks the live cluster whether the fields and subfields the config names actually exist. Tiers 1–2 need no cluster and run in CI.

**Tech Stack:** Python 3.11+, `jsonschema` 4.26.0, Elasticsearch 9.4.1, pytest, ruff.

This is Plan 2 of five, covering rollout step 7 of [the spec](../specs/2026-08-16-config-driven-analysis-portability-design.md). It depends on [Plan 1](2026-08-16-matcher-generalization.md), which is complete and whose [compatibility gate](2026-08-16-compatibility-gate-runbook.md) passed.

## Global Constraints

- **Everything runs from `.venv`.** Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/python -m ruff check .`
- **`ruff check .` must print `All checks passed!`** before any commit.
- **Comments explain why, not what.** State the reason, the caller, and what breaks if it is wrong.
- **Never name a real flagged entity.** Junk data values (`GGGG`, `UNKNOWN`, `(000) 000-0000`) and aggregate counts are fine.
- **Elasticsearch calls pass explicit keyword arguments, never `body=`.**
- **Config objects are `SimpleNamespace`** via `file_utils.load_from_file`. Schema validation needs plain dicts, so it re-reads with `json.load` — see Task 1.
- **Pin every dependency, direct and transitive**, in `requirements.txt`.
- **Do not change matcher behavior.** This plan adds a gate in front of the sweep; it must not alter what the sweep does when config is valid. Plan 1's compatibility gate is not re-run here, so any change to scoring or population selection is out of scope by definition.
- **Branch:** `config-validation`, cut from `config-driven-analysis-portability`.

---

## Scoping decision: what does NOT get a schema

`index-mappings.json` and `index-settings.json` are raw Elasticsearch mapping and settings DSL. Schematizing them properly means schematizing Elasticsearch — a moving target owned by someone else, where a stale schema would reject valid config and teach operators to distrust the validator.

They are already covered better elsewhere: Elasticsearch rejects a bad mapping loudly, and this repo made that rejection fatal (README closed item, "fail the run when Elasticsearch refuses a mapping"). So these two get an **envelope check only** — the `index` key entitopia itself reads — and everything under `mappings` / `settings` is passed through untouched.

The same reasoning applies inside `pipelines.json` and `enrichment-policies.json`: validate the wrapper entitopia defines, not the processor list Elasticsearch defines.

**The rule:** schematize what entitopia invented; let Elasticsearch reject what Elasticsearch invented.

---

## File Structure

| File                                     | Responsibility                                                            |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| `schema/configuration.schema.json`       | Project file: steps, phases, directories, log level                       |
| `schema/index-config.schema.json`        | Per-dataset: alias, index, source, id_field, row caps                     |
| `schema/entity-match.schema.json`        | The analysis: entity, lifecycle, population, candidates, signals, scoring |
| `schema/pipelines.schema.json`           | Envelope only                                                             |
| `schema/enrichment-policies.schema.json` | Envelope only                                                             |
| `schema/index-mappings.schema.json`      | Envelope only                                                             |
| `schema/index-settings.schema.json`      | Envelope only                                                             |
| `utils/config_schema.py`                 | Load schemas, validate a dict, shape errors into readable messages        |
| `utils/config_coherence.py`              | Tier 2: cross-file checks that need no cluster                            |
| `utils/config_liveness.py`               | Tier 3: mapping and subfield checks against a cluster                     |
| `phase_providers/phase_validate.py`      | The phase: run all three tiers, report everything, raise on any failure   |

**Two design decisions that shape every task below.**

**`additionalProperties: false` everywhere entitopia owns the vocabulary.** This is the highest-value setting in the whole plan and the reason it is worth doing at all. A permissive schema accepts `max_shared_carriers` silently after that key was renamed to `max_shared_entities`, and the signal falls back to its default with nothing reported — precisely the class of failure this repo keeps hitting. A typo must be an error, not an ignored key.

**Validation returns messages; it does not raise per problem.** Fixing config is iterative, and a validator that stops at the first error turns a five-mistake config into five runs. Every tier collects everything it can find, the phase prints the lot, and only then raises.

---

### Task 1: Add `jsonschema` and the schema loader

**Files:**

- Modify: `requirements.txt`
- Create: `utils/config_schema.py`
- Create: `schema/index-config.schema.json`
- Test: `tests/test_config_schema.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `utils.config_schema.validate_file(kind: str, path: str) -> list[str]` and `validate_mapping(kind: str, raw: dict, source: str) -> list[str]`, both returning human-readable messages, empty when valid. `kind` is a schema basename without suffix, e.g. `"index-config"`.

- [ ] **Step 1: Pin the new dependencies**

Add to `requirements.txt` under direct dependencies:

```
jsonschema==4.26.0
```

and under transitive:

```
attrs==26.1.0
jsonschema-specifications==2025.9.1
referencing==0.37.0
rpds-py==2026.6.3
```

These are the versions `pip install --dry-run --report` resolved on 2026-08-17. Keep the two groups separate, matching the file's existing structure and its stated reason (a fresh checkout resolves to exactly this environment).

- [ ] **Step 2: Install and confirm**

```bash
bash dependencies.sh
.venv/bin/python -c "import jsonschema; print(jsonschema.__version__)"
```

Expected: `4.26.0`.

- [ ] **Step 3: Write the failing test**

Create `tests/test_config_schema.py`:

```python
"""Config validation reports every problem it can see, by file and key.

A validator that stops at the first error turns a five-mistake config into
five runs, so these pin that messages accumulate. They also pin the setting
the whole exercise rests on: an unknown key is an error, not an ignored key.
A permissive schema accepts a renamed config key silently and lets the value
fall back to its default with nothing reported.
"""

import pytest

from utils import config_schema


def test_a_valid_index_config_reports_nothing():
    raw = {
        "alias": "carriers-000001",
        "index": "carriers-{now/d}-000001",
        "source": "carriers.csv",
        "id_field": "dot_number",
        "num_rows": None,
        "skip_rows": 0,
    }
    assert config_schema.validate_mapping("index-config", raw, "test.json") == []


def test_a_composite_id_field_is_accepted():
    # Several datasets key on a list of columns; both shapes are legitimate.
    raw = {
        "alias": "a-000001",
        "index": "a-000001",
        "source": "a.csv",
        "id_field": ["Ind_enrl_ID", "org_pac_id", "adrs_id"],
    }
    assert config_schema.validate_mapping("index-config", raw, "test.json") == []


def test_an_unknown_key_is_an_error_not_an_ignored_key():
    raw = {
        "alias": "a-000001",
        "index": "a-000001",
        "source": "a.csv",
        "num_rowz": 100,
    }
    errors = config_schema.validate_mapping("index-config", raw, "test.json")
    assert errors
    assert any("num_rowz" in e for e in errors)


def test_a_missing_required_key_is_reported():
    errors = config_schema.validate_mapping("index-config", {"alias": "a"}, "test.json")
    assert any("index" in e for e in errors)


def test_every_problem_is_reported_not_just_the_first():
    raw = {"alias": 7, "index": 9, "source": 11}
    errors = config_schema.validate_mapping("index-config", raw, "test.json")
    assert len(errors) >= 3


def test_messages_name_the_file_and_the_key_path():
    errors = config_schema.validate_mapping(
        "index-config", {"alias": 7, "index": "i", "source": "s"}, "DOT/index-config.json"
    )
    assert any("DOT/index-config.json" in e and "alias" in e for e in errors)


def test_an_unknown_schema_kind_raises():
    # A typo in a caller's kind string must not silently validate nothing.
    with pytest.raises(ValueError, match="no schema"):
        config_schema.validate_mapping("not-a-kind", {}, "test.json")
```

- [ ] **Step 4: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_config_schema.py -q`
Expected: FAIL, `No module named 'utils.config_schema'`.

- [ ] **Step 5: Write `schema/index-config.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "entitopia index-config",
  "description": "Per-dataset load settings. Every key here is read by entitopia itself, so unknown keys are rejected: a typo that is merely ignored lets a setting fall back to its default with nothing reported.",
  "type": "object",
  "additionalProperties": false,
  "required": ["alias", "index", "source"],
  "properties": {
    "alias": {
      "type": "string",
      "description": "Stable read name. Reads through an alias naming more than one index return each document once per attached index, which is silent rather than an error."
    },
    "index": {
      "type": "string",
      "description": "Concrete index name, usually carrying a {now/d} stamp so a reload lands in a fresh index."
    },
    "source": {
      "type": "string",
      "description": "CSV filename under the project's data directory."
    },
    "id_field": {
      "description": "Natural key. A list composes a deterministic composite id. Absent means Elasticsearch generates one, which duplicates every row on rerun.",
      "oneOf": [
        { "type": "string" },
        { "type": "array", "items": { "type": "string" }, "minItems": 1 }
      ]
    },
    "num_rows": {
      "description": "Row cap. null means load everything. A committed non-null value is the validation-sample-in-production hazard.",
      "type": ["integer", "null"],
      "minimum": 1
    },
    "skip_rows": { "type": "integer", "minimum": 0 },
    "pipeline": { "type": "string" }
  }
}
```

Check the `pipeline` key against the shipped configs before committing this: if no `index-config.json` uses it, remove it rather than schematizing a key nothing sets.

- [ ] **Step 6: Implement `utils/config_schema.py`**

```python
"""Structural validation of the config files entitopia itself defines.

Exists because this repo's recurring failure is configuration that parses and
is inert: a renamed key that silently falls back to a default, an analyzer
naming a column that no longer exists, a validation row-cap left switched on.
None of those raise. A schema turns the first class of them into an error.

Deliberately does NOT cover index-mappings.json or index-settings.json beyond
their envelope. Those are Elasticsearch's own DSL, owned by someone else and
moving independently; a stale schema over them would reject valid config and
teach operators to distrust the validator. Elasticsearch rejects a bad mapping
loudly and this repo already made that fatal.

Returns messages rather than raising per problem. Fixing config is iterative,
and a validator that stops at the first error turns a five-mistake config into
five runs.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


def _load_schema(kind: str) -> dict:
    """Read one schema by kind, raising when the kind is unknown.

    Raises rather than returning an empty schema, because an empty schema
    validates everything: a caller with a typo in its kind string would report
    a clean config forever.
    """
    path = SCHEMA_DIR / "{}.schema.json".format(kind)
    if not path.exists():
        raise ValueError(
            "no schema for config kind {!r}; known kinds are {}".format(
                kind,
                ", ".join(sorted(p.name.split(".")[0] for p in SCHEMA_DIR.glob("*.schema.json"))),
            )
        )
    with open(path) as handle:
        return json.load(handle)


def _describe(error, source: str) -> str:
    """One jsonschema error as a line naming the file and the key path.

    jsonschema's own str() omits the file and renders the path as a deque, so
    an operator reading a failure cannot tell which of twelve index-configs it
    came from. The path is joined with dots to match how these keys are
    written and talked about.
    """
    location = ".".join(str(part) for part in error.absolute_path) or "(root)"
    return "{}: {}: {}".format(source, location, error.message)


def validate_mapping(kind: str, raw: dict, source: str) -> list[str]:
    """Validate an already-parsed config dict. Returns messages, empty if valid.

    Takes a dict rather than the SimpleNamespace the rest of the codebase uses
    because jsonschema validates JSON data structures; converting a namespace
    back into a dict would lose exactly the "unknown key" information this is
    here to catch.
    """
    validator = jsonschema.Draft202012Validator(_load_schema(kind))
    return [
        _describe(error, source)
        for error in sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))
    ]


def validate_file(kind: str, path: str) -> list[str]:
    """Validate one config file on disk, reporting unreadable JSON as an error.

    A file that will not parse is a validation failure like any other, not an
    exception for the caller to handle: reporting it in the same list keeps
    the phase's output one flat list of things to fix.
    """
    try:
        with open(path) as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return ["{}: file not found".format(path)]
    except json.JSONDecodeError as e:
        return ["{}: invalid JSON: {}".format(path, e)]
    return validate_mapping(kind, raw, path)
```

- [ ] **Step 7: Run the test**

Run: `.venv/bin/python -m pytest tests/test_config_schema.py -q`
Expected: PASS.

- [ ] **Step 8: Validate every shipped `index-config.json`**

```bash
.venv/bin/python -c "
from pathlib import Path
from utils import config_schema
bad = 0
for p in sorted(Path('.').glob('*/configuration/*/index-config.json')):
    for msg in config_schema.validate_file('index-config', str(p)):
        print(msg); bad += 1
print('problems:', bad)
"
```

Expected: `problems: 0` across all 12. **If any shipped config fails, the schema is wrong, not the config** — these are the files that have been running in production. Fix the schema.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat: add JSON Schema validation for index-config

The recurring failure here is config that parses and is inert. additionalProperties
is false everywhere entitopia owns the vocabulary, which is the point: a renamed
key that is merely ignored lets a setting fall back to its default silently.

Errors accumulate rather than raising one at a time, because fixing config is
iterative and a first-error validator turns a five-mistake config into five runs.
Messages name the file and the dotted key path; jsonschema's own str() gives
neither, so a failure could not be traced to one of twelve index-configs."
```

---

### Task 2: Schemas for the remaining entitopia-owned config kinds

**Files:**

- Create: `schema/configuration.schema.json`, `schema/pipelines.schema.json`, `schema/enrichment-policies.schema.json`, `schema/index-mappings.schema.json`, `schema/index-settings.schema.json`
- Test: `tests/test_config_schema_shipped.py`

**Interfaces:**

- Consumes: `validate_file` from Task 1.
- Produces: schemas addressable by kind: `configuration`, `pipelines`, `enrichment-policies`, `index-mappings`, `index-settings`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_schema_shipped.py`:

```python
"""Every config file both shipped projects run on must validate.

These files have been driving real loads, so a failure here means the schema
is wrong. That direction matters: the shipped configs are the evidence, and a
schema written from the docs rather than the data would quietly reject them.
"""

from pathlib import Path

import pytest

from utils import config_schema

_ROOT = Path(__file__).parent.parent

_KIND_BY_FILENAME = {
    "configuration.json": "configuration",
    "index-config.json": "index-config",
    "index-mappings.json": "index-mappings",
    "index-settings.json": "index-settings",
    "pipelines.json": "pipelines",
    "enrichment-policies.json": "enrichment-policies",
    "entity-match.json": "entity-match",
}


def _shipped_config_files():
    for project in ("DOT-Commercial", "CMS-Providers"):
        yield _ROOT / project / "configuration.json"
        for path in sorted((_ROOT / project / "configuration").rglob("*.json")):
            if path.name in _KIND_BY_FILENAME:
                yield path


@pytest.mark.parametrize(
    "path", list(_shipped_config_files()), ids=lambda p: str(p.relative_to(_ROOT))
)
def test_shipped_config_validates(path):
    kind = _KIND_BY_FILENAME[path.name]
    assert config_schema.validate_file(kind, str(path)) == []


def test_the_sweep_reaches_every_shipped_file():
    # A glob that silently matches nothing would make this whole file vacuous.
    paths = list(_shipped_config_files())
    assert len(paths) >= 20
    assert any(p.name == "entity-match.json" for p in paths)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_config_schema_shipped.py -q`
Expected: FAIL — no schema for `configuration`, and `entity-match` arrives in Task 3.

- [ ] **Step 3: Write `schema/configuration.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "entitopia project configuration",
  "type": "object",
  "additionalProperties": false,
  "required": ["steps", "configurationDir", "dataDir"],
  "properties": {
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "phases"],
        "properties": {
          "name": { "type": "string" },
          "phases": {
            "type": "array",
            "items": {
              "enum": [
                "index-create",
                "index-map",
                "enrichment-policies",
                "pipelines",
                "index-populate",
                "entity-match",
                "validate"
              ]
            },
            "minItems": 1
          }
        }
      }
    },
    "all_phases": { "type": "array", "items": { "type": "string" } },
    "configurationDir": { "type": "string" },
    "dataDir": { "type": "string" },
    "logLevel": { "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] }
  }
}
```

The `phases` enum is closed on purpose: an unrecognized phase name currently reaches the dispatcher's `else` branch and logs an error, and the run continues having silently skipped that work.

- [ ] **Step 4: Write the four envelope schemas**

Each validates only the keys entitopia reads and passes the Elasticsearch DSL through. `schema/index-mappings.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "entitopia index-mappings envelope",
  "description": "Only the envelope. Everything under `mappings` is Elasticsearch's own DSL — owned elsewhere, moving independently, and already rejected loudly by the cluster when wrong. A stale schema over it would reject valid config and teach operators to distrust this validator.",
  "type": "object",
  "additionalProperties": false,
  "required": ["index", "mappings"],
  "properties": {
    "index": { "type": "string" },
    "mappings": { "type": "object" }
  }
}
```

Write `index-settings.schema.json` the same way with `settings` in place of `mappings`, and `pipelines.schema.json` / `enrichment-policies.schema.json` against the actual shipped shapes — **read those files first**; do not assume their envelope.

- [ ] **Step 5: Run the shipped-config test**

Run: `.venv/bin/python -m pytest tests/test_config_schema_shipped.py -q`
Expected: every case passes except `entity-match.json`, which Task 3 adds. Mark it `xfail` for this task only, with a comment naming Task 3, and remove the marker there.

- [ ] **Step 6: Lint, test, commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat: schema the remaining entitopia-owned config kinds

Envelope only for index-mappings, index-settings, pipelines and
enrichment-policies: what is inside them is Elasticsearch's DSL, owned
elsewhere and already rejected loudly by the cluster. The rule is schematize
what entitopia invented, let Elasticsearch reject what Elasticsearch invented.

The phases enum is closed because an unrecognized phase name reaches the
dispatcher's else branch, logs, and lets the run continue with that work
silently skipped."
```

---

### Task 3: Schema for `entity-match.json`

The largest schema, and the one an operator writing a new project spends their time in. Every block Plan 1 introduced lands here.

**Files:**

- Create: `schema/entity-match.schema.json`
- Modify: `tests/test_config_schema_shipped.py` (remove the xfail)
- Test: `tests/test_entity_match_schema.py`

**Interfaces:**

- Consumes: `validate_mapping` from Task 1.
- Produces: schema kind `entity-match`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity_match_schema.py`. Cover, at minimum, one case per trap Plan 1 created:

```python
"""The analysis config, where a typo is most expensive.

Each case here corresponds to a specific way this config has gone wrong or
could: a renamed key silently ignored, a signal type that no longer exists, a
seed naming a signal nobody configured, a clause kind outside the closed menu.
"""

import copy
import json
from pathlib import Path

from utils import config_schema

_SHIPPED = json.loads(
    (
        Path(__file__).parent.parent
        / "DOT-Commercial"
        / "configuration"
        / "chameleon-detection"
        / "entity-match.json"
    ).read_text()
)


def _mutated(**changes):
    raw = copy.deepcopy(_SHIPPED)
    raw.update(changes)
    return raw


def test_the_shipped_config_validates():
    assert config_schema.validate_mapping("entity-match", _SHIPPED, "shipped") == []


def test_a_renamed_signal_key_is_rejected():
    # max_shared_carriers became max_shared_entities. Under a permissive schema
    # the old name is ignored and the limit falls back to DEFAULT_SHARED_LIMIT
    # with nothing reported.
    raw = copy.deepcopy(_SHIPPED)
    signal = next(s for s in raw["signals"] if s["type"] == "shared-token")
    signal["max_shared_carriers"] = signal.pop("max_shared_entities")
    errors = config_schema.validate_mapping("entity-match", raw, "mutated")
    assert any("max_shared_carriers" in e for e in errors)


def test_a_deleted_signal_type_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    next(s for s in raw["signals"] if s["type"] == "shared-token")["type"] = "vin-overlap"
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_an_unknown_clause_kind_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["population"]["selectors"]["out-of-service"] = {"wildcard": {"x": "*"}}
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_an_unknown_population_mode_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["population"]["mode"] = "everything"
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_a_duplicate_detection_config_without_lifecycle_validates():
    # CMS has no dated events. The lifecycle block must be optional, or the
    # all-entities mode this schema is supposed to support cannot be expressed.
    raw = {
        "source_index": "hospitals-000001",
        "entity": {"key": "Facility ID", "summary_fields": ["Facility Name"]},
        "population": {"mode": "all-entities", "sort_field": "Facility ID"},
        "candidates": {"max_candidates": 100, "seed_signals": ["name-phonetic"]},
        "signals": [
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["Facility Name"],
                "subfield": "phonetic",
            }
        ],
        "scoring": {"min_total_score": 0.5, "min_signals": 1},
    }
    assert config_schema.validate_mapping("entity-match", raw, "cms") == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_entity_match_schema.py -q`
Expected: FAIL, no schema for `entity-match`.

- [ ] **Step 3: Write the schema**

Build it against the shipped file and `matching/signals.py`'s registry rather than from memory. Required structure:

- `source_index` (string, required), `source_settings_step` (string).
- `entity`: `key` (required), `key_label`, `summary_fields` (array of strings).
- `lifecycle` (**optional** — absent is how a duplicate-detection project is expressed): `shutdown_date`, `registration_date` required when present; `shutdown_reason` optional.
- `population`: `mode` enum `["lifecycle", "all-entities"]`; `sort_field`; `max_records` integer-or-null; `selector`; `selectors` as an object whose values each declare exactly **one** clause kind from `nested-exists`, `term`, `all`, `any` — express with `minProperties: 1, maxProperties: 1` plus `additionalProperties: false` over those four names.
- `candidates`: `max_candidates` integer, `seed_signals` array of strings.
- `signals`: array where each item's `type` is an enum of the registered names — `name-phonetic`, `name-token`, `address`, `exact-identifier`, `rarity-weighted-value`, `temporal`, `shared-token` — with per-type required keys expressed as an `allOf` of `if/then` clauses. Every item allows `weight` (required), `name`, `conclusive`.
- `ignore_values`, `max_shared_records`: objects keyed by field path.
- `scoring`: `min_total_score`, `min_signals`, `require_identity_signal`, `max_pairs_per_predecessor`, `min_gap_days`, `max_gap_days`.

`additionalProperties: false` at every level entitopia owns, including inside each signal variant.

- [ ] **Step 4: Run both entity-match tests**

Run: `.venv/bin/python -m pytest tests/test_entity_match_schema.py -q`
Expected: PASS. If `test_the_shipped_config_validates` fails, the schema is wrong — that file has been producing the pairs the compatibility gate just certified.

- [ ] **Step 5: Remove the xfail from Task 2's shipped test**

Run: `.venv/bin/python -m pytest tests/test_config_schema_shipped.py -q`
Expected: every shipped file passes with no markers.

- [ ] **Step 6: Lint, test, commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
git add -A
git commit -m "feat: schema entity-match.json, including every block Plan 1 added

The config an operator writing a new project spends their time in, and where a
typo is most expensive. Each rejection test corresponds to a real trap: the
max_shared_carriers rename, the deleted vin-overlap type, a clause kind outside
the closed menu, an unknown population mode.

lifecycle is optional, which is how a duplicate-detection project is expressed
-- CMS has no dated events at all, and a required block would make the
all-entities mode inexpressible."
```

---

### Task 4: Tier 2 — cross-file coherence

Schema validation cannot see relationships between files, or between blocks within one file. These are the checks that catch config which is individually well-formed and jointly incoherent.

**Files:**

- Create: `utils/config_coherence.py`
- Test: `tests/test_config_coherence.py`

**Interfaces:**

- Consumes: nothing from Tasks 1–3 (works on plain dicts).
- Produces: `utils.config_coherence.check(entity_match: dict, source: str) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_coherence.py` covering each rule, one test per rule, each naming the failure it prevents:

```python
"""Config that is individually well-formed and jointly incoherent.

Every rule here corresponds to a configuration that a schema accepts and that
produces a silently degraded sweep — the failure mode this repo keeps hitting,
and the reason a schema alone is not enough.
"""

from utils import config_coherence


def _base():
    return {
        "entity": {"key": "dot_number"},
        "population": {
            "mode": "lifecycle",
            "selector": "out-of-service",
            "selectors": {"out-of-service": {"term": {"a": "b"}}},
        },
        "candidates": {"seed_signals": ["name-phonetic"]},
        "signals": [
            {"type": "name-phonetic", "weight": 0.5, "fields": ["legal_name"],
             "subfield": "phonetic"}
        ],
        "scoring": {},
    }


def test_a_coherent_config_reports_nothing():
    assert config_coherence.check(_base(), "test.json") == []


def test_temporal_without_lifecycle_is_reported():
    # The signal now raises at build time, but reporting it here names the file
    # and the fix instead of surfacing a stack trace mid-sweep.
    raw = _base()
    raw["signals"].append({"type": "temporal", "weight": 0.05, "max_gap_days": 365})
    assert any("lifecycle" in e for e in config_coherence.check(raw, "test.json"))


def test_a_seed_naming_an_unconfigured_signal_is_reported():
    # Silently caps recall at zero for that evidence: the seed matches no
    # configured signal, so no clause is ever built and nothing reports it.
    raw = _base()
    raw["candidates"]["seed_signals"].append("shared-token")
    assert any("shared-token" in e for e in config_coherence.check(raw, "test.json"))


def test_a_selector_naming_an_undefined_entry_is_reported():
    raw = _base()
    raw["population"]["selector"] = "revoked-authority"
    assert any("revoked-authority" in e for e in config_coherence.check(raw, "test.json"))


def test_a_selector_cycle_is_reported():
    raw = _base()
    raw["population"]["selectors"] = {"a": {"all": ["b"]}, "b": {"all": ["a"]}}
    raw["population"]["selector"] = "a"
    assert any("cycle" in e for e in config_coherence.check(raw, "test.json"))


def test_a_gap_window_without_lifecycle_is_reported():
    # Plan 1 made the window depend on the lifecycle block. Setting the window
    # with no block leaves the gate silently off -- an operator's tightening
    # that does nothing.
    raw = _base()
    raw["scoring"] = {"min_gap_days": -180, "max_gap_days": 365}
    assert any("lifecycle" in e for e in config_coherence.check(raw, "test.json"))


def test_all_entities_mode_with_a_lifecycle_is_reported():
    # Not fatal in the matcher, but it means somebody expected succession from
    # a sweep that emits none, which is worth saying out loud.
    raw = _base()
    raw["population"]["mode"] = "all-entities"
    raw["lifecycle"] = {"shutdown_date": "a", "registration_date": "b"}
    assert config_coherence.check(raw, "test.json")


def test_the_shipped_dot_config_is_coherent():
    import json
    from pathlib import Path

    path = (
        Path(__file__).parent.parent / "DOT-Commercial" / "configuration"
        / "chameleon-detection" / "entity-match.json"
    )
    assert config_coherence.check(json.loads(path.read_text()), str(path)) == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_config_coherence.py -q`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `utils/config_coherence.py`**

One small function per rule, each returning zero or one message, composed by `check`. Keep them separate rather than one long function: each rule's docstring is where the incident that motivated it gets recorded, and a combined function has nowhere to put six of them.

Reuse `matching.population.PopulationSelector` for cycle detection rather than reimplementing it — build the selector and call `build_query()`, converting its `ValueError` into a message. Two implementations of "is this selector graph acyclic" would be two places to fix.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_config_coherence.py -q`
Expected: PASS.

- [ ] **Step 5: Lint, test, commit**

---

### Task 5: Tier 3 — live cluster checks

**Files:**

- Create: `utils/config_liveness.py`
- Test: `tests/test_config_liveness.py`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `utils.config_liveness.check(es, entity_match: dict, source: str) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Tests pass a fake ES object returning a canned mapping, so they need no cluster. Cover:

- A signal field path absent from the source mapping is reported.
- A `subfield` a signal names that the mapping does not declare is reported. **This is the highest-value check in the tier** — the README's hazard 3 records analyzers naming columns that no longer exist being silently inert, and hazard-shaped failures like `.phonetic_bm` missing from an older index.
- `entity.key` absent from the mapping is reported.
- `population.sort_field` absent is reported — paging fails outright at sweep time, hours in.
- A field present with an incompatible type is reported (a `term` clause against a `text` field matches zero documents, which the README records as a real defect).
- A correct config against a complete mapping reports nothing.

- [ ] **Step 2: Run it to confirm it fails**

- [ ] **Step 3: Implement `utils/config_liveness.py`**

Read the mapping once with `es.indices.get_mapping(index=...)` and walk it into a flat set of `field` and `field.subfield` names. Do not call the cluster per field — a config with forty field references would make forty round trips for information one call carries.

Do not re-implement the analysis-fingerprint check; `phase_entity_match._check_analysis_fingerprint` already exists and Task 6 calls it.

- [ ] **Step 4: Run the tests, lint, commit**

---

### Task 6: The `validate` phase

**Files:**

- Create: `phase_providers/phase_validate.py`
- Modify: `phase_providers/phase_dispatcher.py`
- Modify: `DOT-Commercial/configuration.json`, `CMS-Providers/configuration.json`
- Test: `tests/test_phase_validate.py`

**Interfaces:**

- Consumes: `config_schema`, `config_coherence`, `config_liveness`.
- Produces: `PhaseValidate(es, project, one_step, project_config)` with `handle()`.

- [ ] **Step 1: Write the failing test**

Cover the behaviors that matter, not the plumbing:

- All three tiers run and their messages are reported together in one report.
- **A later tier does not run when an earlier one failed.** Tier 3 asks the cluster about field paths a tier-1 failure may mean are garbage; running it anyway produces a second page of errors that are consequences of the first.
- The phase **raises** on any failure. The repo's rule is that a phase raises when it cannot fix a problem, and this one never can.
- A clean config produces a log line and no exception.

- [ ] **Step 2: Run it to confirm it fails**

- [ ] **Step 3: Implement `phase_providers/phase_validate.py`**

Docstring must say why the phase exists: config that parses and is inert is this codebase's recurring failure, and every tier here corresponds to a documented incident.

- [ ] **Step 4: Wire it into the dispatcher**

Add one `elif one_phase == "validate"` branch in `phase_dispatcher.py`, alongside the others.

- [ ] **Step 5: Add the phase to both projects' configs**

Add `"validate"` to `all_phases` in both `configuration.json` files, and to the `chameleon-detection` step's `phases` list ahead of `entity-match`.

- [ ] **Step 6: Run it against both shipped projects, for real**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection --phase=validate
.venv/bin/python execute_project.py --project=CMS-Providers --step=hospitals --phase=validate
```

Expected: both clean. **A failure here is the validator being wrong**, not the config — DOT's config produced the pairs the compatibility gate certified two days ago, and CMS's has been loading 5.6M rows.

- [ ] **Step 7: Prove it catches something real**

Temporarily rename `max_shared_entities` back to `max_shared_carriers` in DOT's config, run the validate phase, confirm it fails naming that key and file, then revert. Record the actual output in the commit message. A validator nobody has seen fail is a validator nobody should trust.

- [ ] **Step 8: Lint, full test run, commit**

---

## Self-Review

**Spec coverage.** Rollout step 7 asks for `schema/`, a three-tier `validate` phase, tiers 1–2 runnable without a cluster, and the fingerprint check promoted from a buried preflight. Tasks 1–3 cover the schemas, Task 4 tier 2, Task 5 tier 3, Task 6 the phase and its wiring. Tiers 1 and 2 take dicts and a fake ES object respectively, so both run in CI.

**One spec item deliberately narrowed:** the spec said tier 3 checks "the source index's analysis fingerprint matches the configured analyzers", described as promoting `_check_analysis_fingerprint` out of `entity-match`. Task 6 _calls_ it rather than moving it. Moving it would change `entity-match`'s preflight, which the compatibility gate certified two days ago and which this plan's constraints put out of scope. Recorded here rather than silently done differently.

**Placeholder scan.** Task 3 step 3 and Task 5 step 3 describe schema and code structure rather than giving complete literal content — the entity-match schema is several hundred lines and must be written against the live registry rather than transcribed from a plan, and Task 5's shape depends on the mapping structure the cluster actually returns. Both name their source of truth and their acceptance test. Every other step carries literal content.

**Type consistency.** `validate_mapping(kind, raw, source) -> list[str]` and `validate_file(kind, path) -> list[str]` (Task 1) are the only signatures Tasks 2–3 use. `config_coherence.check(entity_match, source)` (Task 4) and `config_liveness.check(es, entity_match, source)` (Task 5) return the same `list[str]`, which is what lets Task 6 concatenate all three into one report.

**Ordering hazard.** Task 2's shipped-config test covers `entity-match.json`, whose schema arrives in Task 3. Task 2 marks that one case `xfail` and Task 3 removes the marker. Running Task 3 before Task 2 leaves the marker unwritten and the suite red.
