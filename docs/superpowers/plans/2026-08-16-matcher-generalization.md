# Matcher Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `matching/` dataset-agnostic so `entity-match` runs on any project by configuration, and prove DOT-Commercial's sweep is unchanged.

**Architecture:** Parameterize in place. The entity key name, the population definition, the lifecycle date paths, and the pair summary shape all move out of Python literals and into `entity-match.json`. Selectors become named config entries assembled from a closed four-clause menu in framework code. Nothing about scoring arithmetic changes — that is what the compatibility gate protects.

**Tech Stack:** Python 3.11+, Elasticsearch 9.4.1 (`elasticsearch-py` 9.4.1), pytest, ruff.

This plan covers rollout steps 1–6 of [the spec](../specs/2026-08-16-config-driven-analysis-portability-design.md). Steps 7–11 (schema/validate phase, metrics DSL, scaffold generator, CMS instance, docs) are separate plans that depend on this one.

## Global Constraints

- **Everything runs from `.venv`.** Never `python3` or `pip3`. Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/python -m ruff check .`
- **`ruff check .` must print `All checks passed!`** before any commit. Exemptions need a written reason at the narrowest scope.
- **Comments explain why, not what.** Every function, class, and module gets a comment stating why it exists and what breaks if it is wrong. Never narrate the steps the code takes.
- **Never name a real flagged entity** in code, comments, config, docs, or commit messages. Use synthetic placeholders. Data-quality junk values (`GGGG`, `UNKNOWN`, `(000) 000-0000`) and aggregate counts are fine.
- **Elasticsearch calls pass explicit keyword arguments, never `body=`.**
- **Config objects are `SimpleNamespace`**, loaded via `file_utils.load_from_file`. Use attribute access and `getattr(obj, "key", default)` for optional keys.
- **Do not change scoring arithmetic, weights, thresholds, renormalization, `conclusive` handling, or `min_signals` counting.** If a task seems to require it, stop and raise it.
- **Branch:** `config-driven-analysis-portability`. Commit after every task.
- **Every task leaves `pytest` green and the DOT sweep runnable.**

---

## File Structure

**Framework code being modified:**

| File                                    | Responsibility after this plan                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `matching/documents.py`                 | `EntityDoc` (dataset-agnostic), `read_path`, `ScoringContext`, `FieldRarityTable`                            |
| `matching/population.py`                | **New** (replaces `predecessors.py`). Population selection: clause menu + config-defined selectors + paging  |
| `matching/candidates.py`                | Candidate retrieval; builds `EntityDoc` using the configured entity key                                      |
| `matching/scorer.py`                    | Pair scoring. Only the entity-key comparison and the lifecycle date source change                            |
| `matching/signals.py`                   | Signal implementations. `agent` → `rarity-weighted-value`; `vin-overlap` deleted; temporal reads `lifecycle` |
| `phase_providers/phase_entity_match.py` | Sweep orchestration; config-driven summary and gap emission                                                  |

**Project code being moved (framework → project):**

| From                                 | To                                                  |
| ------------------------------------ | --------------------------------------------------- |
| `utils/crash_lift.py`                | `DOT-Commercial/crash_lift.py`                      |
| `scripts/measure_crash_lift.py`      | `DOT-Commercial/scripts/measure_crash_lift.py`      |
| `scripts/measure_chameleon_shape.py` | `DOT-Commercial/scripts/measure_chameleon_shape.py` |
| `tests/test_crash_lift.py`           | stays; import mechanism changes                     |

**Critical constraint on that move:** `DOT-Commercial` contains a hyphen, so it can never be a dotted Python package. `from utils.crash_lift import ...` will not become `from DOT-Commercial.crash_lift import ...`. Consumers must load it by path with `importlib.util`, which is the convention `tests/test_dot_commercial_precision_metrics.py` already documents and uses.

**New scripts:**

| File                          | Responsibility                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------- |
| `scripts/compare_pair_ids.py` | Hash and diff the `_id` set of two candidate indexes — the gate's population-identity check |

---

### Task 1: Relocate DOT-specific measurement code out of the framework

`scripts/measure_crash_lift.py` (37KB, 157 domain references), `scripts/measure_chameleon_shape.py`, and `utils/crash_lift.py` are FMCSA-specific but sit in framework directories. Moving them is pure relocation with no behavior change, which makes it the safest first commit.

**Files:**

- Move: `utils/crash_lift.py` → `DOT-Commercial/crash_lift.py`
- Move: `scripts/measure_crash_lift.py` → `DOT-Commercial/scripts/measure_crash_lift.py`
- Move: `scripts/measure_chameleon_shape.py` → `DOT-Commercial/scripts/measure_chameleon_shape.py`
- Modify: `tests/test_crash_lift.py` (import mechanism)
- Modify: `utils/mapping_coverage.py`, `utils/sweep_compare.py`, `DOT-Commercial/precision_metrics.py` (docstring path references only)

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `DOT-Commercial/crash_lift.py` exposing the same names it exposes today (`GAP_BANDS`, `SCORE_BANDS`, `band_for`, and the rest of its public surface — do not rename anything inside it).

- [ ] **Step 1: Read the current import surface before moving anything**

Run:

```bash
.venv/bin/python -m pytest tests/test_crash_lift.py -q
grep -rn "crash_lift" --include="*.py" . | grep -v __pycache__ | grep -v "^./.venv"
```

Expected: tests pass, and the grep lists exactly the consumers named in the Files section. If it lists a consumer not named here, add it to the task rather than guessing.

- [ ] **Step 2: Move the three files with `git mv`**

```bash
mkdir -p DOT-Commercial/scripts
git mv utils/crash_lift.py DOT-Commercial/crash_lift.py
git mv scripts/measure_crash_lift.py DOT-Commercial/scripts/measure_crash_lift.py
git mv scripts/measure_chameleon_shape.py DOT-Commercial/scripts/measure_chameleon_shape.py
```

- [ ] **Step 3: Run the tests to see exactly what breaks**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.crash_lift'`. This failure is the point — it confirms the dotted import was real and has to be replaced.

- [ ] **Step 4: Convert `tests/test_crash_lift.py` to path-based loading**

Replace the `from utils.crash_lift import (...)` block near line 16 with a path load, mirroring the convention already used further down the same file for `measure_crash_lift`:

```python
# DOT-Commercial/ cannot be a dotted module because of the hyphen, so this
# loads by path -- the same convention tests/test_dot_commercial_precision_metrics.py
# uses and for the same reason. Moving crash_lift.py into the project it
# belongs to is what forced this; a dotted import is not available at any
# price short of renaming the project directory.
_CRASH_LIFT = Path(__file__).parent.parent / "DOT-Commercial" / "crash_lift.py"


def _load_crash_lift():
    spec = importlib.util.spec_from_file_location("crash_lift", _CRASH_LIFT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crash_lift = _load_crash_lift()
```

Then rewrite each bare use of an imported name to go through `crash_lift.` (for example `band_for(...)` becomes `crash_lift.band_for(...)`). Do not rename anything inside `crash_lift.py` itself.

- [ ] **Step 5: Update the path for `measure_crash_lift` in the same test**

At line ~240, change:

```python
_MEASURE_CRASH_LIFT = Path(__file__).parent.parent / "DOT-Commercial" / "scripts" / "measure_crash_lift.py"
```

- [ ] **Step 6: Fix the sys.path bootstrap inside both moved scripts**

`measure_crash_lift.py` and `measure_chameleon_shape.py` each carry a comment and a `sys.path` insert explaining that running as `.venv/bin/python scripts/<name>.py` puts `scripts/` on the path rather than the repo root. They are now two directories deeper. Change each insert to resolve the repo root from the new location:

```python
# Runs as `.venv/bin/python DOT-Commercial/scripts/measure_crash_lift.py`, which
# puts DOT-Commercial/scripts/ on sys.path rather than the repo root, so the
# framework packages (utils, matching) are unimportable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
```

Then change their `from utils.crash_lift import (...)` to load `DOT-Commercial/crash_lift.py` by path, using the same `_load_crash_lift()` shape as Step 4 but resolving `Path(__file__).resolve().parent.parent / "crash_lift.py"`.

- [ ] **Step 7: Update the stale path references in docstrings**

These three files name the old paths in prose and would now be lying:

- `utils/mapping_coverage.py:10` — "a field utils/crash_lift.py documents as depending on `long`"
- `utils/sweep_compare.py:13-14` — "keeps utils/crash_lift.py testable while scripts/measure_crash_lift.py stays integration-shaped"
- `DOT-Commercial/precision_metrics.py:11-12` — same sentence

Update each to the new path. In `sweep_compare.py` and `precision_metrics.py` the sentence is making a point about a testable-core/integration-shell split, so keep the point and only correct the paths.

- [ ] **Step 8: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

Expected: all tests pass, `All checks passed!`.

- [ ] **Step 9: Verify the moved scripts still start**

Run:

```bash
.venv/bin/python DOT-Commercial/scripts/measure_crash_lift.py --help
.venv/bin/python DOT-Commercial/scripts/measure_chameleon_shape.py --help
```

Expected: each prints its argparse help rather than an ImportError. A passing test suite does not cover the `sys.path` bootstrap, because pytest runs from the repo root and the scripts do not.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: move crash-lift measurement into the project it measures

utils/crash_lift.py and two measure scripts carry FMCSA vocabulary but sat in
framework directories, contradicting the rule that everything under the repo
root is generic. Pure relocation, no behavior change.

DOT-Commercial cannot be a dotted package because of the hyphen, so the three
consumers load crash_lift.py by path instead -- the convention
test_dot_commercial_precision_metrics.py already documents. The sys.path
bootstrap inside both scripts moved two directories deeper with them."
```

---

### Task 2: Rename `CarrierDoc` to `EntityDoc` with a configurable key

`CarrierDoc.dot_number` hardcodes the entity key name into framework code. It is read in `candidates.py:162`, `scorer.py:162`, and three places in `phase_entity_match.py`.

**Files:**

- Modify: `matching/documents.py:12-37`
- Modify: `matching/candidates.py:14,114-115,153-165`
- Modify: `matching/scorer.py:162`
- Modify: `phase_providers/phase_entity_match.py:629,638,700,715`
- Test: `tests/test_entity_doc.py` (create)

**Interfaces:**

- Consumes: nothing from Task 1.
- Produces:
  - `matching.documents.EntityDoc(entity_key: str, source: dict, tokens: dict[str, set[str]])` with the same `token_set(field_name, subfield)` and `value(path)` methods `CarrierDoc` has today.
  - `matching.candidates.CandidateFinder.__init__(es, source_index, candidates_config, signal_configs, entity_config=None)` — `entity_config` is a `SimpleNamespace` with an optional `key` attribute, defaulting to `"dot_number"` in this task only. Task 5 removes that default.

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity_doc.py`:

```python
"""EntityDoc's key is configuration, not a field name baked into the framework.

The whole premise of the project is that a new dataset is onboarded by writing
JSON. A hardcoded `dot_number` attribute means every project's records must
pretend to be FMCSA carriers, so these tests pin the key as data.
"""

from matching.candidates import to_entity_doc
from matching.documents import EntityDoc


def test_entity_doc_exposes_its_key_generically():
    doc = EntityDoc(entity_key="12345", source={"dot_number": "12345"}, tokens={})
    assert doc.entity_key == "12345"


def test_entity_doc_has_no_dot_number_attribute():
    # An alias would let DOT vocabulary re-enter framework code and give two
    # names for one value with no rule about which to use.
    doc = EntityDoc(entity_key="12345", source={}, tokens={})
    assert not hasattr(doc, "dot_number")


def test_to_entity_doc_reads_the_configured_key_field():
    hit = {"_id": "abc", "_source": {"Facility ID": "010001", "dot_number": "999"}}
    doc = to_entity_doc(hit, tokens={}, key_field="Facility ID")
    assert doc.entity_key == "010001"


def test_to_entity_doc_falls_back_to_the_es_id():
    # The near-empty dev index carries probe documents with no key field; this
    # keeps the sweep usable against one rather than raising on test data.
    hit = {"_id": "abc", "_source": {}}
    doc = to_entity_doc(hit, tokens={}, key_field="Facility ID")
    assert doc.entity_key == "abc"


def test_to_entity_doc_stringifies_a_numeric_key():
    # dot_number arrives as a JSON integer from some indexes and a string from
    # others; pairs keyed on it must not depend on which.
    hit = {"_id": "abc", "_source": {"dot_number": 23680}}
    doc = to_entity_doc(hit, tokens={}, key_field="dot_number")
    assert doc.entity_key == "23680"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_entity_doc.py -q`
Expected: FAIL with `ImportError: cannot import name 'EntityDoc'`.

- [ ] **Step 3: Rename the dataclass in `matching/documents.py`**

Replace the `CarrierDoc` class (lines 12–37) with:

```python
@dataclass
class EntityDoc:
    """Pairs one record's raw _source with its Elasticsearch-analyzed tokens.

    Tokens are read from _mtermvectors rather than recomputed in Python so
    that scoring always sees the same phonetic encodings and synonym
    expansions the index actually produced, not a local approximation of them.

    `entity_key` is named generically on purpose. It used to be `dot_number`,
    which meant every project's records had to pretend to be FMCSA carriers
    and made `entity-match` unreachable by configuration. There is deliberately
    no `dot_number` alias: two names for one value gives new code no rule about
    which to reach for, which is how the vocabulary got in here originally.
    """

    entity_key: str
    source: dict
    # Keyed "field.subfield", e.g. "legal_name.phonetic_bm"
    tokens: dict[str, set[str]] = field(default_factory=dict)

    def token_set(self, field_name: str, subfield: str) -> set[str]:
        """Tokens for one analyzed field, or an empty set if never indexed.

        Signals intersect sets freely; returning empty rather than raising on
        a missing field lets "not indexed for this record" be treated the
        same as "no overlap" without every caller needing a try/except.
        """
        return self.tokens.get("{}.{}".format(field_name, subfield), set())

    def value(self, path: str):
        """Read a dotted path out of this document's _source."""
        return read_path(self.source, path)
```

Also update the module docstring's first paragraph to say `EntityDoc` rather than `CarrierDoc`, and `read_path`'s docstring where it says "there is no CarrierDoc yet".

- [ ] **Step 4: Replace `_to_carrier_doc` in `matching/candidates.py`**

Delete `_to_carrier_doc` (lines 153–165) and add a public `to_entity_doc` — public because the test imports it and because it is the one place the key field is applied:

```python
def to_entity_doc(hit, tokens, key_field):
    """Combine a search hit's _source with its fetched tokens into an EntityDoc.

    key_field is configuration rather than a literal: it is the column a
    project calls its entity's identity, and framework code cannot know it.

    Falls back to the Elasticsearch _id when _source lacks that column (the
    probe documents in a near-empty dev index do), so this stays usable
    against a sparsely-populated index rather than raising on test data.
    Stringified because the same logical key arrives as a JSON integer from
    some indexes and a string from others, and a pair keyed on it must not
    depend on which.
    """
    source = hit["_source"]
    return EntityDoc(
        entity_key=str(source.get(key_field, hit["_id"])),
        source=source,
        tokens=tokens,
    )
```

Change the import at line 14 to `from matching.documents import EntityDoc`.

- [ ] **Step 5: Thread the key field through `CandidateFinder`**

In `__init__`, accept and store the entity config:

```python
    def __init__(self, es, source_index, candidates_config, signal_configs, entity_config=None):
```

and inside it:

```python
        # The column this project calls its entity's identity. Defaulted here
        # only so this task does not have to update every caller at once;
        # Task 5 makes `entity.key` required and removes the fallback.
        self.entity_key_field = getattr(entity_config, "key", "dot_number")
```

In `find()`, replace the two `_to_carrier_doc` calls (lines 114–115):

```python
        pred_doc = to_entity_doc(pred_hit, tokens_by_id.get(pred_id, {}), self.entity_key_field)
        cand_docs = [
            to_entity_doc(h, tokens_by_id.get(h["_id"], {}), self.entity_key_field)
            for h in hits
        ]
```

- [ ] **Step 6: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_entity_doc.py -q`
Expected: PASS.

- [ ] **Step 7: Update the remaining readers**

- `matching/scorer.py:162`: `if pred.entity_key == cand.entity_key:`
- `matching/scorer.py` type hints on `score_pair` and the `ScoredPair` dataclass: `CarrierDoc` → `EntityDoc`, and the import at the top.
- `matching/signals.py:96` type hints on `Signal.score`: `CarrierDoc` → `EntityDoc`, and the import.
- `phase_providers/phase_entity_match.py:629`: `pred_doc.entity_key, cand_doc.entity_key`
- `phase_providers/phase_entity_match.py:638`: `key = (pair.predecessor.entity_key, pair.successor.entity_key)`
- `phase_providers/phase_entity_match.py:700`: `{"p": pred.entity_key, "s": succ.entity_key}` — **leave the literal `"p"` and `"s"` dict keys alone**, they are what keeps `_id` stable across this rename.
- `phase_providers/phase_entity_match.py:715`: leave the emitted `"dot_number"` key for now; Task 3 replaces it.

Find any remaining references with:

```bash
grep -rn "CarrierDoc\|\.dot_number" --include="*.py" matching/ phase_providers/ utils/ scripts/ tests/ | grep -v __pycache__
```

Expected after this step: only `phase_entity_match.py:715` (handled in Task 3) and DOT-Commercial project files, which read the emitted document rather than the dataclass.

- [ ] **Step 8: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

Expected: all pass. `tests/test_scorer.py` and `tests/test_signals.py` construct docs directly and will need `CarrierDoc(dot_number=...)` changed to `EntityDoc(entity_key=...)`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: name the entity key generically in matching/

CarrierDoc.dot_number required every project's records to pretend to be FMCSA
carriers, which is what made entity-match unreachable by configuration. The
key column is now read from entity.key config.

No dot_number alias on the dataclass: two names for one value gives new code
no rule about which to reach for, which is how the vocabulary got in here.
The emitted _id is unaffected -- it composes literal p/s keys, not the label."
```

---

### Task 3: Emit `entity_key`, the labelled copy, and configurable summary fields

`_carrier_summary` emits a fixed six-field summary, so a non-FMCSA project would produce pairs carrying `legal_name` and `phy_state` keys holding nulls.

**Files:**

- Modify: `phase_providers/phase_entity_match.py:672-728`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Test: `tests/test_pair_summary.py` (create)

**Interfaces:**

- Consumes: `EntityDoc.entity_key` from Task 2.
- Produces: `phase_entity_match._entity_summary(doc, entity_config, extra=None) -> dict`, where `entity_config` has `key`, optional `key_label`, and `summary_fields: list[str]`; `extra` is a dict merged in for lifecycle fields (Task 4 supplies it).

- [ ] **Step 1: Write the failing test**

Create `tests/test_pair_summary.py`:

```python
"""The emitted pair summary is configuration, not a fixed FMCSA field list.

A pair document is routinely read on its own -- pulled by _id, exported into a
review sample, quoted in a README -- so it has to be self-describing without
the project config in the reader's hands. That is why the generic entity_key
and the project's own label are both emitted rather than either alone.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from matching.documents import EntityDoc

_PHASE = Path(__file__).parent.parent / "phase_providers" / "phase_entity_match.py"


def _load_phase():
    spec = importlib.util.spec_from_file_location("phase_entity_match", _PHASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase = _load_phase()


def _doc():
    return EntityDoc(
        entity_key="12345",
        source={"legal_name": "ACME EXAMPLE", "phy_city": "SPRINGFIELD", "unused": "x"},
        tokens={},
    )


def test_summary_emits_the_generic_key():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["entity_key"] == "12345"


def test_summary_emits_the_project_label_as_a_copy():
    config = SimpleNamespace(
        key="dot_number", key_label="dot_number", summary_fields=["legal_name"]
    )
    summary = phase._entity_summary(_doc(), config)
    assert summary["dot_number"] == "12345"
    assert summary["entity_key"] == "12345"


def test_summary_omits_the_label_when_unset():
    # A project with no label gets entity_key only, not a null-valued key that
    # would later read as a real absent value.
    config = SimpleNamespace(key="Facility ID", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config)
    assert "dot_number" not in summary
    assert list(summary) == ["entity_key", "legal_name"]


def test_summary_includes_only_configured_fields():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name", "phy_city"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["legal_name"] == "ACME EXAMPLE"
    assert summary["phy_city"] == "SPRINGFIELD"
    assert "unused" not in summary


def test_summary_keeps_a_configured_field_that_is_absent():
    # Absent must stay distinguishable from "not configured": a reviewer
    # reading one pair needs to know the field was asked for and empty.
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name", "dba_name"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["dba_name"] is None


def test_extra_fields_are_merged():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config, extra={"shutdown_date": "2021-05-01"})
    assert summary["shutdown_date"] == "2021-05-01"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_pair_summary.py -q`
Expected: FAIL with `AttributeError: module 'phase_entity_match' has no attribute '_entity_summary'`.

- [ ] **Step 3: Replace `_carrier_summary`**

Delete `_carrier_summary` (lines 706–728) and add:

```python
def _entity_summary(doc, entity_config, extra=None):
    """Trim an EntityDoc to the human-facing fields a reviewer needs to judge a pair.

    The output document exists to be read by a person deciding whether a
    flagged pair is real, not to carry the full record already available in
    the source index; keeping this list short is what keeps a reviewed hit
    list scannable. Which fields those are is a project's own decision, so it
    is read from entity.summary_fields rather than fixed here -- the previous
    fixed list emitted FMCSA column names, so any other project's pairs would
    have carried six keys holding nulls.

    Both `entity_key` and the project's own label are emitted when a label is
    configured. Both rather than either: generic tooling reads `entity_key`
    without loading project config, while the labelled copy keeps existing
    project scripts, baselines, and README figures working unchanged.

    A configured field that is absent is emitted as None rather than dropped,
    so a reviewer can tell "asked for and empty" from "never asked for".
    """
    summary = {"entity_key": doc.entity_key}
    label = getattr(entity_config, "key_label", None)
    if label:
        summary[label] = doc.entity_key
    for path in getattr(entity_config, "summary_fields", []) or []:
        summary[path] = doc.value(path)
    if extra:
        summary.update(extra)
    return summary
```

- [ ] **Step 4: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_pair_summary.py -q`
Expected: PASS.

- [ ] **Step 5: Update the two call sites in `_pair_doc`**

At lines 673–674, replace:

```python
            "predecessor": _entity_summary(
                pred, self.entity_config, extra={"shutdown_date": shutdown, "shutdown_reason": ...}
            ),
```

For this task keep the existing shutdown/registration extras exactly as they are — Task 4 makes them config-driven. Concretely: build the `extra` dicts from the same values `_carrier_summary` computed before (`shutdown_date`, `shutdown_reason` for the predecessor; `add_date` for the successor), and pass them through. Store `self.entity_config = getattr(config, "entity", SimpleNamespace())` wherever the phase reads its other config blocks.

- [ ] **Step 6: Add the `entity` block to DOT's config**

In `DOT-Commercial/configuration/chameleon-detection/entity-match.json`, add above `"predecessors"`:

```json
  "entity": {
    "key": "dot_number",
    "key_label": "dot_number",
    "summary_fields": ["legal_name", "dba_name", "phy_street", "phy_city", "phy_state"]
  },
```

This reproduces the previous fixed list exactly. Verify against `_carrier_summary`'s old body before committing — a field dropped here is a field missing from every future pair.

- [ ] **Step 7: Pass the entity config into `CandidateFinder`**

Find where the phase constructs `CandidateFinder` and add `entity_config=self.entity_config`.

- [ ] **Step 8: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: drive the emitted pair summary from entity config

_carrier_summary emitted a fixed six-field FMCSA list, so any other project's
pairs would have carried six keys holding nulls. The field list is now
entity.summary_fields, and DOT's block reproduces the previous list exactly.

Every side of a pair now carries entity_key plus, when configured, a copy
under the project's own label. Both rather than either, for the reason the
provenance work already established: a pair is routinely read on its own, and
at that point the project config is not in the reader's hands."
```

---

### Task 4: `PopulationSelector` with a config-defined selector menu

`matching/predecessors.py` hardcodes FMCSA field paths, the literal `"REVOKED"`, a `dot_number` sort, and four selectors that are FMCSA's notions of "shut down". This is the riskiest change in the plan, so it is guarded by an equivalence test that compares generated queries against today's hardcoded output.

**Files:**

- Create: `matching/population.py`
- Delete: `matching/predecessors.py` (after the equivalence test passes)
- Modify: `phase_providers/phase_entity_match.py` (import and construction)
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Test: `tests/test_population.py` (create), `tests/test_selector_equivalence.py` (create), `tests/test_predecessors.py` (delete after porting)

**Interfaces:**

- Consumes: nothing from Tasks 1–3.
- Produces: `matching.population.PopulationSelector(es, source_index, config)` where `config` is the `population` block. Methods: `build_query() -> dict | None` (None in `all-entities` mode, meaning match-all), and `iterate()` yielding hits, same contract as `PredecessorSelector.iterate` today.

- [ ] **Step 1: Write the equivalence test first**

This is the highest-value test in the plan: it pins the new config-driven queries against the existing hardcoded ones without needing a cluster. Create `tests/test_selector_equivalence.py`:

```python
"""The config-defined DOT selectors must build the queries the code built.

The selector decides the population everything downstream scores, so an error
here does not fail -- it quietly changes what the whole sweep is about. This
compares the new config-driven output against the old hardcoded implementation
directly, so the riskiest part of the generalization is verified before any
sweep runs.

Delete this file once matching/predecessors.py is gone and the compatibility
gate has passed; it exists to bridge one refactor, not to be maintained.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.population import PopulationSelector
from matching.predecessors import PredecessorSelector

_DOT_CONFIG = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)

# The old hardcoded selector's knobs, as DOT-Commercial actually sets them.
_OLD_KWARGS = {"oos_status": ["ACTIVE"], "oos_date_from": "2020-01-01"}


def _shipped_population_config():
    raw = json.loads(_DOT_CONFIG.read_text())
    return json.loads(
        json.dumps(raw["population"]), object_hook=lambda d: SimpleNamespace(**d)
    )


def _old_query(selector_name):
    config = SimpleNamespace(selector=selector_name, **_OLD_KWARGS)
    return PredecessorSelector(es=None, source_index="carriers-000001", config=config).build_query()


def _new_query(selector_name):
    config = _shipped_population_config()
    config.selector = selector_name
    return PopulationSelector(es=None, source_index="carriers-000001", config=config).build_query()


@pytest.mark.parametrize(
    "selector_name", ["out-of-service", "revoked-authority", "both", "either"]
)
def test_config_selector_matches_the_hardcoded_one(selector_name):
    assert _new_query(selector_name) == _old_query(selector_name)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_selector_equivalence.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'matching.population'`.

- [ ] **Step 3: Write `tests/test_population.py` for the clause menu itself**

```python
"""The clause menu is closed, and each kind builds one specific query shape.

nested-exists is a single primitive rather than three composable ones because
flattening is a known defect: under an object mapping, a record with an ACTIVE
2015 order and an INACTIVE 2022 order satisfied status=ACTIVE and
oos_date >= 2020 from two different orders and was swept even though no single
order qualified. Making nesting the only shape means no project can
reintroduce that by writing config that looks reasonable.
"""

from types import SimpleNamespace

import pytest

from matching.population import PopulationSelector


def _ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def selector(**population):
    return PopulationSelector(
        es=None, source_index="idx", config=_ns(population)
    )


def test_nested_exists_puts_every_filter_inside_one_element():
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={
            "s": {
                "nested-exists": {
                    "path": "orders",
                    "require": "closed_date",
                    "terms": {"status": ["ACTIVE"]},
                    "range": {"closed_date": {"gte": "2020-01-01"}},
                }
            }
        },
    ).build_query()
    assert query["nested"]["path"] == "orders"
    must = query["nested"]["query"]["bool"]["must"]
    assert {"exists": {"field": "orders.closed_date"}} in must
    assert {"terms": {"orders.status": ["ACTIVE"]}} in must
    assert {"range": {"orders.closed_date": {"gte": "2020-01-01"}}} in must


def test_optional_filters_are_omitted_when_unset():
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={"s": {"nested-exists": {"path": "orders", "require": "closed_date"}}},
    ).build_query()
    assert query["nested"]["query"]["bool"]["must"] == [
        {"exists": {"field": "orders.closed_date"}}
    ]


def test_term_clause_is_not_nested():
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={"s": {"term": {"history.disposition": "REVOKED"}}},
    ).build_query()
    assert query == {"bool": {"must": [{"term": {"history.disposition": "REVOKED"}}]}}


def test_all_intersects_named_selectors():
    query = selector(
        mode="lifecycle",
        selector="both",
        selectors={
            "a": {"term": {"x": "1"}},
            "b": {"term": {"y": "2"}},
            "both": {"all": ["a", "b"]},
        },
    ).build_query()
    assert len(query["bool"]["must"]) == 2


def test_any_unions_named_selectors():
    query = selector(
        mode="lifecycle",
        selector="either",
        selectors={
            "a": {"term": {"x": "1"}},
            "b": {"term": {"y": "2"}},
            "either": {"any": ["a", "b"]},
        },
    ).build_query()
    assert query["bool"]["minimum_should_match"] == 1
    assert len(query["bool"]["should"]) == 2


def test_all_entities_mode_has_no_query():
    # A duplicate-detection project sweeps every record; None means match_all
    # rather than an empty filter that would silently select nothing.
    assert selector(mode="all-entities").build_query() is None


def test_unknown_selector_name_is_refused():
    with pytest.raises(ValueError, match="unknown selector"):
        selector(mode="lifecycle", selector="nope", selectors={"s": {"term": {"x": "1"}}}).build_query()


def test_unknown_clause_kind_is_refused():
    # The menu is closed on purpose: an unrecognized kind must fail loudly
    # rather than contribute nothing and change the swept population.
    with pytest.raises(ValueError, match="unknown clause kind"):
        selector(
            mode="lifecycle", selector="s", selectors={"s": {"wildcard": {"x": "*"}}}
        ).build_query()


def test_a_selector_cycle_is_refused():
    with pytest.raises(ValueError, match="cycle"):
        selector(
            mode="lifecycle",
            selector="a",
            selectors={"a": {"all": ["b"]}, "b": {"all": ["a"]}},
        ).build_query()
```

- [ ] **Step 4: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_population.py -q`
Expected: FAIL, module not found.

- [ ] **Step 5: Implement `matching/population.py`**

```python
"""Population selection: which records the sweep treats as its starting set.

The population decides what everything downstream scores, so an error here
does not fail -- it quietly changes what the whole sweep is about. That is why
the clause menu is closed and an unrecognized kind raises rather than
contributing nothing.

Two modes. `lifecycle` selects records carrying a shutdown-shaped event and is
what succession detection needs. `all-entities` sweeps everything, which is the
only honest option for a project whose data carries no lifecycle events at all
-- there, a pair asserts resemblance, not succession.

Selector definitions live in project configuration rather than here. They used
to be four hardcoded FMCSA queries, which meant using entity-match on another
dataset required editing framework code. The clause vocabulary stays closed and
code-backed: `nested-exists`, `term`, `all`, `any`, and nothing else.
"""

import logging

logger = logging.getLogger(__name__)

MODES = frozenset({"lifecycle", "all-entities"})

PAGE_SIZE = 500


class PopulationSelector:
    """Builds the population query and walks the matching records.

    Which records count as the starting population is a policy call the caller
    makes via config, not something this module should decide -- hence a
    selector menu rather than one query.
    """

    def __init__(self, es, source_index, config):
        self.es = es
        self.source_index = source_index
        self.mode = getattr(config, "mode", "lifecycle")
        if self.mode not in MODES:
            raise ValueError(
                "unknown population mode {!r}; known modes are {}".format(
                    self.mode, ", ".join(sorted(MODES))
                )
            )
        self.selector = getattr(config, "selector", None)
        selectors = getattr(config, "selectors", None)
        self.selectors = vars(selectors) if selectors is not None else {}
        self.max_records = getattr(config, "max_records", None)
        # Paging under a point-in-time needs a stable total order. Which field
        # provides it is per-project; a sort on a missing field fails the
        # search outright rather than silently reordering, which is the
        # behavior we want.
        self.sort_field = getattr(config, "sort_field", None)

    def build_query(self):
        """Compose the selected population's query, or None to match everything.

        None rather than {"match_all": {}} so the caller can tell "sweep
        everything" from "a filter that happened to select everything" -- the
        two are the same result and very different intents.
        """
        if self.mode == "all-entities":
            return None
        if self.selector is None:
            raise ValueError("population.selector is required in lifecycle mode")
        return self._resolve(self.selector, seen=())

    def _resolve(self, name, seen):
        """Build one named selector, following `all`/`any` references.

        `seen` is a tuple rather than a set so the cycle message can name the
        path that closed the loop; a cycle would otherwise recurse until the
        interpreter's stack limit and report nothing useful.
        """
        if name in seen:
            raise ValueError(
                "selector cycle: {}".format(" -> ".join([*seen, name]))
            )
        if name not in self.selectors:
            raise ValueError(
                "unknown selector {!r}; defined selectors are {}".format(
                    name, ", ".join(sorted(self.selectors)) or "(none)"
                )
            )
        definition = self.selectors[name]
        kinds = list(vars(definition))
        if len(kinds) != 1:
            raise ValueError(
                "selector {!r} must declare exactly one clause kind, found {}".format(
                    name, ", ".join(kinds) or "(none)"
                )
            )
        kind = kinds[0]
        body = getattr(definition, kind)
        if kind == "nested-exists":
            return self._nested_exists(body)
        if kind == "term":
            field, value = next(iter(vars(body).items()))
            return {"bool": {"must": [{"term": {field: value}}]}}
        if kind == "all":
            return {"bool": {"must": [self._resolve(n, (*seen, name)) for n in body]}}
        if kind == "any":
            return {
                "bool": {
                    "should": [self._resolve(n, (*seen, name)) for n in body],
                    "minimum_should_match": 1,
                }
            }
        raise ValueError(
            "unknown clause kind {!r} in selector {!r}; known kinds are "
            "nested-exists, term, all, any".format(kind, name)
        )

    def _nested_exists(self, body):
        """Records with a single array element matching every filter.

        Nested rather than a plain bool over dotted paths because an object
        mapping matches each filter against the flattened union of all a
        record's elements: a record with an ACTIVE 2015 order and an INACTIVE
        2022 order satisfied status=ACTIVE and date>=2020 from two different
        orders and was swept even though no single order qualified. That also
        let the temporal signal report a date from an element the selector
        never intended to match, so a pair's gap described the wrong event.
        """
        path = body.path
        must = [{"exists": {"field": "{}.{}".format(path, body.require)}}]
        terms = getattr(body, "terms", None)
        if terms is not None:
            for field, values in vars(terms).items():
                must.append({"terms": {"{}.{}".format(path, field): values}})
        ranges = getattr(body, "range", None)
        if ranges is not None:
            for field, bounds in vars(ranges).items():
                must.append({"range": {"{}.{}".format(path, field): vars(bounds)}})
        return {"nested": {"path": path, "query": {"bool": {"must": must}}}}

    def iterate(self):
        """Yield population hits using a point-in-time and search_after.

        A PIT gives a consistent snapshot across a sweep that may run for
        hours. from/size would break past 10,000 results.
        """
        pit = self.es.open_point_in_time(index=self.source_index, keep_alive="10m")
        pit_id = pit["id"]
        search_after = None
        yielded = 0
        query = self.build_query()

        try:
            while True:
                if self.max_records is not None:
                    remaining = self.max_records - yielded
                    if remaining <= 0:
                        return
                    page_size = min(PAGE_SIZE, remaining)
                else:
                    page_size = PAGE_SIZE

                params = {
                    "size": page_size,
                    "pit": {"id": pit_id, "keep_alive": "10m"},
                    "sort": [{self.sort_field: "asc"}],
                    "track_total_hits": False,
                }
                if query is not None:
                    params["query"] = query
                if search_after is not None:
                    params["search_after"] = search_after

                # No index= when a pit is supplied; the pit carries the target.
                response = self.es.search(**params)
                hits = response["hits"]["hits"]
                if not hits:
                    return

                for hit in hits:
                    yield hit
                    yielded += 1
                    if self.max_records is not None and yielded >= self.max_records:
                        return

                search_after = hits[-1]["sort"]
                pit_id = response.get("pit_id", pit_id)
        finally:
            try:
                self.es.close_point_in_time(id=pit_id)
            except Exception as e:
                logger.warning("Failed to close point in time: {}".format(e))
```

- [ ] **Step 6: Add the `population` block to DOT's config**

Replace the `"predecessors"` block in `DOT-Commercial/configuration/chameleon-detection/entity-match.json` with:

```json
  "population": {
    "mode": "lifecycle",
    "sort_field": "dot_number",
    "max_records": null,
    "selector": "out-of-service",
    "selectors": {
      "out-of-service": {
        "nested-exists": {
          "path": "out_of_service_orders",
          "require": "oos_date",
          "terms": { "status": ["ACTIVE"] },
          "range": { "oos_date": { "gte": "2020-01-01" } }
        }
      },
      "revoked-authority": { "term": { "auth_history.disp_action_desc": "REVOKED" } },
      "both": { "all": ["out-of-service", "revoked-authority"] },
      "either": { "any": ["out-of-service", "revoked-authority"] }
    }
  },
```

- [ ] **Step 7: Run both new test files**

Run: `.venv/bin/python -m pytest tests/test_population.py tests/test_selector_equivalence.py -q`
Expected: PASS. If the equivalence test fails, **the new query is wrong — do not adjust the expectation.** Read the diff and fix `population.py`.

- [ ] **Step 8: Switch the phase over**

In `phase_providers/phase_entity_match.py`, change the import from `matching.predecessors import PredecessorSelector` to `matching.population import PopulationSelector`, and construct it from `config.population` instead of `config.predecessors`.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, including the still-present `tests/test_predecessors.py`.

- [ ] **Step 10: Delete the superseded module and its tests**

```bash
git rm matching/predecessors.py tests/test_predecessors.py tests/test_selector_equivalence.py
```

The equivalence test goes with it: it imports `PredecessorSelector`, so it cannot outlive it. Its value was consumed the moment it passed. `tests/test_population.py` is the durable replacement and already covers every clause kind.

- [ ] **Step 11: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: define the swept population in project config

matching/predecessors.py hardcoded FMCSA field paths, the literal REVOKED, a
dot_number sort, and four selectors that are FMCSA's notions of shut down.
Selector definitions now live in project config, assembled from a closed
four-clause menu -- nested-exists, term, all, any -- that stays in code.

Verified by building each of DOT's four selectors from the shipped config and
asserting the query is byte-identical to what the hardcoded implementation
produced. That test is deleted in the same commit: it imports the module it
was comparing against, so it cannot outlive it.

nested-exists is one primitive rather than three composable ones because
flattening is a known defect, not a style preference -- an object mapping let
two different array elements jointly satisfy a filter no single element met."
```

---

### Task 5: The `lifecycle` block as a single source of truth for dates

The shutdown and registration paths exist twice today: on the `temporal` signal, and again as literals at `phase_entity_match.py:664` where `gap_days` is computed. Nothing checks they agree. This task removes the duplication, which is worth doing on correctness grounds independent of portability.

**Files:**

- Modify: `matching/signals.py` (`TemporalSignal`)
- Modify: `matching/scorer.py:117-144` (`_gap_outside_window`)
- Modify: `phase_providers/phase_entity_match.py:661-674`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Test: `tests/test_lifecycle.py` (create)

**Interfaces:**

- Consumes: `_entity_summary(doc, entity_config, extra)` from Task 3.
- Produces: `matching.signals.TemporalSignal(config, lifecycle)` — the signal now takes the lifecycle block as a second constructor argument; `build_signal(config, lifecycle=None)` passes it through. `PairScorer(signal_configs, scoring_config, lifecycle=None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lifecycle.py`:

```python
"""One source of truth for the dates a pair's gap is measured between.

The paths used to live on the temporal signal AND as literals in the phase
that computes gap_days, with nothing checking they agreed. A pair could
therefore be scored on one pair of dates and reported with a gap computed from
another. These tests pin that both now read the same config.
"""

from types import SimpleNamespace

import pytest

from matching.documents import EntityDoc
from matching.signals import build_signal


def _lifecycle():
    return SimpleNamespace(shutdown_date="orders.closed", registration_date="opened")


def _signal():
    config = SimpleNamespace(type="temporal", weight=0.05, max_gap_days=365)
    return build_signal(config, lifecycle=_lifecycle())


def test_temporal_reads_paths_from_lifecycle():
    pred = EntityDoc(entity_key="1", source={"orders": {"closed": "2021-01-01"}}, tokens={})
    cand = EntityDoc(entity_key="2", source={"opened": "2021-01-31"}, tokens={})
    score = _signal().score(pred, cand, ctx=SimpleNamespace())
    assert score is not None
    assert 0.0 < score < 1.0


def test_temporal_is_unevaluable_without_both_dates():
    # None means "not evaluable", which the scorer drops and renormalizes
    # around. Returning 0.0 would penalize a record for a gap in its data.
    pred = EntityDoc(entity_key="1", source={}, tokens={})
    cand = EntityDoc(entity_key="2", source={"opened": "2021-01-31"}, tokens={})
    assert _signal().score(pred, cand, ctx=SimpleNamespace()) is None


def test_temporal_without_lifecycle_is_refused():
    # Silently scoring nothing is the failure mode this guards: a project that
    # configures temporal but no lifecycle would get a signal that never fires
    # and no indication why.
    config = SimpleNamespace(type="temporal", weight=0.05, max_gap_days=365)
    with pytest.raises(ValueError, match="temporal signal requires a lifecycle block"):
        build_signal(config, lifecycle=None)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_lifecycle.py -q`
Expected: FAIL — `build_signal()` takes 1 positional argument.

- [ ] **Step 3: Thread `lifecycle` through `build_signal` and `TemporalSignal`**

In `matching/signals.py`:

```python
def build_signal(config, lifecycle=None):
    """Construct the Signal subclass registered for this config's type.

    lifecycle is passed to every signal that needs dated events, so the paths
    a signal scores on and the paths the phase reports a gap from cannot
    disagree -- they are now the same object.
    """
    signal_class = SIGNAL_TYPES.get(config.type)
    if signal_class is None:
        raise ValueError(
            "unknown signal type {!r}; known types are {}".format(
                config.type, ", ".join(sorted(SIGNAL_TYPES))
            )
        )
    if signal_class is TemporalSignal:
        return signal_class(config, lifecycle)
    return signal_class(config)
```

And on `TemporalSignal`:

```python
class TemporalSignal(Signal):
    """Closeness between the predecessor's shutdown and the successor's registration.

    A chameleon typically re-registers soon after being shut down, to resume
    operating with minimal downtime. A short gap is corroborating evidence of
    reincarnation; a gap of years is more likely coincidence.

    Reads its two date paths from the lifecycle block rather than from its own
    config. They used to be duplicated -- here and again where the phase
    computes the reported gap_days -- with nothing checking the two agreed, so
    a pair could be scored on one pair of dates and reported with a gap
    measured between another.
    """

    type_names = ("temporal",)

    def __init__(self, config, lifecycle=None):
        super().__init__(config)
        if lifecycle is None:
            raise ValueError(
                "temporal signal requires a lifecycle block naming shutdown_date "
                "and registration_date; without it the signal would score every "
                "pair as unevaluable and report nothing"
            )
        self.lifecycle = lifecycle

    def score(self, pred, cand, ctx):
        shutdown = _latest_date(pred.value(self.lifecycle.shutdown_date))
        registered = _latest_date(cand.value(self.lifecycle.registration_date))
        if shutdown is None or registered is None:
            return None

        gap_days = (registered - shutdown).days
        max_gap = float(self.config.max_gap_days)

        if gap_days >= 0:
            return max(0.0, 1.0 - (gap_days / max_gap))

        # Registered before the shutdown: a pre-positioned shell is a real
        # tactic, but weaker evidence than reopening days afterward.
        backward = min(1.0, abs(gap_days) / float(BACKWARD_WINDOW_DAYS))
        return max(0.0, (1.0 - backward) * BACKWARD_SCALE)
```

- [ ] **Step 4: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 5: Point `PairScorer._gap_outside_window` at the same lifecycle**

In `matching/scorer.py`, accept `lifecycle=None` in `__init__`, store it, delete the `self._temporal_config` lookup, and rewrite the two path reads in `_gap_outside_window`:

```python
        if self.lifecycle is None:
            return False
        shutdown = _latest_date(pred.value(self.lifecycle.shutdown_date))
        registered = _latest_date(cand.value(self.lifecycle.registration_date))
```

Keep the surrounding guards exactly as they are, including the "returns False when either date is unparseable" behavior and its comment — that distinction is load-bearing and unchanged.

- [ ] **Step 6: Make the phase compute `gap_days` from lifecycle**

In `_pair_doc`, replace the two hardcoded paths at lines 664–665:

```python
        shutdown = None
        registered = None
        if self.lifecycle is not None:
            shutdown = _latest_iso(pred.value(self.lifecycle.shutdown_date))
            registered = _latest_iso(succ.value(self.lifecycle.registration_date))
        gap_days = None
        if shutdown and registered:
            gap_days = (
                parse_flexible_date(registered) - parse_flexible_date(shutdown)
            ).days
```

Build the summary `extra` dicts from lifecycle too, including the optional `shutdown_reason` path:

```python
        pred_extra = {}
        succ_extra = {}
        if shutdown is not None:
            pred_extra["shutdown_date"] = shutdown
            reason_path = getattr(self.lifecycle, "shutdown_reason", None)
            if reason_path:
                reason = pred.value(reason_path)
                pred_extra["shutdown_reason"] = (
                    reason[0] if isinstance(reason, list) else reason
                )
        if registered is not None:
            succ_extra["add_date"] = registered
```

Store `self.lifecycle = getattr(config, "lifecycle", None)` alongside the other config blocks, and pass it into both `build_signal` calls and `PairScorer`.

- [ ] **Step 7: Add the `lifecycle` block and trim the temporal signal in DOT's config**

Add above `"population"`:

```json
  "lifecycle": {
    "shutdown_date": "out_of_service_orders.oos_date",
    "registration_date": "add_date",
    "shutdown_reason": "out_of_service_orders.oos_reason"
  },
```

And remove `predecessor_date` and `successor_date` from the `temporal` signal entry, leaving:

```json
    { "type": "temporal", "weight": 0.05, "max_gap_days": 365 },
```

- [ ] **Step 8: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

`tests/test_signals.py` and `tests/test_scorer.py` construct temporal signals directly and will need the new argument.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "fix: read the pair gap's dates from one place

The shutdown and registration paths existed twice -- on the temporal signal,
and again as literals where the phase computes the reported gap_days -- with
nothing checking they agreed. A pair could be scored on one pair of dates and
reported with a gap measured between another.

Both now read the lifecycle block, so the gap a signal scores and the gap a
reviewer reads are the same computation by construction. A temporal signal
configured without a lifecycle block now raises instead of scoring every pair
as unevaluable and reporting nothing."
```

---

### Task 6: Generalize agent rarity into `FieldRarityTable`

`ScoringContext.agent_rarity` and the BOC-3 prefetch are a general "value frequency for a field" mechanism wearing FMCSA vocabulary.

**Files:**

- Modify: `matching/documents.py:95-184`
- Modify: `matching/signals.py:470-491` (`AgentSignal`)
- Modify: `phase_providers/phase_entity_match.py:548-590`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Test: `tests/test_field_rarity.py` (create)

**Interfaces:**

- Consumes: nothing from Tasks 1–5.
- Produces: `ScoringContext.rarity(field_path: str, value: str) -> float` and `ScoringContext(rarity_tables: dict[str, FieldRarityTable], ignored_values: dict[str, set[str]])`. `FieldRarityTable(counts: dict[str, int], total: int)` with `.rarity(value) -> float`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_field_rarity.py`:

```python
"""Rarity weighting is a general mechanism, not a BOC-3 one.

The arithmetic below is load-bearing and unchanged by the rename: normalized
IDF rather than 1 - share, because with only 89 distinct values the largest
share is 9.4% and 1 - share would compress everything into [0.906, 1.0],
leaving the signal no discriminating power at all.
"""

import pytest

from matching.documents import FieldRarityTable, ScoringContext


def test_unseen_value_scores_one():
    table = FieldRarityTable(counts={"common": 90}, total=100)
    assert table.rarity("never-seen") == 1.0


def test_dominant_value_scores_near_zero():
    table = FieldRarityTable(counts={"common": 90, "rare": 1}, total=100)
    assert table.rarity("common") < table.rarity("rare")


def test_uses_normalized_idf_not_one_minus_share():
    # 1 - share would give 0.10 here; normalized IDF spreads the population
    # far wider, which is the entire reason for the choice.
    table = FieldRarityTable(counts={"common": 90}, total=100)
    assert table.rarity("common") == pytest.approx(0.0229, abs=1e-3)


def test_tiny_corpus_floors_to_zero():
    # log(N) is 0 or undefined below two records, so there is no defensible
    # rarity. 0.0 is the floor of the range, deliberately not the 1.0 "unseen"
    # placeholder, which would misrepresent a known value as novel.
    assert FieldRarityTable(counts={}, total=1).rarity("x") == 0.0
    assert FieldRarityTable(counts={}, total=0).rarity("x") == 0.0


def test_lookup_is_case_and_whitespace_insensitive():
    # Must match how the signal normalizes before intersecting, or every
    # lookup silently misses and rarity weighting turns itself off.
    table = FieldRarityTable(counts={"acme filings": 5}, total=100)
    assert table.rarity("  ACME Filings ") == table.rarity("acme filings")


def test_context_rarity_is_keyed_by_field_path():
    ctx = ScoringContext(
        rarity_tables={"boc3_agents.co_name": FieldRarityTable({"a": 50}, 100)}
    )
    assert ctx.rarity("boc3_agents.co_name", "a") < 1.0


def test_context_rarity_without_a_table_scores_zero():
    # No table means frequencies were never gathered. A shared value is still
    # real evidence, but scoring it 1.0 would claim it is novel on no data.
    ctx = ScoringContext()
    assert ctx.rarity("some.field", "a") == 0.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_field_rarity.py -q`
Expected: FAIL, `cannot import name 'FieldRarityTable'`.

- [ ] **Step 3: Implement `FieldRarityTable` in `matching/documents.py`**

Replace `MIN_AGENT_CORPUS`, `_normalize_agent_key`, and `agent_rarity` with:

```python
# Below this many records, log(N) is 0 or undefined and normalized IDF cannot
# be computed; rarity floors to 0.0 instead.
MIN_RARITY_CORPUS = 2


def normalize_rarity_key(value) -> str:
    """Casefold a value for keying and lookup.

    Must match how a signal normalizes before intersecting, otherwise a lookup
    silently misses and every value degrades to the 1.0 "unseen" fallback --
    turning rarity weighting off with no error anywhere.
    """
    return str(value).strip().lower()


@dataclass
class FieldRarityTable:
    """Value frequencies for one field, as normalized inverse document frequency.

    Exists because a signal that scores a shared value highly is asserting the
    value is discriminating, and only the corpus can say whether it is. One
    project's shared-filing-agent field had 89 distinct values across 1.43M
    rows, so two unrelated records share one about 7% of the time by chance;
    unweighted, that signal fires on noise.

    Uses log(N/count)/log(N), NOT 1 - count/N. With 89 values the largest share
    is 9.4%, so 1 - share compresses every value into [0.906, 1.0] and carries
    no discriminating power at all. Normalized IDF spreads the same population
    across [0.167, 1.0].
    """

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def __post_init__(self):
        self.counts = {normalize_rarity_key(k): v for k, v in self.counts.items()}

    def rarity(self, value: str) -> float:
        """1.0 for a value nobody uses, near 0.0 for a dominant one.

        Returns 0.0 -- the floor of the range, not "neutral" and emphatically
        not "maximally common" -- when there is no usable corpus. A shared
        value is still real evidence then, but scoring it 1.0 would
        misrepresent a known value as novel, and inventing a mid-range number
        would fabricate precision the data cannot support.
        """
        if self.total < MIN_RARITY_CORPUS:
            return 0.0
        count = self.counts.get(normalize_rarity_key(value), 0)
        if count <= 0:
            return 1.0
        return math.log(self.total / count) / math.log(self.total)
```

Then rewrite `ScoringContext` to hold `rarity_tables: dict[str, FieldRarityTable]` instead of `agent_counts`/`total_agent_carriers`, keep `ignored_values` and `is_ignored` unchanged except for the rename of `_normalize_agent_key` to `normalize_rarity_key`, and add:

```python
    def rarity(self, field_path: str, value: str) -> float:
        """Rarity of a value on one field, or 0.0 when no table was gathered."""
        table = self.rarity_tables.get(field_path)
        if table is None:
            return 0.0
        return table.rarity(value)
```

- [ ] **Step 4: Rename `AgentSignal` to `RarityWeightedValueSignal`**

```python
class RarityWeightedValueSignal(Signal):
    """A shared value on one field, weighted by how rare that value is.

    Named for the mechanism rather than the field: it was "agent", after the
    BOC-3 process agents it was written for, which put a domain concept in the
    framework's type registry. Any field where a shared value is weak evidence
    in proportion to its commonness behaves identically.

    Declines to seed (inherits the base seed_clauses returning []) because a
    field this signal suits is by construction a field many records share, and
    seeding on one returns essentially random candidates.
    """

    type_names = ("rarity-weighted-value",)

    def score(self, pred, cand, ctx):
        pred_values = set()
        cand_values = set()
        field_path = self.config.name_field
        _collect(pred_values, pred.value(field_path), normalize_text_identifier)
        _collect(cand_values, cand.value(field_path), normalize_text_identifier)

        if not pred_values or not cand_values:
            return None

        shared = pred_values & cand_values
        if not shared:
            return 0.0
        return max(ctx.rarity(field_path, value) for value in shared)
```

- [ ] **Step 5: Generalize the prefetch in `phase_entity_match.py`**

Replace the `agent_config = next(...)` block (lines 548–590) with a loop over every configured signal whose type is `rarity-weighted-value`, aggregating on `"{}.keyword".format(config.name_field)` and building one `FieldRarityTable` per field path. Keep both warnings — the aggregation failure and the zero-total case — and keep their reasoning, generalizing only the wording:

```python
            self.logger.warning(
                "Loaded 0 distinct values for {}; the enrichment feeding it may not "
                "have run. Its signal will score every shared value at 0.0.".format(field_path)
            )
```

- [ ] **Step 6: Update DOT's config**

```json
    { "type": "rarity-weighted-value", "weight": 0.04, "name_field": "boc3_agents.co_name" },
```

- [ ] **Step 7: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: generalize agent rarity into a field rarity table

agent_rarity and the BOC-3 prefetch were a general value-frequency mechanism
wearing domain vocabulary, and the signal type 'agent' put a domain concept
in the framework's type registry.

The arithmetic is unchanged and still load-bearing: normalized IDF rather
than 1 - share, because with 89 distinct values the largest share is 9.4% and
1 - share would compress everything into [0.906, 1.0], leaving no
discriminating power. The 0.0-on-no-corpus floor and its reasoning are
preserved verbatim."
```

---

### Task 7: Delete `vin-overlap` and add signal provenance

Deleting the DOT-flavored type name costs the output its only clue about what a signal read, so the provenance fields ship in the same commit.

**Files:**

- Modify: `matching/signals.py:547` (`type_names`), plus a `fields_read()` method on `Signal`
- Modify: `matching/scorer.py:21-30` (`IDENTITY_SIGNAL_TYPES`), `SignalContribution`
- Modify: `phase_providers/phase_entity_match.py:679-688`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Modify: `DOT-Commercial/precision_metrics.py:38` (`CORROBORATING`)
- Test: `tests/test_signal_provenance.py` (create)

**Interfaces:**

- Consumes: `_entity_summary` from Task 3.
- Produces: `Signal.fields_read() -> list[str]` returning the config field paths this signal reads; `SignalContribution` gains `signal_name: str | None` and `fields: list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_signal_provenance.py`:

```python
"""A reader of one pair must be able to tell what evidence fired.

Deleting the vin-overlap type name removed the only clue the emitted document
carried about what a shared-token signal actually read. These pin the two
replacements: the operator's label, and the field paths themselves.
"""

from types import SimpleNamespace

from matching.scorer import IDENTITY_SIGNAL_TYPES
from matching.signals import SIGNAL_TYPES, build_signal


def test_vin_overlap_is_no_longer_a_registered_type():
    assert "vin-overlap" not in SIGNAL_TYPES
    assert "shared-token" in SIGNAL_TYPES


def test_vin_overlap_is_no_longer_an_identity_type():
    assert "vin-overlap" not in IDENTITY_SIGNAL_TYPES
    assert "shared-token" in IDENTITY_SIGNAL_TYPES


def test_unknown_type_names_the_known_ones():
    # A config still saying vin-overlap must fail loudly with a usable message,
    # not score nothing.
    config = SimpleNamespace(type="vin-overlap", weight=0.1, fields=["a"])
    try:
        build_signal(config)
    except ValueError as e:
        assert "shared-token" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_signal_reports_the_fields_it_reads():
    config = SimpleNamespace(
        type="shared-token", weight=0.16, fields=["crashes.vin", "inspections.units.vin"]
    )
    assert build_signal(config).fields_read() == [
        "crashes.vin",
        "inspections.units.vin",
    ]


def test_signal_name_defaults_to_none():
    config = SimpleNamespace(type="shared-token", weight=0.16, fields=["a"])
    assert build_signal(config).signal_name is None


def test_signal_name_comes_from_config():
    # The label lives on the instance in project config, so the framework never
    # learns the word "vin" and a project names its own signals.
    config = SimpleNamespace(
        type="shared-token", weight=0.16, fields=["a"], name="vin-overlap"
    )
    assert build_signal(config).signal_name == "vin-overlap"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_signal_provenance.py -q`
Expected: FAIL — `vin-overlap` is still registered.

- [ ] **Step 3: Delete the alias and add the provenance accessors**

In `matching/signals.py`, change `SharedTokenSignal.type_names` to `("shared-token",)` and update its docstring: it currently explains that `"vin-overlap"` is retained for existing config, which is no longer true. State instead that the type is named for the mechanism and that a project labels its own instance via `name`.

On the `Signal` base class, add:

```python
    @property
    def signal_name(self) -> str | None:
        """The operator's label for this signal instance, or None.

        Carried into every emitted contribution. The framework's type names are
        deliberately generic, which leaves a reader of one pair unable to tell
        what a `shared-token` signal actually read; a project labels its own
        instances instead, so no domain vocabulary enters the type registry.
        """
        return getattr(self.config, "name", None)

    def fields_read(self) -> list[str]:
        """The config field paths this signal reads, in config order.

        Emitted on each contribution so a pair says what kind of evidence
        fired without depending on an operator having set a label. Paths only,
        never values -- a matched value belonging to a flagged entity must not
        enter the pair document.
        """
        names: list[str] = []
        for key in _FIELD_CONFIG_KEYS:
            value = getattr(self.config, key, None)
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, list):
                names.extend(value)
        return names
```

- [ ] **Step 4: Remove `vin-overlap` from `IDENTITY_SIGNAL_TYPES`**

In `matching/scorer.py:21-30`, delete the `"vin-overlap"` entry. Leave `"shared-token"` and the surrounding comment, which explains why a shared unique token counts as identity evidence.

- [ ] **Step 5: Run the test**

Run: `.venv/bin/python -m pytest tests/test_signal_provenance.py -q`
Expected: PASS.

- [ ] **Step 6: Carry the provenance onto contributions**

Add `signal_name: str | None = None` and `fields: list[str] = field(default_factory=list)` to `SignalContribution`, populate them in `score_pair`:

```python
            contributions.append(
                SignalContribution(
                    signal_type=signal.signal_type,
                    signal_name=signal.signal_name,
                    fields=signal.fields_read(),
                    subfield=getattr(signal.config, "subfield", None),
                    weight=signal.weight,
                    score=score,
                    contribution=signal.weight * score,
                )
            )
```

and emit them in `_pair_doc`, omitting `signal_name` when unset so an absent label never reads as a real value:

```python
            "signals": [
                {
                    k: v
                    for k, v in {
                        "signal_type": c.signal_type,
                        "signal_name": c.signal_name,
                        "fields": c.fields,
                        "subfield": c.subfield,
                        "weight": c.weight,
                        "score": round(c.score, 6),
                        "contribution": round(c.contribution, 6),
                    }.items()
                    if v is not None
                }
                for c in pair.signals
            ],
```

**Leave `matched_on` exactly as it is** — `sorted({c.signal_type for c in fired})`. Names must not enter it: `IDENTITY_SIGNAL_TYPES` and the metric predicates all operate on that set.

- [ ] **Step 7: Update DOT's config and `precision_metrics.py`**

In `entity-match.json`, change the `vin-overlap` signal to:

```json
    {
      "type": "shared-token",
      "name": "vin-overlap",
      "weight": 0.16,
      "conclusive": true,
      "max_shared_entities": 5,
      "fields": [
        "crashes.vehicle_identification_number",
        "inspections.units.insp_unit_vehicle_id_number"
      ]
    },
```

Change `"seed_signals"` to list `"shared-token"` instead of `"vin-overlap"`. Rename `max_shared_carriers` to `max_shared_entities` in `phase_entity_match.py:537` too.

In `DOT-Commercial/precision_metrics.py:38`, change `CORROBORATING` to `frozenset({"shared-token", "exact-identifier"})`, and update the `matched == {"vin-overlap"}` and `matched & IDENTITY_SIGNAL_TYPES == {"vin-overlap"}` comparisons at lines ~100–104. **The metric names `vin_only` and `vin_only_identity` stay** — they are keys in a committed baseline, and renaming them would make the gate's comparison fail for a cosmetic reason.

- [ ] **Step 8: Run the full suite and the linter**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

- [ ] **Step 9: Verify no config still names the deleted type**

Run:

```bash
grep -rn "vin-overlap\|max_shared_carriers\|\"agent\"" --include="*.json" . | grep -v node_modules
```

Expected: only the `"name": "vin-overlap"` label in DOT's config. Anything else is a config that will now raise at build time.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: name the shared-token signal for its mechanism

SharedTokenSignal already registered both vin-overlap and shared-token, and
shared-token was already an identity type, so this deletes the DOT-flavored
half of an alias pair rather than inventing a name.

Deleting it costs the emitted document its only clue about what the signal
read, so provenance ships in the same commit: an optional operator label from
config, and the field paths the signal reads. Paths only, never values -- a
matched value belonging to a flagged entity must not enter a pair document.

matched_on stays keyed by type. IDENTITY_SIGNAL_TYPES and the metric
predicates all operate on that set, so admitting labels would change metric
values for no gain."
```

---

### Task 8: The compatibility gate

Everything above is verified without a cluster. This task is the measurement that says the refactor moved nothing.

**Files:**

- Create: `scripts/compare_pair_ids.py`
- Create: `docs/superpowers/plans/2026-08-16-compatibility-gate-runbook.md`
- Test: `tests/test_compare_pair_ids.py` (create)

**Interfaces:**

- Consumes: everything from Tasks 1–7.
- Produces: `scripts/compare_pair_ids.py --baseline-index <name> --candidate-index <name>`, exiting 0 when the `_id` sets are identical and 1 otherwise.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_pair_ids.py`:

```python
"""Set-identity of two pair populations, independent of the metric counts.

Eleven aggregate counts can coincide across a population that has genuinely
changed -- a pair lost and a pair gained in the same score band cancel out --
so the gate needs a check that no count can satisfy.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "compare_pair_ids.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_pair_ids", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_pair_ids = _load()


def test_identical_sets_report_no_difference():
    result = compare_pair_ids.diff_id_sets({"a", "b"}, {"a", "b"})
    assert result.identical
    assert result.only_in_baseline == []
    assert result.only_in_candidate == []


def test_a_lost_and_gained_pair_are_both_reported():
    # The cancelling case: same count, different population.
    result = compare_pair_ids.diff_id_sets({"a", "b"}, {"a", "c"})
    assert not result.identical
    assert result.only_in_baseline == ["b"]
    assert result.only_in_candidate == ["c"]


def test_differences_are_sorted_for_a_stable_report():
    result = compare_pair_ids.diff_id_sets({"z", "y", "x"}, set())
    assert result.only_in_baseline == ["x", "y", "z"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_compare_pair_ids.py -q`
Expected: FAIL, file not found.

- [ ] **Step 3: Implement `scripts/compare_pair_ids.py`**

Module docstring must state why counts are insufficient. Provide:

```python
@dataclass
class IdSetDiff:
    """Which pair ids appear on only one side, and whether the sets agree."""

    only_in_baseline: list[str]
    only_in_candidate: list[str]

    @property
    def identical(self) -> bool:
        return not self.only_in_baseline and not self.only_in_candidate


def diff_id_sets(baseline: set, candidate: set) -> IdSetDiff:
    """Compare two pair-id sets, sorted so a report is stable across runs."""
    return IdSetDiff(
        only_in_baseline=sorted(baseline - candidate),
        only_in_candidate=sorted(candidate - baseline),
    )
```

Plus a `scan_ids(es, index)` helper using `elasticsearch.helpers.scan` with `_source=False`, an argparse main following `scripts/compare_sweeps.py`'s conventions, and a report that prints the first 20 differing ids on each side rather than all of them.

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_compare_pair_ids.py -q`
Expected: PASS.

- [ ] **Step 5: Run the linter and commit the tooling**

```bash
.venv/bin/python -m ruff check .
git add -A
git commit -m "feat: add pair-id set comparison for the compatibility gate

Metric equality alone cannot prove the population is unchanged: a pair lost
and a pair gained in the same score band cancel out across all eleven counts.
Composite ids are label-independent by construction, so this check is valid
across the entity-key rename."
```

- [ ] **Step 6: Confirm the cluster holds the baseline's source index**

```bash
curl -s "http://localhost:9200/carriers-000001/_mapping" | head -40
```

Expected: the alias resolves to the index stamped `0595ca890d9ec6fb` in its `_meta`. **If it does not, stop.** The README has two outstanding reload items, and sweeping a reloaded index would conflate this refactor with the `dot_number` and composite-`_id` re-keying already queued. Report what the cluster actually holds rather than proceeding.

- [ ] **Step 7: Run the sweep**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection --phase=entity-match
```

This takes hours. Record the candidates index name it writes to.

- [ ] **Step 8: Compare the metrics for exact equality**

Run `DOT-Commercial/precision_metrics.py` over the new pair population and compare against `DOT-Commercial/data/precision/baseline-post-reload.json`:

```
canary 11, coherent_ge_070 584, coherent_share_ge_070 1.0,
identical_name_triage 145, pairs 75537, pairs_ge_070 584,
predecessors_with_pairs 23040, triage_bounded 197, triage_unbounded 302,
vin_only 1, vin_only_identity 208
```

**Use direct equality, not `utils.sweep_compare.compare()`.** That engine judges whether an intentional change moved the right metrics in the right direction; its vocabulary (`must_not_fall`, `must_not_rise`, `within_10pct`, `informational`) has no way to say "must not change at all", so a real regression could clear it wearing `informational`.

- [ ] **Step 9: Compare the pair id sets**

```bash
.venv/bin/python scripts/compare_pair_ids.py \
  --baseline-index chameleon-candidates-2026.08.13-000001 \
  --candidate-index <the index from step 7>
```

Expected: exit 0, no differences.

**Verified 2026-08-16 that the baseline is diffable:**
`chameleon-candidates-2026.08.13-000001` holds 75,537 documents, matching
`baseline-post-reload.json`'s `pairs` exactly, and `carriers-000001` resolves to
`carriers-2026.08.13-000001` stamped `0595ca890d9ec6fb`. That index carries no
`_meta` of its own — it predates provenance stamping, which is expected and
irrelevant here, since this check compares ids rather than provenance.

**Do not sweep twice in one day.** `index-create` stamps `{now/d}`, so a second
run on the same date writes into the same index as the first and the comparison
target is destroyed. The baseline is safe only because it is dated 08-13.

- [ ] **Step 10: If anything differs, stop and diagnose**

Do not adjust a threshold, a weight, or a baseline number to make the gate pass. A non-zero delta means one of Tasks 2–7 changed behavior, and the differing ids name the pairs to investigate. Report the delta and the first differing pairs.

- [ ] **Step 11: Record the result**

Write the runbook to `docs/superpowers/plans/2026-08-16-compatibility-gate-runbook.md` with the commands above, the index names actually used, the measured metric values, and the id-set result. Then update the spec's compatibility-gate section to say the gate ran and what it found.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "test: record the compatibility gate result

Swept DOT-Commercial against the source index the baseline was taken from and
compared both ways: all eleven metrics for exact equality, and the pair _id
sets for population identity. Records the index names and measured values so a
later reader can tell which corpus the claim rests on."
```

---

## Self-Review

**Spec coverage (rollout steps 1–6):**

| Rollout step                                | Task       |
| ------------------------------------------- | ---------- |
| 1. Move DOT-specific scripts                | Task 1     |
| 2. `EntityDoc` / `entity_key` / `key_label` | Tasks 2, 3 |
| 3. `PopulationSelector` + clause menu       | Task 4     |
| 4. `lifecycle`, `gap_days`, summary fields  | Tasks 3, 5 |
| 5. Signal renames + provenance              | Tasks 6, 7 |
| 6. Compatibility gate                       | Task 8     |

Spec tests mapped: `test_population.py` (Task 4), `test_selector_equivalence` (Task 4), `test_entity_key.py` → delivered as `test_entity_doc.py` + `test_pair_summary.py` (Tasks 2, 3), `test_signal_provenance.py` (Task 7). `test_metric_predicates.py` and `test_config_validation.py` belong to Plans 2 and 3 and are deliberately absent here.

**Deferred to later plans:** `schema/`, the `validate` phase, `metrics.json` and its runner, `scripts/new_project.py`, the CMS instance, and the documentation rewrite.

**Type consistency:** `EntityDoc.entity_key` (Task 2) is read by `_entity_summary` (Task 3), `scorer.score_pair` (Task 2), and `_pair_doc` (Tasks 3, 5). `build_signal(config, lifecycle=None)` (Task 5) is called by `CandidateFinder.__init__` and the phase; Task 6 does not change its arity, and Task 7 adds only methods. `ScoringContext.rarity(field_path, value)` (Task 6) is called only by `RarityWeightedValueSignal` (Task 6).

**Known ordering hazard:** Task 3 passes `extra` dicts built from literals, which Task 5 then replaces with lifecycle-driven ones. Running Task 5 before Task 3 leaves `_entity_summary` undefined. The order is not optional.
