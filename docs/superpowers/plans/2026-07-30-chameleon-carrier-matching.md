# Chameleon Carrier Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give entitopia its first query/scoring layer — an `entity-match` phase that sweeps shut-down commercial carriers and emits ranked "this carrier probably reopened as that one" pairs with per-signal evidence.

**Architecture:** Elasticsearch generates candidates (one `bool.should` query per predecessor), `_mtermvectors` returns the exact analyzed tokens, and Python scores each pair across seven signal types with explicit weights. Scoring is a pure function over token sets, so it unit-tests without a cluster. Results land in a new `chameleon-candidates` index built by the existing `index-create`/`index-map` phases.

**Tech Stack:** Python 3.11+, `elasticsearch==9.4.1`, pandas, pytest (new), Elasticsearch with `analysis-icu` and `analysis-phonetic` plugins.

**Spec:** `docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md`

## Global Constraints

- **Python 3.11+.** Enforced at `execute_project.py:3-10`. Use `X | None` union syntax, not `Optional[X]`.
- **`elasticsearch==9.4.1`.** Use `request_timeout=`, not `timeout=`. `BadRequestError`/`NotFoundError`/`ConflictError` from the `elasticsearch` package.
- **All JSON config loads as `types.SimpleNamespace`**, via `file_utils.load_from_file`'s `object_hook`. Access with attributes (`config.signals[0].weight`), never `config["signals"]`. Optional keys are read with `getattr(obj, "key", default)`, matching how `phase_index_populate.py:102-121` handles them.
- **`{now/d}` expands client-side** to `%Y.%m.%d` (e.g. `carriers-2026.07.30-000001`) via `elasticsearch_utils.replace_index_with_now_version`. It operates on `config.index` in place.
- **Signal weights need not sum to 1.0.** Unevaluable signals return `None`, drop out, and the total is renormalized over what remains. Only ratios matter.
- **Every signal returns `float` in `[0.0, 1.0]` or `None`.** `None` means "not evaluable" (missing data on either side). Never return `0.0` to mean missing — that is a scoring error, since it penalizes absent data instead of treating it neutrally.
- **Analyzer changes require `analysis-icu` and `analysis-phonetic`** installed on the cluster. Already a documented prerequisite in `README.md`.
- **Tests are pytest, run manually.** The repo has no CI and none is added here.
- **Markdown files are auto-formatted by a prettier PostToolUse hook.** Do not fight its reformatting of tables.

---

## File Structure

**New — pure Python, no cluster needed (Tasks 1-5):**

| File | Responsibility |
| --- | --- |
| `matching/__init__.py` | Package marker |
| `matching/tokens.py` | Set math (jaccard, containment, blend) and identifier normalization |
| `matching/documents.py` | `CarrierDoc` and `ScoringContext` — the data shapes signals consume |
| `matching/signals.py` | `Signal` base, registry, and all seven signal implementations |
| `matching/scorer.py` | `PairScorer` — aggregation, renormalization, the three guards |

**New — cluster-facing (Tasks 10-12):**

| File | Responsibility |
| --- | --- |
| `matching/predecessors.py` | `PredecessorSelector` — the four selectors and PIT/`search_after` iteration |
| `matching/candidates.py` | `CandidateFinder` — seed query construction and `_mtermvectors` fetch |
| `phase_providers/phase_entity_match.py` | Orchestration, config validation, mapping precondition, output writing |

**New — utils and config:**

| File | Responsibility |
| --- | --- |
| `utils/id_utils.py` | `compute_id` lifted out of `phase_index_populate.py` |
| `DOT-Commercial/configuration/chameleon-detection/index-config.json` | Output index name and alias |
| `DOT-Commercial/configuration/chameleon-detection/index-mappings.json` | Output document mapping |
| `DOT-Commercial/configuration/chameleon-detection/entity-match.json` | Selector, signals, weights, guards |

**New — tests:**

`tests/__init__.py`, `tests/test_tokens.py`, `tests/test_signals.py`, `tests/test_scorer.py`

**Modified:**

| File | Change |
| --- | --- |
| `DOT-Commercial/configuration/carriers/index-settings.json` | Replace metaphone, add beider_morse, corporate-suffix stop, street synonyms |
| `DOT-Commercial/configuration/carriers/index-mappings.json` | New subfields; pin `add_date`, `phy_zip`, `fax`, `boc3_agents.*` |
| `DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json` | Painless `add_date` century fix |
| `DOT-Commercial/configuration.json` | New `chameleon-detection` step, `entity-match` in `all_phases` |
| `phase_providers/phase_dispatcher.py` | New `entity-match` branch; fix the broken `else` |
| `phase_providers/phase_index_populate.py` | Delegate to `utils/id_utils.py` |
| `requirements.txt` | Add pytest |
| `DOT-Commercial/README.md` | New step; correct the BOC-3 claim |

---

### Task 1: Token math and identifier normalization

**Files:**

- Create: `matching/__init__.py`, `matching/tokens.py`, `tests/__init__.py`, `tests/test_tokens.py`
- Modify: `requirements.txt`

**Interfaces:**

- Consumes: nothing
- Produces: `jaccard(a, b) -> float`, `containment(a, b) -> float`, `blended_overlap(a, b) -> float | None`, `normalize_phone(value) -> str | None`, `normalize_text_identifier(value) -> str | None`. All take/return `set[str]` or `str | None` as shown.

- [ ] **Step 1: Add pytest to requirements**

Append to `requirements.txt`:

```
pytest
```

Then install: `pip install -r requirements.txt`

- [ ] **Step 2: Create the package markers**

Create `matching/__init__.py` and `tests/__init__.py` as empty files.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_tokens.py`:

```python
import pytest

from matching.tokens import (
    blended_overlap,
    containment,
    jaccard,
    normalize_phone,
    normalize_text_identifier,
)


def test_jaccard_identical_sets_is_one():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_set_is_zero():
    assert jaccard(set(), {"a"}) == 0.0


def test_containment_rewards_subset():
    # "SMITH LLC" tokens are a subset of "SMITH TRUCKING LLC" tokens
    assert containment({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"}) == 1.0


def test_jaccard_punishes_the_same_subset():
    assert jaccard({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"}) == pytest.approx(2 / 3)


def test_blended_overlap_abbreviation_scores_above_five_eighths():
    # This is the abbreviation case the design exists to catch.
    score = blended_overlap({"SM0", "LLC"}, {"SM0", "TRKN", "LLC"})
    assert score == pytest.approx(0.5 * (2 / 3) + 0.5 * 1.0)
    assert score > 0.8


def test_blended_overlap_returns_none_for_empty_set():
    # A carrier named literally "TRUCKING LLC" reduces to zero tokens after
    # the corporate-suffix stop filter. That is "no signal", not "no match".
    assert blended_overlap(set(), {"SM0"}) is None
    assert blended_overlap({"SM0"}, set()) is None


def test_normalize_phone_strips_formatting():
    assert normalize_phone("(503) 289-5558") == "5032895558"


def test_normalize_phone_rejects_repeated_digit_placeholders():
    assert normalize_phone("(000) 000-0000") is None
    assert normalize_phone("1111111111") is None


def test_normalize_phone_rejects_blank_and_short():
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
    assert normalize_phone("12345") is None


def test_normalize_text_identifier_lowercases_and_trims():
    assert normalize_text_identifier("  Joe@Example.COM ") == "joe@example.com"


def test_normalize_text_identifier_rejects_blank():
    assert normalize_text_identifier("   ") is None
    assert normalize_text_identifier(None) is None
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matching.tokens'`

- [ ] **Step 5: Implement `matching/tokens.py`**

```python
"""Set-overlap math and identifier normalization for entity matching.

Every function here is pure. Signals build on these so that scoring can be
tested without an Elasticsearch cluster.
"""

import re

# A phone that is one digit repeated (0000000000, 1111111111) is a placeholder,
# not a phone number. Left alone these cluster thousands of unrelated carriers.
_REPEATED_DIGIT = re.compile(r"^(\d)\1*$")
_NON_DIGIT = re.compile(r"\D")

MIN_PHONE_DIGITS = 7


def jaccard(a: set[str], b: set[str]) -> float:
    """Intersection over union. 0.0 when either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    """Intersection over the smaller set. 0.0 when either set is empty.

    This is what catches abbreviation: "SMITH LLC" is fully contained in
    "SMITH TRUCKING LLC" and scores 1.0, where jaccard scores 0.67.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def blended_overlap(a: set[str], b: set[str]) -> float | None:
    """Half jaccard, half containment. None when either set is empty.

    Pure jaccard would punish name abbreviation, which is one of the evasion
    tactics being hunted. Pure containment would treat any subset as a perfect
    match. Blending keeps full overlap ranked above a subset match while still
    scoring abbreviation highly.
    """
    if not a or not b:
        return None
    return 0.5 * jaccard(a, b) + 0.5 * containment(a, b)


def normalize_phone(value) -> str | None:
    """Digits only. None for blanks, short numbers, and repeated-digit placeholders."""
    if value is None:
        return None
    digits = _NON_DIGIT.sub("", str(value))
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    if _REPEATED_DIGIT.match(digits):
        return None
    return digits


def normalize_text_identifier(value) -> str | None:
    """Trimmed and lowercased. None for blanks."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_tokens.py -v`
Expected: PASS — all tests in the file green, no failures or errors

- [ ] **Step 7: Commit**

```bash
git add matching/__init__.py matching/tokens.py tests/__init__.py tests/test_tokens.py requirements.txt
git commit -m "feat: add token overlap math and identifier normalization

Blends jaccard with containment because abbreviation ('SMITH TRUCKING LLC'
-> 'SMITH LLC') is a named evasion tactic that pure jaccard punishes.

Rejects repeated-digit phone placeholders, which would otherwise cluster
thousands of unrelated carriers into false matches."
```

---

### Task 2: Document shapes and the signal registry

**Files:**

- Create: `matching/documents.py`
- Test: `tests/test_signals.py` (created here, extended in Tasks 3-4)

**Interfaces:**

- Consumes: `matching.tokens`
- Produces:
  - `CarrierDoc(dot_number: str, source: dict, tokens: dict[str, set[str]])` with `token_set(field, subfield) -> set[str]` and `value(path) -> object | None` (dotted path into `source`).
  - `ScoringContext(agent_counts: dict[str, int], total_agent_carriers: int)` with `agent_rarity(name) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signals.py`:

```python
from matching.documents import CarrierDoc, ScoringContext


def make_doc(dot_number="1", source=None, tokens=None):
    return CarrierDoc(
        dot_number=dot_number,
        source=source or {},
        tokens=tokens or {},
    )


def test_token_set_reads_field_and_subfield():
    doc = make_doc(tokens={"legal_name.phonetic": {"SM0"}})
    assert doc.token_set("legal_name", "phonetic") == {"SM0"}


def test_token_set_missing_field_is_empty_not_error():
    doc = make_doc()
    assert doc.token_set("legal_name", "phonetic") == set()


def test_value_reads_a_plain_field():
    doc = make_doc(source={"phy_state": "OR"})
    assert doc.value("phy_state") == "OR"


def test_value_reads_a_dotted_path_into_a_nested_object():
    doc = make_doc(source={"boc3_agents": {"co_name": "ACME"}})
    assert doc.value("boc3_agents.co_name") == "ACME"


def test_value_collects_a_dotted_path_across_a_list():
    # Enriched fields arrive as lists because max_matches > 1.
    doc = make_doc(
        source={"crashes": [{"vin": "A"}, {"vin": "B"}]},
    )
    assert doc.value("crashes.vin") == ["A", "B"]


def test_value_flattens_doubly_nested_lists():
    # Inspection VINs sit two enrichment levels deep on a carrier:
    # inspections[] (max_matches 100) -> units[] (max_matches 10) -> vin.
    # Without flattening, the units step yields a list of lists and the final
    # step finds no dicts, silently returning None.
    doc = make_doc(
        source={
            "inspections": [
                {"units": [{"vin": "A"}, {"vin": "B"}]},
                {"units": [{"vin": "C"}]},
            ]
        }
    )
    assert doc.value("inspections.units.vin") == ["A", "B", "C"]


def test_value_missing_path_is_none():
    assert make_doc().value("nope.not_here") is None


def test_agent_rarity_common_agent_scores_low():
    ctx = ScoringContext(agent_counts={"BIG FILER": 134283}, total_agent_carriers=1426508)
    assert ctx.agent_rarity("BIG FILER") < 0.15


def test_agent_rarity_rare_agent_scores_high():
    ctx = ScoringContext(agent_counts={"TINY FILER": 2}, total_agent_carriers=1426508)
    assert ctx.agent_rarity("TINY FILER") > 0.99


def test_agent_rarity_unknown_agent_is_maximally_rare():
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=1000)
    assert ctx.agent_rarity("UNSEEN") == 1.0


def test_agent_rarity_with_no_corpus_is_neutral_zero():
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=0)
    assert ctx.agent_rarity("ANY") == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matching.documents'`

- [ ] **Step 3: Implement `matching/documents.py`**

```python
"""Data shapes that signals consume.

CarrierDoc pairs a carrier's _source with the analyzed tokens Elasticsearch
produced for it. Tokens come from _mtermvectors rather than being recomputed in
Python, so scoring always sees exactly what the index sees.
"""

from dataclasses import dataclass, field


@dataclass
class CarrierDoc:
    dot_number: str
    source: dict
    # Keyed "field.subfield", e.g. "legal_name.phonetic_bm"
    tokens: dict[str, set[str]] = field(default_factory=dict)

    def token_set(self, field_name: str, subfield: str) -> set[str]:
        return self.tokens.get("{}.{}".format(field_name, subfield), set())

    def value(self, path: str):
        """Read a dotted path out of _source.

        Enriched fields arrive as lists (max_matches > 1), so walking a path
        through a list collects the value from every element. Collected values
        are flattened at each step because enrichment nests two levels deep:
        a carrier's inspections[] each carry their own units[], so
        "inspections.units.insp_unit_vehicle_id_number" would otherwise produce
        a list of lists and find no dicts at the final step.

        Returns None when any part of the path is missing.
        """
        current = self.source
        for part in path.split("."):
            if isinstance(current, list):
                collected = []
                for item in _flatten(current):
                    if isinstance(item, dict) and part in item:
                        collected.append(item[part])
                if not collected:
                    return None
                current = _flatten(collected)
            elif isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None
        return current


def _flatten(values):
    """One level of list flattening, leaving non-list elements alone."""
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


@dataclass
class ScoringContext:
    """Corpus-level statistics gathered once per sweep."""

    agent_counts: dict[str, int] = field(default_factory=dict)
    total_agent_carriers: int = 0

    def agent_rarity(self, agent_name: str) -> float:
        """1.0 for an agent nobody uses, near 0.0 for a dominant filer.

        BOC-3 process agents are a commercial filing industry: only 89 distinct
        agents cover 1.43M filings, and the largest covers 9.4%. Without this
        weighting a shared agent fires on roughly 7% of random pairs.
        """
        if self.total_agent_carriers <= 0:
            return 0.0
        count = self.agent_counts.get(agent_name, 0)
        return 1.0 - (count / self.total_agent_carriers)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: PASS — all tests in the file green, no failures or errors

- [ ] **Step 5: Commit**

```bash
git add matching/documents.py tests/test_signals.py
git commit -m "feat: add CarrierDoc and ScoringContext

CarrierDoc.value walks dotted paths across lists because enriched fields
arrive as lists whenever max_matches > 1.

ScoringContext.agent_rarity implements the IDF weighting that BOC-3 needs:
89 distinct agents cover 1.43M filings, so an unweighted shared-agent signal
fires on ~7% of random pairs."
```

---

### Task 3: Signal base, registry, and the two name signals

**Files:**

- Create: `matching/signals.py`
- Modify: `tests/test_signals.py`

**Interfaces:**

- Consumes: `matching.tokens`, `matching.documents`
- Produces:
  - `Signal` base with `type_names: tuple[str, ...]`, `__init__(config)`, `weight: float`, `signal_type: str`, and `score(pred, cand, ctx) -> float | None`.
  - `build_signal(config) -> Signal` and `SIGNAL_TYPES: dict[str, type[Signal]]`.
  - `NameOverlapSignal`, registered for both `name-phonetic` and `name-token`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signals.py`:

```python
from types import SimpleNamespace

import pytest

from matching.signals import SIGNAL_TYPES, build_signal


def cfg(**kwargs):
    return SimpleNamespace(**kwargs)


def test_build_signal_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown signal type"):
        build_signal(cfg(type="not-a-signal", weight=0.1))


def test_name_overlap_registered_under_both_names():
    assert "name-phonetic" in SIGNAL_TYPES
    assert "name-token" in SIGNAL_TYPES


def test_name_overlap_scores_identical_names_as_one():
    signal = build_signal(
        cfg(type="name-phonetic", weight=0.22, fields=["legal_name"], subfield="phonetic")
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}})
    cand = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_name_overlap_returns_none_when_tokens_absent():
    signal = build_signal(
        cfg(type="name-phonetic", weight=0.22, fields=["legal_name"], subfield="phonetic")
    )
    pred = make_doc(tokens={"legal_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": {"SM0"}})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_name_overlap_cross_field_matches_legal_name_against_dba():
    # The classic chameleon move: the old legal name becomes the new DBA.
    signal = build_signal(
        cfg(
            type="name-phonetic",
            weight=0.22,
            fields=["legal_name", "dba_name"],
            subfield="phonetic",
            cross_field=True,
        )
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}, "dba_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": set(), "dba_name.phonetic": {"SM0", "TRKN"}})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_name_overlap_without_cross_field_ignores_the_dba_crossover():
    signal = build_signal(
        cfg(
            type="name-phonetic",
            weight=0.22,
            fields=["legal_name", "dba_name"],
            subfield="phonetic",
            cross_field=False,
        )
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0"}, "dba_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": set(), "dba_name.phonetic": {"SM0"}})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_signal_exposes_weight_as_float():
    signal = build_signal(
        cfg(type="name-token", weight="0.10", fields=["legal_name"], subfield="clean")
    )
    assert signal.weight == 0.10
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matching.signals'`

- [ ] **Step 3: Implement `matching/signals.py`**

```python
"""Signal implementations.

Each signal scores one kind of evidence that two carriers are the same
operation. Every score() returns a float in [0.0, 1.0] or None.

None means "not evaluable" — data missing on one or both sides. It is not the
same as 0.0, which means "evaluated, no similarity". Returning 0.0 for missing
data would penalize carriers for absent records rather than judging them
neutrally.
"""

import logging

from matching.documents import CarrierDoc, ScoringContext
from matching.tokens import blended_overlap

logger = logging.getLogger(__name__)


class Signal:
    type_names: tuple[str, ...] = ()

    def __init__(self, config):
        self.config = config
        self.signal_type = config.type
        self.weight = float(config.weight)

    def score(self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext) -> float | None:
        raise NotImplementedError


class NameOverlapSignal(Signal):
    """Token-set overlap over name fields.

    Registered for both name-phonetic and name-token: the math is identical and
    only the subfield differs. Listing the same type twice in config with
    different subfields is how the double-metaphone and Beider-Morse arms get
    weighted independently.
    """

    type_names = ("name-phonetic", "name-token")

    def score(self, pred, cand, ctx):
        fields = list(self.config.fields)
        subfield = self.config.subfield
        cross_field = getattr(self.config, "cross_field", False)

        if cross_field:
            pairings = [(p, c) for p in fields for c in fields]
        else:
            pairings = [(f, f) for f in fields]

        best = None
        for pred_field, cand_field in pairings:
            score = blended_overlap(
                pred.token_set(pred_field, subfield),
                cand.token_set(cand_field, subfield),
            )
            if score is not None and (best is None or score > best):
                best = score
        return best


SIGNAL_TYPES: dict[str, type[Signal]] = {}


def _register(signal_class: type[Signal]) -> None:
    for name in signal_class.type_names:
        SIGNAL_TYPES[name] = signal_class


_register(NameOverlapSignal)


def build_signal(config) -> Signal:
    signal_class = SIGNAL_TYPES.get(config.type)
    if signal_class is None:
        raise ValueError(
            "unknown signal type {!r}; known types are {}".format(
                config.type, ", ".join(sorted(SIGNAL_TYPES))
            )
        )
    return signal_class(config)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: PASS — all tests in the file green, including Task 2's

- [ ] **Step 5: Commit**

```bash
git add matching/signals.py tests/test_signals.py
git commit -m "feat: add signal base, registry, and name overlap signal

NameOverlapSignal serves both name-phonetic and name-token; the math is
identical and only the subfield differs. Listing the type twice in config
with different subfields is how the double-metaphone and Beider-Morse arms
are weighted independently.

cross_field catches the classic chameleon move of the old legal name
reappearing as the new DBA."
```

---

### Task 4: Address and exact-identifier signals

**Files:**

- Modify: `matching/signals.py`, `tests/test_signals.py`

**Interfaces:**

- Consumes: Task 3's `Signal`, `_register`; `matching.tokens.containment`, `normalize_phone`, `normalize_text_identifier`
- Produces: `AddressSignal` (type `address`), `ExactIdentifierSignal` (type `exact-identifier`)

**Note on config shape:** `exact-identifier` takes `phone_fields` and `text_fields` rather than the single `fields` list shown in the spec's example. This signal reads raw `_source` values, not analyzed tokens, so it needs no `.clean` subfield paths — and splitting the keys makes normalization explicit instead of inferred from field names. Task 9 updates the spec snippet to match.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signals.py`:

```python
def address_cfg(**overrides):
    base = dict(
        type="address",
        weight=0.20,
        fields=["phy_street", "mailing_street"],
        exact_subfield="clean",
        fuzzy_subfield="tokens",
        fuzzy_scale=0.7,
    )
    base.update(overrides)
    return cfg(**base)


def test_address_exact_match_scores_one():
    signal = build_signal(address_cfg())
    tokens = {"phy_street.clean": {"123 main street"}, "mailing_street.clean": set()}
    pred = make_doc(source={"phy_state": "OR"}, tokens=tokens)
    cand = make_doc(source={"phy_state": "OR"}, tokens=dict(tokens))
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_address_fuzzy_match_is_scaled_down():
    signal = build_signal(address_cfg())
    pred = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street"}, "phy_street.tokens": {"123", "main", "street"}},
    )
    cand = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street suite 4"}, "phy_street.tokens": {"123", "main", "street", "suite", "4"}},
    )
    # containment is 1.0 (pred tokens fully inside cand), scaled by fuzzy_scale
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.7)


def test_address_fuzzy_match_across_states_is_halved():
    # "100 MAIN ST" exists in every state; a fuzzy hit across states is weak.
    signal = build_signal(address_cfg())
    pred = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street"}, "phy_street.tokens": {"123", "main", "street"}},
    )
    cand = make_doc(
        source={"phy_state": "TX"},
        tokens={"phy_street.clean": {"123 main street suite 4"}, "phy_street.tokens": {"123", "main", "street", "suite", "4"}},
    )
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.35)


def test_address_exact_match_across_states_is_not_halved():
    # Identical street in a different state is genuinely suspicious.
    signal = build_signal(address_cfg())
    tokens = {"phy_street.clean": {"123 main street"}}
    pred = make_doc(source={"phy_state": "OR"}, tokens=dict(tokens))
    cand = make_doc(source={"phy_state": "TX"}, tokens=dict(tokens))
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_address_returns_none_when_no_address_data():
    signal = build_signal(address_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None


def identifier_cfg():
    return cfg(
        type="exact-identifier",
        weight=0.12,
        phone_fields=["telephone", "fax"],
        text_fields=["email_address"],
    )


def test_exact_identifier_matching_phone_scores_one():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "(503) 289-5558"})
    cand = make_doc(source={"telephone": "503-289-5558"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_exact_identifier_matching_email_scores_one():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"email_address": "Joe@Example.com"})
    cand = make_doc(source={"email_address": "joe@example.com "})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_exact_identifier_different_values_score_zero():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "5032895558"})
    cand = make_doc(source={"telephone": "2025555555"})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_exact_identifier_placeholder_phones_never_match():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "0000000000"})
    cand = make_doc(source={"telephone": "0000000000"})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_exact_identifier_returns_none_when_both_sides_blank():
    signal = build_signal(identifier_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: FAIL with `ValueError: unknown signal type 'address'`

- [ ] **Step 3: Implement the two signals**

Add to `matching/signals.py` — the import line at the top becomes:

```python
from matching.tokens import (
    blended_overlap,
    containment,
    normalize_phone,
    normalize_text_identifier,
)
```

Then append these classes above the `SIGNAL_TYPES` block:

```python
CROSS_STATE_FUZZY_PENALTY = 0.5


class AddressSignal(Signal):
    """Street similarity, exact first then fuzzy.

    The exact subfield uses a keyword tokenizer, so its "token set" is a single
    normalized string. The fuzzy subfield is standard-tokenized with street
    suffix synonyms applied.
    """

    type_names = ("address",)

    def score(self, pred, cand, ctx):
        fields = list(self.config.fields)
        exact_subfield = self.config.exact_subfield
        fuzzy_subfield = self.config.fuzzy_subfield
        fuzzy_scale = float(self.config.fuzzy_scale)

        pred_state = pred.value("phy_state")
        cand_state = cand.value("phy_state")
        same_state = bool(pred_state) and pred_state == cand_state

        best = None
        saw_any_data = False

        for pred_field in fields:
            for cand_field in fields:
                pred_exact = pred.token_set(pred_field, exact_subfield)
                cand_exact = cand.token_set(cand_field, exact_subfield)
                pred_fuzzy = pred.token_set(pred_field, fuzzy_subfield)
                cand_fuzzy = cand.token_set(cand_field, fuzzy_subfield)

                if not (pred_exact or pred_fuzzy) or not (cand_exact or cand_fuzzy):
                    continue
                saw_any_data = True

                if pred_exact and pred_exact == cand_exact:
                    score = 1.0
                else:
                    score = containment(pred_fuzzy, cand_fuzzy) * fuzzy_scale
                    if not same_state:
                        # "100 MAIN ST" exists in every state. An exact match
                        # across states stays strong; a fuzzy one does not.
                        score *= CROSS_STATE_FUZZY_PENALTY

                if best is None or score > best:
                    best = score

        return best if saw_any_data else None


class ExactIdentifierSignal(Signal):
    """Shared phone, fax, or email. Binary.

    Reads raw _source rather than analyzed tokens, so placeholder rejection
    happens here rather than relying on the analyzer.
    """

    type_names = ("exact-identifier",)

    def score(self, pred, cand, ctx):
        pred_values = set()
        cand_values = set()

        for field_name in getattr(self.config, "phone_fields", []):
            _collect(pred_values, pred.value(field_name), normalize_phone)
            _collect(cand_values, cand.value(field_name), normalize_phone)

        for field_name in getattr(self.config, "text_fields", []):
            _collect(pred_values, pred.value(field_name), normalize_text_identifier)
            _collect(cand_values, cand.value(field_name), normalize_text_identifier)

        if not pred_values or not cand_values:
            return None
        return 1.0 if pred_values & cand_values else 0.0


def _collect(target: set, raw, normalize) -> None:
    """Normalize raw (scalar or list) into target, dropping None results."""
    if raw is None:
        return
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        normalized = normalize(item)
        if normalized is not None:
            target.add(normalized)
```

Then extend the registration block:

```python
_register(NameOverlapSignal)
_register(AddressSignal)
_register(ExactIdentifierSignal)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: PASS — all tests in the file green, including Tasks 2-3's

- [ ] **Step 5: Commit**

```bash
git add matching/signals.py tests/test_signals.py
git commit -m "feat: add address and exact-identifier signals

Address matching tries exact-after-normalization first, then falls back to
fuzzy token containment. A fuzzy hit across different states is halved
because '100 MAIN ST' exists everywhere; an exact cross-state match is left
at full strength because that is genuinely suspicious.

exact-identifier takes phone_fields and text_fields separately so
normalization is explicit rather than inferred from field names."
```

---

### Task 5: Agent, temporal, and VIN signals

**Files:**

- Modify: `matching/signals.py`, `tests/test_signals.py`

**Interfaces:**

- Consumes: Task 4's `_collect`, `Signal`, `_register`
- Produces: `AgentSignal` (`agent`), `TemporalSignal` (`temporal`), `VinOverlapSignal` (`vin-overlap`), and `parse_flexible_date(value) -> datetime.date | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signals.py`:

```python
import datetime

from matching.signals import parse_flexible_date


def test_parse_flexible_date_reads_iso():
    assert parse_flexible_date("2022-07-09") == datetime.date(2022, 7, 9)


def test_parse_flexible_date_reads_oracle_format_with_century_pivot():
    # 01-JUN-74 is a 1974 registration, not 2074.
    assert parse_flexible_date("01-JUN-74") == datetime.date(1974, 6, 1)


def test_parse_flexible_date_pivots_low_years_to_2000s():
    assert parse_flexible_date("23-JAN-02") == datetime.date(2002, 1, 23)


def test_parse_flexible_date_returns_none_for_junk():
    assert parse_flexible_date("") is None
    assert parse_flexible_date(None) is None
    assert parse_flexible_date("not a date") is None


def agent_cfg():
    return cfg(
        type="agent",
        weight=0.04,
        name_field="boc3_agents.co_name",
        idf_weighted=True,
    )


def test_agent_shared_rare_agent_scores_high():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={"TINY FILER": 2}, total_agent_carriers=1426508)
    pred = make_doc(source={"boc3_agents": [{"co_name": "TINY FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "TINY FILER"}]})
    assert signal.score(pred, cand, ctx) > 0.99


def test_agent_shared_common_agent_scores_low():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={"BIG FILER": 134283}, total_agent_carriers=1426508)
    pred = make_doc(source={"boc3_agents": [{"co_name": "BIG FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "BIG FILER"}]})
    assert signal.score(pred, cand, ctx) < 0.15


def test_agent_no_shared_agent_scores_zero():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=100)
    pred = make_doc(source={"boc3_agents": [{"co_name": "A FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "B FILER"}]})
    assert signal.score(pred, cand, ctx) == 0.0


def test_agent_blank_names_never_match():
    # co_name is blank on 23.3% of BOC-3 rows.
    signal = build_signal(agent_cfg())
    pred = make_doc(source={"boc3_agents": [{"co_name": ""}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "  "}]})
    assert signal.score(pred, cand, ScoringContext()) is None


def temporal_cfg(**overrides):
    base = dict(
        type="temporal",
        weight=0.05,
        predecessor_date="out_of_service_orders.oos_date",
        successor_date="add_date",
        max_gap_days=365,
    )
    base.update(overrides)
    return cfg(**base)


def test_temporal_same_day_reopen_scores_one():
    signal = build_signal(temporal_cfg())
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2022-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_temporal_decays_linearly_over_the_window():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2022-02-20"})  # 50 days
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.5)


def test_temporal_beyond_the_window_scores_zero():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2024-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_temporal_uses_the_latest_shutdown_date():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(
        source={"out_of_service_orders": [{"oos_date": "2010-01-01"}, {"oos_date": "2022-01-01"}]}
    )
    cand = make_doc(source={"add_date": "2022-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_temporal_pre_registered_shell_scores_at_half_weight():
    # Registering the successor before the shutdown is a real tactic, but
    # weaker evidence than registering right after. 90 days before is halfway
    # through the 180-day backward window, scaled by 0.5 => 0.25.
    signal = build_signal(temporal_cfg(max_gap_days=365))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-07-01"}]})
    earlier = make_doc(source={"add_date": "2022-04-02"})  # 90 days before
    assert signal.score(pred, earlier, ScoringContext()) == pytest.approx(0.25)


def test_temporal_beyond_the_backward_window_scores_zero():
    signal = build_signal(temporal_cfg(max_gap_days=365))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-07-01"}]})
    earlier = make_doc(source={"add_date": "2021-01-01"})  # far before the window
    assert signal.score(pred, earlier, ScoringContext()) == 0.0


def test_temporal_returns_none_when_a_date_is_missing():
    signal = build_signal(temporal_cfg())
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    assert signal.score(pred, make_doc(), ScoringContext()) is None


def vin_cfg():
    return cfg(
        type="vin-overlap",
        weight=0.08,
        fields=["crashes.vehicle_identification_number"],
    )


def test_vin_overlap_shared_vin_scores_one():
    signal = build_signal(vin_cfg())
    pred = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    cand = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_vin_overlap_no_shared_vin_scores_zero():
    signal = build_signal(vin_cfg())
    pred = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    cand = make_doc(source={"crashes": [{"vehicle_identification_number": "2XYZ"}]})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_vin_overlap_returns_none_without_vins():
    signal = build_signal(vin_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_flexible_date'`

- [ ] **Step 3: Implement the three signals**

Add to the top of `matching/signals.py`:

```python
import datetime
```

Append before the registration block:

```python
# Two-digit years above this pivot are 19xx. FMCSA carrier registrations go
# back to the 1970s, so "01-JUN-74" is 1974. Java's yy pattern would render it
# as 2074, which is why add_date needs explicit handling rather than a naive
# dd-MMM-yy date mapping.
CENTURY_PIVOT = 30

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

BACKWARD_WINDOW_DAYS = 180
BACKWARD_SCALE = 0.5


def parse_flexible_date(value) -> datetime.date | None:
    """Parse ISO (2022-07-09) or Oracle (01-JUN-74) dates. None on failure."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None

    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        pass

    parts = text.split("-")
    if len(parts) == 3 and parts[1] in _MONTHS:
        try:
            day = int(parts[0])
            year = int(parts[2])
        except ValueError:
            return None
        if len(parts[2]) == 2:
            year += 1900 if year > CENTURY_PIVOT else 2000
        try:
            return datetime.date(year, _MONTHS[parts[1]], day)
        except ValueError:
            return None
    return None


class AgentSignal(Signal):
    """Shared BOC-3 process agent, weighted by how rare the agent is.

    Only 89 distinct agents cover 1.43M filings, so an unweighted version of
    this signal fires on roughly 7% of random pairs. Weight is deliberately low.
    """

    type_names = ("agent",)

    def score(self, pred, cand, ctx):
        pred_agents = set()
        cand_agents = set()
        _collect(pred_agents, pred.value(self.config.name_field), normalize_text_identifier)
        _collect(cand_agents, cand.value(self.config.name_field), normalize_text_identifier)

        if not pred_agents or not cand_agents:
            return None

        shared = pred_agents & cand_agents
        if not shared:
            return 0.0
        return max(ctx.agent_rarity(name) for name in shared)


class TemporalSignal(Signal):
    """Closeness between the predecessor's shutdown and the successor's registration."""

    type_names = ("temporal",)

    def score(self, pred, cand, ctx):
        shutdown = _latest_date(pred.value(self.config.predecessor_date))
        registered = _latest_date(cand.value(self.config.successor_date))
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


class VinOverlapSignal(Signal):
    """Any shared VIN. Binary — VINs are globally unique, so one is damning."""

    type_names = ("vin-overlap",)

    def score(self, pred, cand, ctx):
        pred_vins = set()
        cand_vins = set()
        for path in self.config.fields:
            _collect(pred_vins, pred.value(path), normalize_text_identifier)
            _collect(cand_vins, cand.value(path), normalize_text_identifier)

        if not pred_vins or not cand_vins:
            return None
        return 1.0 if pred_vins & cand_vins else 0.0


def _latest_date(raw) -> datetime.date | None:
    """Most recent parseable date from a scalar or list."""
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    dates = [d for d in (parse_flexible_date(item) for item in items) if d is not None]
    return max(dates) if dates else None
```

Extend the registration block:

```python
_register(AgentSignal)
_register(TemporalSignal)
_register(VinOverlapSignal)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_signals.py -v`
Expected: PASS — all tests in the file green, including Tasks 2-4's

- [ ] **Step 5: Commit**

```bash
git add matching/signals.py tests/test_signals.py
git commit -m "feat: add agent, temporal, and VIN overlap signals

parse_flexible_date pivots two-digit years at 30 so '01-JUN-74' resolves to
1974. Java's yy pattern would render it 2074, which is why add_date cannot
simply be mapped as dd-MMM-yy.

Temporal scoring is asymmetric: a successor registered before the shutdown
is a pre-positioned shell, which is real but weaker evidence than one
registered days after.

Agent scoring is IDF-weighted and weighted low; 89 distinct agents cover
1.43M BOC-3 filings."
```

---

### Task 6: PairScorer aggregation and guards

**Files:**

- Create: `matching/scorer.py`, `tests/test_scorer.py`

**Interfaces:**

- Consumes: `matching.signals.build_signal`, `matching.documents`
- Produces:
  - `SignalContribution(signal_type, subfield, weight, score, contribution)`
  - `ScoredPair(predecessor, successor, total_score, signals, matched_on, signals_present)`
  - `PairScorer(signal_configs, scoring_config)` with `score_pair(pred, cand, ctx) -> ScoredPair | None`
  - `IDENTITY_SIGNAL_TYPES: frozenset[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scorer.py`:

```python
from types import SimpleNamespace

import pytest

from matching.documents import CarrierDoc, ScoringContext
from matching.scorer import PairScorer


def cfg(**kwargs):
    return SimpleNamespace(**kwargs)


def doc(dot_number="1", source=None, tokens=None):
    return CarrierDoc(dot_number=dot_number, source=source or {}, tokens=tokens or {})


def scoring(**overrides):
    base = dict(
        min_total_score=0.35,
        min_signals=2,
        require_identity_signal=True,
        max_pairs_per_predecessor=10,
    )
    base.update(overrides)
    return cfg(**base)


NAME_SIGNAL = cfg(
    type="name-phonetic", weight=0.5, fields=["legal_name"], subfield="phonetic"
)
VIN_SIGNAL = cfg(
    type="vin-overlap", weight=0.5, fields=["crashes.vehicle_identification_number"]
)


def strong_pair():
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0", "TRKN"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0", "TRKN"}},
    )
    return pred, cand


def test_scores_a_strong_pair():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, cand = strong_pair()
    result = scorer.score_pair(pred, cand, ScoringContext())
    assert result is not None
    assert result.total_score == pytest.approx(1.0)
    assert result.signals_present == 2
    assert set(result.matched_on) == {"name-phonetic", "vin-overlap"}


def test_renormalizes_over_available_signals():
    # VIN is unevaluable; the name signal alone should carry the total,
    # not be diluted by the absent VIN weight.
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1))
    pred = doc("1", tokens={"legal_name.phonetic": {"SM0"}})
    cand = doc("2", tokens={"legal_name.phonetic": {"SM0"}})
    result = scorer.score_pair(pred, cand, ScoringContext())
    assert result.total_score == pytest.approx(1.0)
    assert result.signals_present == 1


def test_min_signals_guard_rejects_thin_evidence():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=2))
    pred = doc("1", tokens={"legal_name.phonetic": {"SM0"}})
    cand = doc("2", tokens={"legal_name.phonetic": {"SM0"}})
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_min_total_score_guard_rejects_weak_pairs():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_total_score=0.9))
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "2XYZ"}]},
        tokens={"legal_name.phonetic": {"SM0"}},
    )
    # name 1.0 * 0.5 + vin 0.0 * 0.5 = 0.5, below the 0.9 floor
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_require_identity_signal_rejects_vin_only_match():
    temporal = cfg(
        type="temporal",
        weight=0.5,
        predecessor_date="out_of_service_orders.oos_date",
        successor_date="add_date",
        max_gap_days=365,
    )
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL, temporal], scoring(min_signals=1))
    pred = doc(
        "1",
        source={
            "crashes": [{"vehicle_identification_number": "9ZZZ"}],
            "out_of_service_orders": [{"oos_date": "2022-01-01"}],
        },
    )
    cand = doc(
        "2",
        source={
            "crashes": [{"vehicle_identification_number": "8YYY"}],
            "add_date": "2022-01-01",
        },
    )
    # temporal fires perfectly and VIN is evaluable but zero. No identity
    # signal fired, so this must be rejected: 340K carriers are shut down and
    # temporal proximity alone is meaningless.
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_identity_signal_must_actually_fire_not_merely_be_evaluable():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1, min_total_score=0.0))
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"AAA"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"BBB"}},
    )
    # The name signal is evaluable but scores 0.0, so no identity signal fired.
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_returns_none_when_no_signal_is_evaluable():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1))
    assert scorer.score_pair(doc("1"), doc("2"), ScoringContext()) is None


def test_contributions_record_per_signal_detail():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, cand = strong_pair()
    result = scorer.score_pair(pred, cand, ScoringContext())
    by_type = {c.signal_type: c for c in result.signals}
    assert by_type["name-phonetic"].weight == 0.5
    assert by_type["name-phonetic"].score == pytest.approx(1.0)
    assert by_type["name-phonetic"].contribution == pytest.approx(0.5)


def test_rejects_a_carrier_matched_against_itself():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, _ = strong_pair()
    assert scorer.score_pair(pred, pred, ScoringContext()) is None


def test_rejects_zero_total_weight_config():
    with pytest.raises(ValueError, match="weights sum to zero"):
        PairScorer([cfg(type="name-phonetic", weight=0.0, fields=["legal_name"], subfield="phonetic")], scoring())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_scorer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matching.scorer'`

- [ ] **Step 3: Implement `matching/scorer.py`**

```python
"""Pair scoring: aggregate signals, renormalize, apply guards."""

import logging
from dataclasses import dataclass, field

from matching.documents import CarrierDoc, ScoringContext
from matching.signals import build_signal

logger = logging.getLogger(__name__)

# A pair must be tied together by at least one of these. Temporal proximity and
# a shared process agent corroborate; they cannot carry a match on their own.
IDENTITY_SIGNAL_TYPES = frozenset(
    {"name-phonetic", "name-token", "address", "exact-identifier"}
)


@dataclass
class SignalContribution:
    signal_type: str
    subfield: str | None
    weight: float
    score: float
    contribution: float


@dataclass
class ScoredPair:
    predecessor: CarrierDoc
    successor: CarrierDoc
    total_score: float
    signals: list[SignalContribution] = field(default_factory=list)
    matched_on: list[str] = field(default_factory=list)
    signals_present: int = 0


class PairScorer:
    def __init__(self, signal_configs, scoring_config):
        self.signals = [build_signal(c) for c in signal_configs]
        if sum(s.weight for s in self.signals) <= 0:
            raise ValueError("signal weights sum to zero; nothing can be scored")

        self.min_total_score = float(getattr(scoring_config, "min_total_score", 0.0))
        self.min_signals = int(getattr(scoring_config, "min_signals", 1))
        self.require_identity_signal = bool(
            getattr(scoring_config, "require_identity_signal", True)
        )

    def score_pair(
        self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext
    ) -> ScoredPair | None:
        if pred.dot_number == cand.dot_number:
            return None

        contributions: list[SignalContribution] = []
        for signal in self.signals:
            score = signal.score(pred, cand, ctx)
            if score is None:
                continue
            contributions.append(
                SignalContribution(
                    signal_type=signal.signal_type,
                    subfield=getattr(signal.config, "subfield", None),
                    weight=signal.weight,
                    score=score,
                    contribution=signal.weight * score,
                )
            )

        if len(contributions) < self.min_signals:
            return None

        fired = [c for c in contributions if c.score > 0.0]
        if self.require_identity_signal and not any(
            c.signal_type in IDENTITY_SIGNAL_TYPES for c in fired
        ):
            return None

        total_weight = sum(c.weight for c in contributions)
        if total_weight <= 0:
            return None
        total_score = sum(c.contribution for c in contributions) / total_weight

        if total_score < self.min_total_score:
            return None

        return ScoredPair(
            predecessor=pred,
            successor=cand,
            total_score=total_score,
            signals=contributions,
            matched_on=sorted({c.signal_type for c in fired}),
            signals_present=len(contributions),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — the whole suite green across test_tokens, test_signals, and test_scorer

- [ ] **Step 5: Commit**

```bash
git add matching/scorer.py tests/test_scorer.py
git commit -m "feat: add PairScorer with renormalization and three guards

Unevaluable signals drop out and weights renormalize over what remains, so a
carrier with no BOC-3 record is judged neutrally rather than penalized for
missing data. Because renormalizing lets one lucky signal reach 1.0, three
guards bound it: min_signals, min_total_score, and require_identity_signal.

require_identity_signal checks that an identity signal actually fired, not
merely that it was evaluable. Temporal proximity alone is meaningless when
340K carriers have been shut down."
```

---

### Task 7: Lift `compute_id` into utils

**Files:**

- Create: `utils/id_utils.py`
- Modify: `phase_providers/phase_index_populate.py:26-32`

**Interfaces:**

- Consumes: nothing
- Produces: `compute_id(record: dict, id_field: str | list[str]) -> str`

- [ ] **Step 1: Create `utils/id_utils.py`**

```python
"""Deterministic document _id construction.

Shared by index-populate (CSV rows) and entity-match (scored pairs) so both
build composite keys the same way.
"""


def compute_id(record, id_field):
    """Join a list id_field with '|', or read a single field.

    Raises KeyError when a named field is absent; callers fall back to
    Elasticsearch auto-generated ids.
    """
    if isinstance(id_field, list):
        return "|".join(str(record[field]) for field in id_field)
    return record[id_field]
```

- [ ] **Step 2: Delegate from the existing phase**

In `phase_providers/phase_index_populate.py`, add to the imports:

```python
import utils.id_utils as id_utils
```

Replace the `compute_id` method (lines 26-32) with:

```python
    def compute_id(self, record, id_field):
        # id_field as a list builds a composite key by joining the named
        # fields' values; a KeyError here is handled the same as the
        # single-field case, falling back to an ES auto-generated _id
        return id_utils.compute_id(record, id_field)
```

- [ ] **Step 3: Verify nothing broke**

Run: `python3 -c "import phase_providers.phase_index_populate; import utils.id_utils; print(utils.id_utils.compute_id({'a': 1, 'b': 2}, ['a', 'b']))"`
Expected: `1|2`

- [ ] **Step 4: Commit**

```bash
git add utils/id_utils.py phase_providers/phase_index_populate.py
git commit -m "refactor: lift compute_id into utils for reuse

entity-match needs the same composite-key logic to build deterministic
predecessor|successor ids. The method on PhaseIndexingPopulate now delegates
so there is one implementation."
```

---

### Task 8: Carrier analyzer changes

**Files:**

- Modify: `DOT-Commercial/configuration/carriers/index-settings.json`

**Interfaces:**

- Consumes: nothing
- Produces: analyzers `name_clean`, `name_phonetic`, `name_phonetic_bm`, `street_clean`, `street_tokens`, `phone_clean`

**Warning:** This changes analyzers on an existing index definition. The `carriers` index must be recreated and reloaded (Task 13 covers the run order). Elasticsearch cannot change analyzers on a live index.

- [ ] **Step 1: Replace the file contents**

```json
{
  "index": "carriers-{now/d}-000001",
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "analysis": {
        "filter": {
          "phonetic_dm": {
            "type": "phonetic",
            "encoder": "double_metaphone",
            "max_code_len": 6
          },
          "phonetic_bm": {
            "type": "phonetic",
            "encoder": "beider_morse",
            "rule_type": "approx",
            "name_type": "generic",
            "languageset": ["english", "spanish"]
          },
          "carrier_suffix_stop": {
            "type": "stop",
            "ignore_case": true,
            "stopwords": [
              "llc", "inc", "ltd", "co", "corp", "corporation", "company",
              "trucking", "truck", "transport", "transportation", "express",
              "logistics", "carrier", "carriers", "enterprises", "enterprise",
              "services", "service", "group", "freight", "hauling", "the"
            ]
          },
          "street_suffix_synonyms": {
            "type": "synonym",
            "lenient": true,
            "synonyms": [
              "st, street",
              "ave, av, avenue",
              "rd, road",
              "blvd, boulevard",
              "dr, drive",
              "ln, lane",
              "hwy, highway",
              "pkwy, parkway",
              "ct, court",
              "cir, circle",
              "ste, suite",
              "apt, apartment",
              "n, north",
              "s, south",
              "e, east",
              "w, west"
            ]
          },
          "punct_white": {
            "pattern": "\\p{Punct}",
            "type": "pattern_replace",
            "replacement": " "
          },
          "remove_non_digits": {
            "pattern": "[^\\d]",
            "type": "pattern_replace",
            "replacement": ""
          }
        },
        "analyzer": {
          "name_clean": {
            "filter": ["icu_normalizer", "icu_folding", "punct_white"],
            "tokenizer": "standard"
          },
          "name_phonetic": {
            "filter": [
              "icu_normalizer",
              "icu_folding",
              "punct_white",
              "carrier_suffix_stop",
              "phonetic_dm"
            ],
            "tokenizer": "standard"
          },
          "name_phonetic_bm": {
            "filter": [
              "icu_normalizer",
              "icu_folding",
              "punct_white",
              "carrier_suffix_stop",
              "phonetic_bm"
            ],
            "tokenizer": "standard"
          },
          "street_clean": {
            "filter": ["icu_normalizer", "icu_folding", "punct_white", "trim"],
            "tokenizer": "keyword"
          },
          "street_tokens": {
            "filter": [
              "icu_normalizer",
              "icu_folding",
              "punct_white",
              "street_suffix_synonyms"
            ],
            "tokenizer": "standard"
          },
          "phone_clean": {
            "filter": ["remove_non_digits"],
            "tokenizer": "keyword"
          }
        }
      }
    }
  }
}
```

Key changes from the previous version:

- `metaphone` is gone, replaced by `phonetic_dm` (`double_metaphone`, `max_code_len: 6`). The default length of 4 over-collides on company-name tokens.
- `phonetic_bm` is new. `beider_morse` does not support the `replace` setting and emits several tokens per input, so this subfield will be the largest in the index.
- `carrier_suffix_stop` runs **before** phonetic encoding and only in the phonetic analyzers. Nearly every carrier name ends in these words; left in, overlap is dominated by noise every carrier shares.
- `street_suffix_map` is **deleted**. It was referenced by no analyzer, and its unanchored `(st)` → `street` pattern would have mapped `stone` → `streetone`. Replaced by a term-level `synonym` filter.
- `street_tokens` is new — `street_clean` uses a keyword tokenizer and structurally cannot do fuzzy matching.

- [ ] **Step 2: Validate the JSON parses**

Run: `python3 -c "import json; c = json.load(open('DOT-Commercial/configuration/carriers/index-settings.json')); print(sorted(c['settings']['index']['analysis']['analyzer']))"`
Expected: `['name_clean', 'name_phonetic', 'name_phonetic_bm', 'phone_clean', 'street_clean', 'street_tokens']`

- [ ] **Step 3: Verify the analyzers work against a live cluster**

This requires the cluster from `README.md` with `analysis-icu` and `analysis-phonetic` installed. Create the index with the new settings and analyze a sample:

```bash
python3 execute_project.py --project=DOT-Commercial --step=carriers --phase=index-create
```

Then confirm the corporate-suffix stop and double-metaphone both apply:

```bash
curl -sk -u "$ES_USER:$ES_PASS" "$ES_URL/carriers-$(date +%Y.%m.%d)-000001/_analyze" \
  -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic","text":"SMITH TRUCKING LLC"}'
```

Expected: tokens for `SMITH` only — `TRUCKING` and `LLC` are stopped. If you see three tokens, `carrier_suffix_stop` is not wired in.

Then confirm Beider-Morse emits multiple tokens:

```bash
curl -sk -u "$ES_USER:$ES_PASS" "$ES_URL/carriers-$(date +%Y.%m.%d)-000001/_analyze" \
  -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic_bm","text":"SMITH"}'
```

Expected: several tokens at the same position.

- [ ] **Step 4: Commit**

```bash
git add DOT-Commercial/configuration/carriers/index-settings.json
git commit -m "feat: upgrade carrier analyzers for chameleon matching

Replaces metaphone with double_metaphone (max_code_len 6; the default 4
over-collides on company names) and adds a beider_morse analyzer so encoder
performance can be compared empirically from config.

Adds carrier_suffix_stop before phonetic encoding: nearly every carrier name
ends in LLC/INC/TRUCKING, and scoring happens in Python where there is no
BM25 IDF to discount them.

Deletes street_suffix_map, which was dead config and broken — its unanchored
'(st)' pattern would have mapped 'stone' to 'streetone'. Replaced with a
term-level synonym filter plus a new street_tokens analyzer, since
street_clean's keyword tokenizer cannot do fuzzy matching at all."
```

---

### Task 9: Carrier mappings and the `add_date` century fix

**Files:**

- Modify: `DOT-Commercial/configuration/carriers/index-mappings.json`, `DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json`

**Interfaces:**

- Consumes: Task 8's analyzers
- Produces: subfields `legal_name.phonetic`, `legal_name.phonetic_bm`, `legal_name.clean`, same for `dba_name`; `phy_street.tokens`, `mailing_street.tokens`; typed `add_date`, `phy_zip`, `fax`, `boc3_agents.co_name`

- [ ] **Step 1: Add the name subfields**

In `DOT-Commercial/configuration/carriers/index-mappings.json`, replace the `legal_name` and `dba_name` blocks (lines 8-39) with:

```json
      "legal_name": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "clean": { "type": "text", "analyzer": "name_clean" },
          "phonetic": { "type": "text", "analyzer": "name_phonetic" },
          "phonetic_bm": { "type": "text", "analyzer": "name_phonetic_bm" }
        }
      },
      "dba_name": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "clean": { "type": "text", "analyzer": "name_clean" },
          "phonetic": { "type": "text", "analyzer": "name_phonetic" },
          "phonetic_bm": { "type": "text", "analyzer": "name_phonetic_bm" }
        }
      },
```

- [ ] **Step 2: Add the street `tokens` subfield**

Replace the `phy_street` and `mailing_street` blocks with:

```json
      "phy_street": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "clean": { "type": "text", "analyzer": "street_clean" },
          "tokens": { "type": "text", "analyzer": "street_tokens" }
        }
      },
```

and

```json
      "mailing_street": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "clean": { "type": "text", "analyzer": "street_clean" },
          "tokens": { "type": "text", "analyzer": "street_tokens" }
        }
      },
```

- [ ] **Step 3: Pin the previously-dynamic fields**

Add these properties alongside the existing ones:

```json
      "add_date": {
        "type": "date",
        "format": "strict_date_optional_time||yyyy-MM-dd"
      },
      "phy_zip": { "type": "keyword" },
      "mailing_zip": { "type": "keyword" },
      "fax": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" },
          "clean": { "type": "text", "analyzer": "phone_clean" }
        }
      },
      "boc3_agents": {
        "properties": {
          "dot_number": { "type": "long" },
          "co_name": {
            "type": "text",
            "fields": {
              "keyword": { "type": "keyword" },
              "clean": { "type": "text", "analyzer": "name_clean" },
              "phonetic": { "type": "text", "analyzer": "name_phonetic" }
            }
          },
          "street_po": {
            "type": "text",
            "fields": {
              "keyword": { "type": "keyword" },
              "clean": { "type": "text", "analyzer": "street_clean" },
              "tokens": { "type": "text", "analyzer": "street_tokens" }
            }
          },
          "city": { "type": "keyword" },
          "state_code": { "type": "keyword" },
          "zip_code": { "type": "keyword" }
        }
      }
```

Note `add_date` is mapped as ISO **only**. The pipeline rewrites the raw
`dd-MMM-yy` value into ISO before indexing, so Elasticsearch never sees the
Oracle format. Mapping `dd-MMM-yy` here instead would silently produce 2074 for
a 1974 registration, because Java's `yy` pattern pivots to 2000-2099.

- [ ] **Step 4: Add the century-fix processor to the pipeline**

In `DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json`, add this as the **first** entry in `processors`, before the five `enrich` blocks:

```json
    {
      "script": {
        "description": "rewrite add_date from dd-MMM-yy to ISO with a 1900/2000 century pivot",
        "lang": "painless",
        "source": "if (ctx.add_date == null) { return; } String raw = ctx.add_date.toString().trim().toUpperCase(); if (raw.length() == 0) { ctx.remove('add_date'); return; } def parts = /-/.split(raw); if (parts.length != 3) { ctx.remove('add_date'); return; } def months = ['JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12]; if (!months.containsKey(parts[1])) { ctx.remove('add_date'); return; } int day = Integer.parseInt(parts[0]); int year = Integer.parseInt(parts[2]); if (parts[2].length() == 2) { year = year > 30 ? 1900 + year : 2000 + year; } ctx.add_date = String.format('%04d-%02d-%02d', new def[]{year, months[parts[1]], day});",
        "ignore_failure": true
      }
    },
```

The pivot at 30 mirrors `matching/signals.py`'s `CENTURY_PIVOT`. FMCSA
registrations go back to the 1970s, so `74` must resolve to 1974.
`ignore_failure: true` drops unparseable dates rather than failing the document
— the temporal signal already treats a missing date as `None`.

- [ ] **Step 5: Validate both files parse**

Run:

```bash
python3 -c "
import json
m = json.load(open('DOT-Commercial/configuration/carriers/index-mappings.json'))
p = json.load(open('DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json'))
props = m['mappings']['properties']
print('legal_name subfields:', sorted(props['legal_name']['fields']))
print('phy_street subfields:', sorted(props['phy_street']['fields']))
print('add_date:', props['add_date'])
print('first processor:', list(p['processors'][0]))
"
```

Expected:

```
legal_name subfields: ['clean', 'keyword', 'phonetic', 'phonetic_bm']
phy_street subfields: ['clean', 'keyword', 'tokens']
add_date: {'type': 'date', 'format': 'strict_date_optional_time||yyyy-MM-dd'}
first processor: ['script']
```

- [ ] **Step 6: Verify the pipeline script against a live cluster**

```bash
python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup --phase=pipelines
```

Then simulate:

```bash
curl -sk -u "$ES_USER:$ES_PASS" "$ES_URL/_ingest/pipeline/carrier-enrichment-pipeline-000001/_simulate" \
  -H 'Content-Type: application/json' \
  -d '{"docs":[{"_source":{"dot_number":1,"add_date":"01-JUN-74"}},{"_source":{"dot_number":2,"add_date":"23-JAN-02"}}]}'
```

Expected: `add_date` of `1974-06-01` and `2002-01-23`. If you see `2074-06-01`, the pivot is inverted.

- [ ] **Step 7: Commit**

```bash
git add DOT-Commercial/configuration/carriers/index-mappings.json DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json
git commit -m "feat: add scoring subfields and fix add_date century handling

Adds phonetic/phonetic_bm/clean subfields to the name fields, a tokens
subfield to the streets, and explicit mappings for boc3_agents.co_name and
street_po which were previously dynamic text with no analyzable subfield.

add_date was silently indexed as text: its dd-MMM-yy format is not one
Elasticsearch's dynamic date detection recognizes. Mapping it as dd-MMM-yy
would be worse than leaving it, because Java's yy pattern pivots to
2000-2099 and would render a 1974 registration as 2074. A Painless processor
rewrites it to ISO with a pivot at 30, mirroring CENTURY_PIVOT in
matching/signals.py."
```

---

### Task 10: Chameleon-detection configuration files

**Files:**

- Create: `DOT-Commercial/configuration/chameleon-detection/index-config.json`, `index-mappings.json`, `entity-match.json`
- Modify: `docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md`

**Interfaces:**

- Consumes: nothing
- Produces: the config `phase_entity_match.py` reads in Task 13

- [ ] **Step 1: Create `index-config.json`**

```json
{
  "index": "chameleon-candidates-{now/d}-000001",
  "alias": "chameleon-candidates-000001"
}
```

No `source`, `id_field`, or `pipeline` keys — this index is not populated from CSV, so `index-populate` never runs against it. `entity-match` builds its own ids.

- [ ] **Step 2: Create `index-mappings.json`**

```json
{
  "index": "chameleon-candidates-{now/d}-000001",
  "mappings": {
    "properties": {
      "total_score": { "type": "float" },
      "gap_days": { "type": "integer" },
      "signals_present": { "type": "integer" },
      "matched_on": { "type": "keyword" },
      "generated_at": { "type": "date" },
      "run_id": { "type": "keyword" },
      "predecessor": {
        "properties": {
          "dot_number": { "type": "keyword" },
          "legal_name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
          "dba_name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
          "phy_street": { "type": "keyword" },
          "phy_city": { "type": "keyword" },
          "phy_state": { "type": "keyword" },
          "shutdown_date": { "type": "date", "format": "strict_date_optional_time||yyyy-MM-dd" },
          "shutdown_reason": { "type": "keyword" }
        }
      },
      "successor": {
        "properties": {
          "dot_number": { "type": "keyword" },
          "legal_name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
          "dba_name": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
          "phy_street": { "type": "keyword" },
          "phy_city": { "type": "keyword" },
          "phy_state": { "type": "keyword" },
          "add_date": { "type": "date", "format": "strict_date_optional_time||yyyy-MM-dd" }
        }
      },
      "signals": {
        "properties": {
          "signal_type": { "type": "keyword" },
          "subfield": { "type": "keyword" },
          "weight": { "type": "float" },
          "score": { "type": "float" },
          "contribution": { "type": "float" }
        }
      }
    }
  }
}
```

- [ ] **Step 3: Create `entity-match.json`**

```json
{
  "source_index": "carriers-000001",
  "predecessors": {
    "selector": "out-of-service",
    "oos_status": ["ACTIVE"],
    "oos_date_from": "2020-01-01",
    "max_predecessors": 2000
  },
  "candidates": {
    "max_candidates": 100,
    "seed_signals": ["name-phonetic", "address", "exact-identifier"]
  },
  "signals": [
    {
      "type": "name-phonetic",
      "weight": 0.22,
      "fields": ["legal_name", "dba_name"],
      "subfield": "phonetic",
      "cross_field": true
    },
    {
      "type": "name-phonetic",
      "weight": 0.13,
      "fields": ["legal_name", "dba_name"],
      "subfield": "phonetic_bm",
      "cross_field": true
    },
    {
      "type": "name-token",
      "weight": 0.1,
      "fields": ["legal_name", "dba_name"],
      "subfield": "clean",
      "cross_field": true
    },
    {
      "type": "address",
      "weight": 0.2,
      "fields": ["phy_street", "mailing_street"],
      "exact_subfield": "clean",
      "fuzzy_subfield": "tokens",
      "fuzzy_scale": 0.7
    },
    {
      "type": "exact-identifier",
      "weight": 0.12,
      "phone_fields": ["telephone", "fax"],
      "text_fields": ["email_address"]
    },
    {
      "type": "agent",
      "weight": 0.04,
      "name_field": "boc3_agents.co_name",
      "idf_weighted": true
    },
    {
      "type": "temporal",
      "weight": 0.05,
      "predecessor_date": "out_of_service_orders.oos_date",
      "successor_date": "add_date",
      "max_gap_days": 365
    },
    {
      "type": "vin-overlap",
      "weight": 0.08,
      "fields": [
        "crashes.vehicle_identification_number",
        "inspections.units.insp_unit_vehicle_id_number"
      ]
    }
  ],
  "scoring": {
    "min_total_score": 0.35,
    "min_signals": 2,
    "require_identity_signal": true,
    "max_pairs_per_predecessor": 10
  }
}
```

Weights sum to 0.94, which is correct — unevaluable signals drop out and the total renormalizes over what remains, so only ratios matter.

- [ ] **Step 4: Update the spec's config snippet to match**

In `docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md`, find the `exact-identifier` line in the Section 2 JSON block:

```json
    { "type": "exact-identifier", "weight": 0.12, "fields": ["telephone.clean", "email_address", "fax.clean"] },
```

Replace it with:

```json
    { "type": "exact-identifier", "weight": 0.12, "phone_fields": ["telephone", "fax"], "text_fields": ["email_address"] },
```

Then add this note directly beneath that JSON block:

```markdown
**Refined during planning:** `exact-identifier` takes `phone_fields` and
`text_fields` rather than one `fields` list. The signal reads raw `_source`
values rather than analyzed tokens, so it needs no `.clean` subfield paths, and
splitting the keys makes normalization explicit instead of inferred from field
names.
```

- [ ] **Step 5: Validate all three files parse and weights are sane**

Run:

```bash
python3 -c "
import json
for name in ['index-config', 'index-mappings', 'entity-match']:
    json.load(open('DOT-Commercial/configuration/chameleon-detection/{}.json'.format(name)))
c = json.load(open('DOT-Commercial/configuration/chameleon-detection/entity-match.json'))
print('signals:', len(c['signals']))
print('weight sum:', round(sum(s['weight'] for s in c['signals']), 4))
print('types:', sorted({s['type'] for s in c['signals']}))
"
```

Expected:

```
signals: 8
weight sum: 0.94
types: ['address', 'agent', 'exact-identifier', 'name-phonetic', 'name-token', 'temporal', 'vin-overlap']
```

- [ ] **Step 6: Commit**

```bash
git add DOT-Commercial/configuration/chameleon-detection/ docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md
git commit -m "feat: add chameleon-detection configuration

Eight signal instances over seven types; name-phonetic appears twice with
different subfields so the double-metaphone and Beider-Morse arms are
weighted independently.

agent is weighted 0.04 and excluded from seed_signals: BOC-3 has only 89
distinct agent names across 1.43M rows, so seeding candidate generation on it
would return 100 essentially random carriers per predecessor."
```

---

### Task 11: Predecessor selection and iteration

**Files:**

- Create: `matching/predecessors.py`

**Interfaces:**

- Consumes: `elasticsearch` client
- Produces: `PredecessorSelector(es, source_index, predecessors_config)` with `build_query() -> dict` and `iterate() -> Iterator[dict]` (yields raw hits with `_id` and `_source`), plus `SELECTORS: frozenset[str]`

- [ ] **Step 1: Implement `matching/predecessors.py`**

```python
"""Predecessor selection: which carriers count as 'shut down'.

Population sizes measured against the July 2026 FMCSA extracts:

    out-of-service        340,352
    revoked-authority   1,008,619
    both                  182,774
    either              1,166,197

revoked-authority covers roughly half of every carrier ever registered.
Involuntary revocation for lapsed insurance is routine and is not by itself
evidence of a chameleon, so out-of-service is the default.
"""

import logging

logger = logging.getLogger(__name__)

SELECTORS = frozenset({"out-of-service", "revoked-authority", "both", "either"})

# auth_history.original_action_desc == 'INVOLUNTARY REVOCATION' occurs 2,215,957
# times, but 2,208,586 dispositions are 'DISCONTINUED REVOCATION' — the
# revocation was reversed. Selecting on the filing would gather millions of
# carriers that were never shut down, so selectors key on the disposition.
REVOKED_DISPOSITION = "REVOKED"

PAGE_SIZE = 500


class PredecessorSelector:
    def __init__(self, es, source_index, config):
        self.es = es
        self.source_index = source_index
        self.selector = getattr(config, "selector", "out-of-service")
        if self.selector not in SELECTORS:
            raise ValueError(
                "unknown selector {!r}; known selectors are {}".format(
                    self.selector, ", ".join(sorted(SELECTORS))
                )
            )
        self.oos_status = list(getattr(config, "oos_status", []) or [])
        self.oos_date_from = getattr(config, "oos_date_from", None)
        self.max_predecessors = getattr(config, "max_predecessors", None)

    def _out_of_service_clause(self):
        must = [{"exists": {"field": "out_of_service_orders.oos_date"}}]
        if self.oos_status:
            must.append({"terms": {"out_of_service_orders.status": self.oos_status}})
        if self.oos_date_from:
            # oos_date is mapped as keyword, but ISO dates sort lexicographically
            # so a range query still behaves correctly.
            must.append(
                {"range": {"out_of_service_orders.oos_date": {"gte": self.oos_date_from}}}
            )
        return {"bool": {"must": must}}

    def _revoked_clause(self):
        return {
            "bool": {
                "must": [
                    {"term": {"auth_history.disp_action_desc": REVOKED_DISPOSITION}}
                ]
            }
        }

    def build_query(self):
        if self.selector == "out-of-service":
            return self._out_of_service_clause()
        if self.selector == "revoked-authority":
            return self._revoked_clause()
        if self.selector == "both":
            return {
                "bool": {"must": [self._out_of_service_clause(), self._revoked_clause()]}
            }
        return {
            "bool": {
                "should": [self._out_of_service_clause(), self._revoked_clause()],
                "minimum_should_match": 1,
            }
        }

    def iterate(self):
        """Yield predecessor hits using a point-in-time and search_after.

        A PIT gives a consistent snapshot across a sweep that may run for hours.
        from/size would break past 10,000 results.
        """
        pit = self.es.open_point_in_time(index=self.source_index, keep_alive="10m")
        pit_id = pit["id"]
        search_after = None
        yielded = 0

        try:
            while True:
                if self.max_predecessors is not None:
                    remaining = self.max_predecessors - yielded
                    if remaining <= 0:
                        return
                    page_size = min(PAGE_SIZE, remaining)
                else:
                    page_size = PAGE_SIZE

                body = {
                    "size": page_size,
                    "query": self.build_query(),
                    "pit": {"id": pit_id, "keep_alive": "10m"},
                    "sort": [{"dot_number": "asc"}],
                    "track_total_hits": False,
                }
                if search_after is not None:
                    body["search_after"] = search_after

                response = self.es.search(body=body)
                hits = response["hits"]["hits"]
                if not hits:
                    return

                for hit in hits:
                    yield hit
                    yielded += 1
                    if self.max_predecessors is not None and yielded >= self.max_predecessors:
                        return

                search_after = hits[-1]["sort"]
                pit_id = response.get("pit_id", pit_id)
        finally:
            try:
                self.es.close_point_in_time(body={"id": pit_id})
            except Exception as e:
                logger.warning("Failed to close point in time: {}".format(e))
```

- [ ] **Step 2: Verify the module imports and rejects bad selectors**

Run:

```bash
python3 -c "
from types import SimpleNamespace
from matching.predecessors import PredecessorSelector, SELECTORS
print('selectors:', sorted(SELECTORS))
s = PredecessorSelector(None, 'carriers-000001', SimpleNamespace(selector='out-of-service', oos_status=['ACTIVE'], oos_date_from='2020-01-01', max_predecessors=10))
import json; print(json.dumps(s.build_query()))
try:
    PredecessorSelector(None, 'x', SimpleNamespace(selector='nope'))
except ValueError as e:
    print('rejected:', e)
"
```

Expected: the four selectors listed, a `bool` query containing `exists`, `terms`, and `range` clauses, and a `rejected: unknown selector 'nope'` line.

- [ ] **Step 3: Commit**

```bash
git add matching/predecessors.py
git commit -m "feat: add predecessor selection with four population selectors

Selectors key on auth_history.disp_action_desc rather than
original_action_desc. 'INVOLUNTARY REVOCATION' appears 2,215,957 times as a
filing, but 2,208,586 dispositions are 'DISCONTINUED REVOCATION' — the
revocation was reversed. Selecting on the filing would gather millions of
carriers that were never shut down.

Iteration uses a point-in-time with search_after so a multi-hour sweep sees a
consistent snapshot and does not break past 10,000 results."
```

---

### Task 12: Candidate retrieval and term vectors

**Files:**

- Create: `matching/candidates.py`

**Interfaces:**

- Consumes: `matching.documents.CarrierDoc`
- Produces: `CandidateFinder(es, source_index, candidates_config, signal_configs)` with `find(pred_hit) -> tuple[CarrierDoc, list[CarrierDoc], bool]` returning `(predecessor_doc, candidate_docs, truncated)`, and `scored_subfields() -> set[str]`

- [ ] **Step 1: Implement `matching/candidates.py`**

```python
"""Candidate retrieval and analyzed-token fetching.

Two round trips per predecessor: one bool.should query to pull candidates, one
_mtermvectors call to fetch the analyzed tokens for the predecessor and every
candidate.

Tokens come from Elasticsearch rather than being recomputed in Python so that
scoring always sees exactly what the index sees — no risk of a local
double-metaphone implementation drifting from the plugin's.
"""

import logging

from matching.documents import CarrierDoc

logger = logging.getLogger(__name__)

# Signal types that can seed candidate generation, mapped to how their clauses
# are built. agent is deliberately absent: only 89 distinct BOC-3 agents cover
# 1.43M filings, so seeding on it returns essentially random carriers.
SEEDABLE = {"name-phonetic", "name-token", "address", "exact-identifier"}


class CandidateFinder:
    def __init__(self, es, source_index, candidates_config, signal_configs):
        self.es = es
        self.source_index = source_index
        self.max_candidates = int(getattr(candidates_config, "max_candidates", 100))
        self.seed_signals = set(getattr(candidates_config, "seed_signals", []) or [])
        self.signal_configs = list(signal_configs)

    def scored_subfields(self) -> set[str]:
        """Every "field.subfield" the configured signals read tokens from."""
        wanted = set()
        for config in self.signal_configs:
            if config.type in ("name-phonetic", "name-token"):
                for field_name in config.fields:
                    wanted.add("{}.{}".format(field_name, config.subfield))
            elif config.type == "address":
                for field_name in config.fields:
                    wanted.add("{}.{}".format(field_name, config.exact_subfield))
                    wanted.add("{}.{}".format(field_name, config.fuzzy_subfield))
        return wanted

    def _seed_clauses(self, source):
        clauses = []
        for config in self.signal_configs:
            if config.type not in self.seed_signals or config.type not in SEEDABLE:
                continue

            if config.type in ("name-phonetic", "name-token"):
                for field_name in config.fields:
                    text = source.get(field_name)
                    if text:
                        clauses.append(
                            {
                                "match": {
                                    "{}.{}".format(field_name, config.subfield): {
                                        "query": text
                                    }
                                }
                            }
                        )
            elif config.type == "address":
                for field_name in config.fields:
                    text = source.get(field_name)
                    if text:
                        clauses.append(
                            {
                                "match": {
                                    "{}.{}".format(field_name, config.exact_subfield): {
                                        "query": text
                                    }
                                }
                            }
                        )
            elif config.type == "exact-identifier":
                for field_name in getattr(config, "phone_fields", []):
                    value = source.get(field_name)
                    if value:
                        clauses.append(
                            {"match": {"{}.clean".format(field_name): {"query": value}}}
                        )
                for field_name in getattr(config, "text_fields", []):
                    value = source.get(field_name)
                    if value:
                        clauses.append(
                            {"term": {"{}.keyword".format(field_name): value}}
                        )
        return clauses

    def find(self, pred_hit):
        pred_id = pred_hit["_id"]
        pred_source = pred_hit["_source"]

        clauses = self._seed_clauses(pred_source)
        if not clauses:
            logger.debug("No seed clauses for predecessor %s", pred_id)
            return None, [], False

        response = self.es.search(
            index=self.source_index,
            body={
                "size": self.max_candidates,
                "query": {
                    "bool": {
                        "should": clauses,
                        "minimum_should_match": 1,
                        "must_not": [{"ids": {"values": [pred_id]}}],
                    }
                },
                "track_total_hits": False,
            },
        )
        hits = response["hits"]["hits"]
        truncated = len(hits) >= self.max_candidates

        all_ids = [pred_id] + [h["_id"] for h in hits]
        tokens_by_id = self._fetch_tokens(all_ids)

        pred_doc = _to_carrier_doc(pred_hit, tokens_by_id.get(pred_id, {}))
        cand_docs = [_to_carrier_doc(h, tokens_by_id.get(h["_id"], {})) for h in hits]
        return pred_doc, cand_docs, truncated

    def _fetch_tokens(self, doc_ids):
        fields = sorted(self.scored_subfields())
        if not fields or not doc_ids:
            return {}

        response = self.es.mtermvectors(
            index=self.source_index,
            body={
                "ids": doc_ids,
                "parameters": {
                    "fields": fields,
                    "term_statistics": False,
                    "field_statistics": False,
                    "positions": False,
                    "offsets": False,
                    "payloads": False,
                },
            },
        )

        tokens_by_id = {}
        for doc in response.get("docs", []):
            doc_tokens = {}
            for field_name, field_data in (doc.get("term_vectors") or {}).items():
                doc_tokens[field_name] = set((field_data.get("terms") or {}).keys())
            tokens_by_id[doc["_id"]] = doc_tokens
        return tokens_by_id


def _to_carrier_doc(hit, tokens):
    source = hit["_source"]
    return CarrierDoc(
        dot_number=str(source.get("dot_number", hit["_id"])),
        source=source,
        tokens=tokens,
    )
```

- [ ] **Step 2: Verify seed clause construction without a cluster**

Run:

```bash
python3 -c "
import json
from types import SimpleNamespace as NS
from matching.candidates import CandidateFinder

signals = [
    NS(type='name-phonetic', weight=0.22, fields=['legal_name','dba_name'], subfield='phonetic'),
    NS(type='address', weight=0.2, fields=['phy_street'], exact_subfield='clean', fuzzy_subfield='tokens', fuzzy_scale=0.7),
    NS(type='exact-identifier', weight=0.12, phone_fields=['telephone'], text_fields=['email_address']),
    NS(type='agent', weight=0.04, name_field='boc3_agents.co_name'),
]
cands = NS(max_candidates=100, seed_signals=['name-phonetic','address','exact-identifier'])
f = CandidateFinder(None, 'carriers-000001', cands, signals)
print('subfields:', sorted(f.scored_subfields()))
src = {'legal_name':'SMITH TRUCKING LLC','dba_name':'','phy_street':'123 MAIN ST','telephone':'(503) 289-5558','email_address':'a@b.com'}
print('clauses:', json.dumps(f._seed_clauses(src), indent=None))
"
```

Expected: `subfields` lists `dba_name.phonetic`, `legal_name.phonetic`, `phy_street.clean`, `phy_street.tokens`. Clauses include a `match` on `legal_name.phonetic`, a `match` on `phy_street.clean`, a `match` on `telephone.clean`, and a `term` on `email_address.keyword`. No `boc3_agents` clause — `agent` is not seedable.

- [ ] **Step 3: Commit**

```bash
git add matching/candidates.py
git commit -m "feat: add candidate retrieval and term vector fetching

Two round trips per predecessor: a bool.should query for candidates, then one
_mtermvectors call for the predecessor and all candidates.

Tokens come from Elasticsearch rather than a local reimplementation of
double-metaphone or Beider-Morse, so scoring always sees exactly what the
index sees. Term vectors are generated on the fly, so no term_vector setting
is needed in the mapping and the Beider-Morse subfield does not bloat the
index."
```

---

### Task 13: The `entity-match` phase and wiring

**Files:**

- Create: `phase_providers/phase_entity_match.py`
- Modify: `phase_providers/phase_dispatcher.py`, `DOT-Commercial/configuration.json`, `DOT-Commercial/README.md`, `README.md`

**Interfaces:**

- Consumes: everything from Tasks 1-12
- Produces: `PhaseEntityMatch(es, project, one_step, project_config)` with `handle()`

- [ ] **Step 1: Implement `phase_providers/phase_entity_match.py`**

```python
"""entity-match phase: sweep shut-down carriers for likely successors.

The theme of the error handling here is converting silent wrong output into
loud failure. Every bug documented in this repo's README is of that shape: a
phase logs acknowledged/True, nothing errors, and the output is quietly wrong.
"""

import datetime
import logging
import uuid

from elasticsearch.helpers import parallel_bulk

import utils.elasticsearch_utils as elasticsearch_utils
import utils.file_utils as file_utils
import utils.id_utils as id_utils
from matching.candidates import CandidateFinder
from matching.documents import ScoringContext
from matching.predecessors import PredecessorSelector
from matching.scorer import PairScorer

AGENT_TERMS_SIZE = 500
BULK_THREAD_COUNT = 2


class PhaseEntityMatch:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )

        config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "entity-match.json",
        )
        if not config:
            self.logger.error("No entity-match.json for step {}".format(self.one_step))
            return

        index_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        if not index_config:
            self.logger.error("No index-config.json for step {}".format(self.one_step))
            return
        elasticsearch_utils.replace_index_with_now_version(index_config)

        source_index = config.source_index
        scorer = PairScorer(config.signals, config.scoring)
        finder = CandidateFinder(
            self.es, source_index, config.candidates, config.signals
        )
        selector = PredecessorSelector(self.es, source_index, config.predecessors)

        if not self._preflight(source_index, finder.scored_subfields()):
            return

        ctx = self._build_context(source_index, config.signals)
        max_pairs = int(getattr(config.scoring, "max_pairs_per_predecessor", 10))
        run_id = uuid.uuid4().hex
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        stats = {
            "predecessors": 0,
            "candidates": 0,
            "pairs": 0,
            "truncated": 0,
            "errors": 0,
        }

        actions = self._generate_actions(
            selector, finder, scorer, ctx, index_config.index, max_pairs,
            run_id, generated_at, stats,
        )

        indexed = 0
        for success, response in parallel_bulk(
            client=self.es,
            thread_count=BULK_THREAD_COUNT,
            actions=actions,
            raise_on_error=False,
            raise_on_exception=False,
        ):
            if success:
                indexed += 1
            else:
                stats["errors"] += 1
                self.logger.error("Failed to index pair: {}".format(response))

        self.logger.info(
            "entity-match complete: {} predecessors, {} candidates examined, "
            "{} pairs emitted, {} indexed, {} truncated candidate sets, {} errors".format(
                stats["predecessors"], stats["candidates"], stats["pairs"],
                indexed, stats["truncated"], stats["errors"],
            )
        )
        if indexed == 0:
            self.logger.warning(
                "entity-match produced NO pairs. Check that {} is populated and "
                "that min_total_score ({}) is not set too high.".format(
                    source_index, scorer.min_total_score
                )
            )
        if stats["truncated"]:
            self.logger.warning(
                "{} predecessors hit the max_candidates ceiling; real matches "
                "may have been cut off".format(stats["truncated"])
            )

    def _preflight(self, source_index, required_subfields):
        """Fail loudly before sweeping rather than emitting a silently empty result.

        Running against an older carriers index that lacks .phonetic_bm would
        make _mtermvectors return nothing for that field, turn every phonetic
        score into None, and produce an empty result set with no error anywhere.
        """
        try:
            self.es.indices.refresh(index=source_index)
        except Exception as e:
            self.logger.error("Cannot refresh source index {}: {}".format(source_index, e))
            return False

        count = self.es.count(index=source_index)["count"]
        if count == 0:
            self.logger.error("Source index {} is empty; nothing to sweep".format(source_index))
            return False
        self.logger.info("Sweeping against {} ({} documents)".format(source_index, count))

        mapping = self.es.indices.get_mapping(index=source_index)
        properties = {}
        for index_mapping in mapping.body.values():
            properties = index_mapping.get("mappings", {}).get("properties", {})
            break

        missing = []
        for subfield_path in sorted(required_subfields):
            field_name, _, subfield = subfield_path.partition(".")
            field_def = properties.get(field_name, {})
            if subfield not in (field_def.get("fields") or {}):
                missing.append(subfield_path)

        if missing:
            self.logger.error(
                "Source index {} is missing scored subfields: {}. Recreate and "
                "reload the carriers index with the updated index-settings.json "
                "and index-mappings.json.".format(source_index, ", ".join(missing))
            )
            return False
        return True

    def _build_context(self, source_index, signal_configs):
        """Gather BOC-3 agent frequencies once for IDF weighting."""
        agent_config = next((c for c in signal_configs if c.type == "agent"), None)
        if agent_config is None:
            return ScoringContext()

        keyword_field = "{}.keyword".format(agent_config.name_field)
        try:
            response = self.es.search(
                index=source_index,
                body={
                    "size": 0,
                    "aggs": {
                        "agents": {"terms": {"field": keyword_field, "size": AGENT_TERMS_SIZE}}
                    },
                },
            )
        except Exception as e:
            self.logger.warning(
                "Could not gather agent frequencies ({}); agent signal will treat "
                "every agent as maximally rare".format(e)
            )
            return ScoringContext()

        buckets = response["aggregations"]["agents"]["buckets"]
        counts = {b["key"].strip().lower(): b["doc_count"] for b in buckets}
        total = sum(counts.values())
        self.logger.info(
            "Loaded {} distinct BOC-3 agents covering {} carrier filings".format(
                len(counts), total
            )
        )
        return ScoringContext(agent_counts=counts, total_agent_carriers=total)

    def _generate_actions(
        self, selector, finder, scorer, ctx, target_index, max_pairs,
        run_id, generated_at, stats,
    ):
        seen_pairs = set()

        for pred_hit in selector.iterate():
            stats["predecessors"] += 1
            try:
                pred_doc, cand_docs, truncated = finder.find(pred_hit)
            except Exception as e:
                stats["errors"] += 1
                self.logger.error(
                    "Candidate lookup failed for {}: {}".format(pred_hit["_id"], e)
                )
                continue

            if pred_doc is None:
                continue
            stats["candidates"] += len(cand_docs)
            if truncated:
                stats["truncated"] += 1

            scored = []
            for cand_doc in cand_docs:
                try:
                    pair = scorer.score_pair(pred_doc, cand_doc, ctx)
                except Exception as e:
                    stats["errors"] += 1
                    self.logger.error(
                        "Scoring failed for {} -> {}: {}".format(
                            pred_doc.dot_number, cand_doc.dot_number, e
                        )
                    )
                    continue
                if pair is not None:
                    scored.append(pair)

            scored.sort(key=lambda p: p.total_score, reverse=True)
            for pair in scored[:max_pairs]:
                key = (pair.predecessor.dot_number, pair.successor.dot_number)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                stats["pairs"] += 1
                yield self._to_action(pair, target_index, run_id, generated_at)

    def _to_action(self, pair, target_index, run_id, generated_at):
        from matching.signals import parse_flexible_date

        pred = pair.predecessor
        succ = pair.successor

        shutdown = _latest_iso(pred.value("out_of_service_orders.oos_date"))
        registered = _latest_iso(succ.value("add_date"))
        gap_days = None
        if shutdown and registered:
            gap_days = (
                parse_flexible_date(registered) - parse_flexible_date(shutdown)
            ).days

        document = {
            "predecessor": _carrier_summary(pred, shutdown_date=shutdown),
            "successor": _carrier_summary(succ, add_date=registered),
            "total_score": round(pair.total_score, 6),
            "gap_days": gap_days,
            "signals_present": pair.signals_present,
            "matched_on": pair.matched_on,
            "signals": [
                {
                    "signal_type": c.signal_type,
                    "subfield": c.subfield,
                    "weight": c.weight,
                    "score": round(c.score, 6),
                    "contribution": round(c.contribution, 6),
                }
                for c in pair.signals
            ],
            "run_id": run_id,
            "generated_at": generated_at,
        }
        return {
            "_index": target_index,
            "_id": id_utils.compute_id(
                {"p": pred.dot_number, "s": succ.dot_number}, ["p", "s"]
            ),
            "_source": document,
        }


def _carrier_summary(doc, shutdown_date=None, add_date=None):
    summary = {
        "dot_number": doc.dot_number,
        "legal_name": doc.value("legal_name"),
        "dba_name": doc.value("dba_name"),
        "phy_street": doc.value("phy_street"),
        "phy_city": doc.value("phy_city"),
        "phy_state": doc.value("phy_state"),
    }
    if shutdown_date is not None:
        summary["shutdown_date"] = shutdown_date
        reason = doc.value("out_of_service_orders.oos_reason")
        summary["shutdown_reason"] = reason[0] if isinstance(reason, list) else reason
    if add_date is not None:
        summary["add_date"] = add_date
    return summary


def _latest_iso(raw):
    from matching.signals import parse_flexible_date

    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    dates = [d for d in (parse_flexible_date(i) for i in items) if d is not None]
    return max(dates).isoformat() if dates else None
```

- [ ] **Step 2: Wire the dispatcher and fix its broken else branch**

In `phase_providers/phase_dispatcher.py`, add to the imports:

```python
from phase_providers.phase_entity_match import PhaseEntityMatch
```

Add this branch before the `else`:

```python
        elif one_phase == "entity-match":
            handler = PhaseEntityMatch(es, project, step_name, project_config)
            handler.handle()
```

Replace the `else` body (line 35). It currently calls an undefined `logger` and a nonexistent `step_name.phase`, so an unrecognized phase raises `NameError` instead of logging:

```python
        else:
            self.logger.error(
                "Unrecognized phase: {} in step {}".format(one_phase, step_name)
            )
```

- [ ] **Step 3: Register the step in the project configuration**

In `DOT-Commercial/configuration.json`, append to `steps`:

```json
  {
    "name": "chameleon-detection",
    "phases": ["index-create", "index-map", "entity-match"]
  }
```

and append `"entity-match"` to `all_phases`.

- [ ] **Step 4: Verify wiring without a cluster**

Run:

```bash
python3 -c "
import json
from phase_providers.phase_dispatcher import PhaseDispatcher
c = json.load(open('DOT-Commercial/configuration.json'))
print('entity-match in all_phases:', 'entity-match' in c['all_phases'])
print('step present:', any(s['name'] == 'chameleon-detection' for s in c['steps']))
d = PhaseDispatcher()
d.process_phase_step(None, 'DOT-Commercial', 'x', 'no-such-phase', None)
print('unrecognized phase logged without raising')
"
```

Expected: both `True`, an ERROR log line, and the final print — no `NameError`.

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — the whole suite green, no failures or errors

- [ ] **Step 6: Run the sweep end to end**

Requires the `carriers` index rebuilt with Tasks 8-9 in place:

```bash
python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
python3 execute_project.py --project=DOT-Commercial --step=carriers
python3 execute_project.py --project=DOT-Commercial --step=chameleon-detection
```

Expected: the run summary logs non-zero predecessors and pairs. Then inspect the top hits:

```bash
curl -sk -u "$ES_USER:$ES_PASS" "$ES_URL/chameleon-candidates-000001/_search" \
  -H 'Content-Type: application/json' \
  -d '{"size":5,"sort":[{"total_score":"desc"}],"_source":["total_score","matched_on","gap_days","predecessor.legal_name","successor.legal_name"]}'
```

Expected: pairs with recognizably similar names and a populated `matched_on`.

- [ ] **Step 7: Update the documentation**

In `DOT-Commercial/README.md`:

1. Add `chameleon-detection` as step 11 in the "Processing Steps" list:

```markdown
1. `chameleon-detection` - sweep shut-down carriers for likely successors and write ranked suspect pairs to `chameleon-candidates`
```

2. Correct the BOC-3 row in the datasets table. Replace its "Purpose" cell with:

```markdown
Each carrier's legal process agent (name + address). **Weak signal:** only 89 distinct agents cover all 1.43M filings, so two unrelated carriers share an agent roughly 7% of the time by chance. Used only as IDF-weighted corroboration at weight 0.04.
```

3. Add a note beneath the datasets table:

```markdown
The earlier claim that a shared BOC-3 agent is "a harder signal to fake than a business address" did not survive measurement — the dataset carries no per-carrier information, only which of ~89 commercial filing companies a carrier paid. See the chameleon carrier matching design spec.
```

In the top-level `README.md`, add `entity-match` to the phase list under "Processing Steps are made up of Phases":

```markdown
1. `entity-match` - score pairs of related entities and write ranked candidates to an output index
```

- [ ] **Step 8: Commit**

```bash
git add phase_providers/phase_entity_match.py phase_providers/phase_dispatcher.py DOT-Commercial/configuration.json DOT-Commercial/README.md README.md
git commit -m "feat: add entity-match phase and wire chameleon-detection step

Sweeps shut-down carriers, scores candidate successors, and writes ranked
pairs with per-signal evidence to chameleon-candidates.

Preflight fails loudly when the source index is empty or missing a scored
subfield. Without it, running against an older carriers index lacking
.phonetic_bm would make _mtermvectors return nothing, turn every phonetic
score into None, and emit a silently empty result set with no error anywhere
— the same failure shape as the enrich bugs in the README.

Also fixes phase_dispatcher's else branch, which called an undefined logger
and a nonexistent step_name.phase, raising NameError instead of logging when
a phase was unrecognized.

Corrects the DOT-Commercial README's BOC-3 claim: 89 distinct agents cover
1.43M filings, so a shared agent is weak corroboration, not a hard signal."
```

---

### Task 14: Carry inspection VINs through to carriers

**Files:**

- Modify: `DOT-Commercial/configuration/carriers-ingestion-setup/enrichment-policies.json`

**Interfaces:**

- Consumes: Task 10's `vin-overlap` config, which already lists
  `inspections.units.insp_unit_vehicle_id_number`
- Produces: `inspections.units.insp_unit_vehicle_id_number` present on carrier documents

**Why this task exists.** The two-level enrichment chain already assembles VIN
data — `inspections-per-unit` is enriched onto `inspections` under a `units`
target field by `inspections-pipeline-000001` — and then the last hop throws it
away. `inspections-enrichment-policy` carries only `dot_number` and
`inspection_id` into carriers. Today `vin-overlap` can only see the 333K crash
records; this makes the 5.6M-row inspection VIN signal reachable.

**Scale, measured.** Joining the two local extracts gives 4,976,529 distinct
carrier→VIN links across 569,118 carriers. VINs per carrier run p50=2, p90=12,
p95=23, p99=95, and **99.06% of carriers have 100 or fewer**. The 5,341
carriers above the cap are megafleets that do not reincarnate under new DOT
numbers, so `max_matches` truncation lands on the population this analysis does
not care about.

**Do not raise `max_matches` to compensate.** Elasticsearch caps it at 128
("In order to avoid documents getting too large, the maximum allowed value is
128"), which would still discard 25.2% of links. The cap is not the lever.

- [ ] **Step 1: Add the VIN path to the enrichment policy**

In `DOT-Commercial/configuration/carriers-ingestion-setup/enrichment-policies.json`,
change the `inspections-enrichment-policy` entry's `enrich_fields` from:

```json
   "enrich_fields": [
    "dot_number",
    "inspection_id"
   ]
```

to:

```json
   "enrich_fields": [
    "dot_number",
    "inspection_id",
    "units.insp_unit_vehicle_id_number"
   ]
```

Only the VIN subfield is carried, not the whole `units` object. Each carrier
holds up to `max_matches: 100` inspections, each with up to 10 units, so
pulling `insp_unit_make` / `insp_unit_license` / the rest would multiply carrier
document size for data no signal reads.

- [ ] **Step 2: Verify the config parses and the path is correct**

Run:

```bash
python3 -c "
import json
p = json.load(open('DOT-Commercial/configuration/carriers-ingestion-setup/enrichment-policies.json'))
pol = next(x for x in p if x['name'] == 'inspections-enrichment-policy')
print('enrich_fields:', pol['enrich_fields'])
assert 'units.insp_unit_vehicle_id_number' in pol['enrich_fields']
i = json.load(open('DOT-Commercial/configuration/inspections-ingestion-setup/pipelines.json'))
print('inspections target_field:', i['processors'][0]['enrich']['target_field'])
assert i['processors'][0]['enrich']['target_field'] == 'units'
print('OK: carrier path is inspections.units.insp_unit_vehicle_id_number')
"
```

Expected: the three lines print and both asserts pass. The `target_field` check
matters — if `inspections-pipeline-000001` ever renames it, the enrich path and
the `vin-overlap` config in `entity-match.json` must change together.

- [ ] **Step 3: Rebuild the policy, working around the documented delete conflict**

`README.md` documents this trap at length: an enrich policy bound to a live
pipeline **cannot be deleted**, and `phase_enrichment_policies.py:44-49` catches
that `ConflictError` and only logs a warning. The rebuild silently no-ops and
the policy stays pinned to its old snapshot — which is exactly how
`inspections-enrichment-policy` once stayed stuck on a 5,000-row sample across a
full production run. Delete the pipeline first:

```bash
curl -sk -u "$ES_USER:$ES_PASS" -XDELETE \
  "$ES_URL/_ingest/pipeline/carrier-enrichment-pipeline-000001"
```

Then rebuild the policy and pipeline together:

```bash
python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
```

Watch the log for `Failed to delete enrichment policy due to conflict`. If it
appears, the pipeline delete did not take and the policy is stale — stop and
retry rather than continuing.

- [ ] **Step 4: Confirm the enrich index actually contains VINs**

```bash
curl -sk -u "$ES_USER:$ES_PASS" \
  "$ES_URL/.enrich-inspections-enrichment-policy*/_search?size=1&pretty"
```

Expected: a hit whose `_source` contains a `units` array with
`insp_unit_vehicle_id_number` populated. If `units` is absent, the `inspections`
index itself lacks the data and `--step=inspections` must be rerun first.

- [ ] **Step 5: Reload carriers and verify VINs landed**

```bash
python3 execute_project.py --project=DOT-Commercial --step=carriers
```

Then confirm on a real document:

```bash
curl -sk -u "$ES_USER:$ES_PASS" "$ES_URL/carriers-000001/_search" \
  -H 'Content-Type: application/json' \
  -d '{"size":1,"query":{"exists":{"field":"inspections.units.insp_unit_vehicle_id_number"}},"_source":["dot_number","inspections.units.insp_unit_vehicle_id_number"]}'
```

Expected: a carrier with a populated nested VIN array. A zero-hit result means
the enrich field did not propagate — recheck Step 3's conflict warning.

- [ ] **Step 6: Commit**

```bash
git add DOT-Commercial/configuration/carriers-ingestion-setup/enrichment-policies.json
git commit -m "feat: carry inspection VINs through to carrier documents

The two-level chain already assembles this data — inspections-per-unit is
enriched onto inspections under a 'units' target field — and then the last
hop discarded it, carrying only dot_number and inspection_id into carriers.
vin-overlap could therefore only see the 333K crash records rather than the
5.6M-row inspection signal.

Only the VIN subfield is carried, not the whole units object: each carrier
holds up to 100 inspections of up to 10 units each, so pulling make/license
would multiply document size for data no signal reads.

max_matches is deliberately left at 100. Elasticsearch caps it at 128, and
measurement shows the cap is not the lever: 99.06% of the 569,118 carriers
with VINs have 100 or fewer (p50=2, p90=12, p99=95). The 5,341 carriers above
it are megafleets that do not reincarnate under new DOT numbers."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| §1 phonetic filters (double_metaphone, beider_morse) | 8 |
| §1 corporate-suffix stop filter | 8 |
| §1 street analyzer fix (delete `street_suffix_map`, add `street_tokens`) | 8 |
| §1 mapping additions | 9 |
| §1 `add_date` century fix | 9 |
| §2 `entity-match` phase and dispatcher branch | 13 |
| §2 config schema | 10 |
| §2 three scoring guards | 6 |
| §3 `_mtermvectors` token retrieval | 12 |
| §3 per-signal math | 3, 4, 5 |
| §3 containment vs jaccard | 1 |
| §3 placeholder identifiers | 1, 4 |
| §3 cross-state address penalty | 4 |
| §3 BOC-3 IDF weighting | 2, 5, 13 |
| §3 asymmetric temporal | 5 |
| §4 four selectors, disposition trap | 11 |
| §4 PIT + `search_after` | 11 |
| §4 candidate query | 12 |
| §4 directed pairs and dedupe | 13 |
| §4 truncation warning | 13 |
| §5 output document and `_id` | 10, 13 |
| §5 `compute_id` refactor | 7 |
| §5 error handling (5 items) | 13 |
| §5 unit tests | 1-6 |
| Follow-on: correct README BOC-3 claim | 13 |
| Follow-on: inspection VINs reachable from carriers | 14 |

**Deliberately not implemented** (spec's "follow-on work"): filtering `dot_number = 00000000` from BOC-3 ingestion, and re-typing the shadow datasets' date fields. Both are listed in the spec as out of scope.

**Promoted from follow-on into Task 14:** carrying `insp_unit_vehicle_id_number` through to carriers. Without it `vin-overlap` only ever sees the 333K crash records, so the signal is nearly dead on arrival. Task 14 also forced a correctness fix in Task 2 — `CarrierDoc.value()` originally could not walk `inspections[].units[].insp_unit_vehicle_id_number`, because collecting across a list produced a list of lists and the final step then found no dicts and returned `None`. `_flatten` fixes it, with a test.

**Placeholder scan:** none found. Every code step contains complete, runnable content.

**Type consistency check:**

- `blended_overlap` returns `float | None` (Task 1); `NameOverlapSignal.score` propagates that contract (Task 3). ✓
- `_collect(target, raw, normalize)` defined in Task 4, reused in Task 5. ✓
- `CarrierDoc.value` returns a list when the path crosses a list (Task 2); `_latest_date` (Task 5) and `_collect` (Task 4) both handle scalar-or-list. ✓
- `Signal.signal_type` set in Task 3, consumed by `PairScorer` (Task 6) and the output document (Task 13). ✓
- `CENTURY_PIVOT = 30` in Python (Task 5) matches `year > 30` in the Painless script (Task 9). ✓
- `parse_flexible_date` (Task 5) is imported by `phase_entity_match` (Task 13). ✓
- `CandidateFinder.scored_subfields()` (Task 12) feeds `_preflight`'s mapping check (Task 13). ✓
- `exact-identifier` uses `phone_fields`/`text_fields` consistently in Task 4 (implementation), Task 10 (config), and Task 12 (seed clauses). ✓

---

## Run Order

Tasks 1-7 are pure Python and need no cluster. Tasks 8, 9, and 14 all change
what a carrier document contains, so they share one rebuild.

**Do the config edits for Tasks 8, 9, and 14 first, then run the reload once:**

```bash
# delete the pipeline BEFORE rebuilding policies, or the policy delete hits a
# ConflictError, is swallowed as a warning, and silently keeps its old snapshot
curl -sk -u "$ES_USER:$ES_PASS" -XDELETE \
  "$ES_URL/_ingest/pipeline/carrier-enrichment-pipeline-000001"

python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
python3 execute_project.py --project=DOT-Commercial --step=carriers   # ~2M docs, slow

# after Task 13
python3 execute_project.py --project=DOT-Commercial --step=chameleon-detection
```

The carriers reload is the expensive step. Run it once, after Tasks 8, 9, and
14 are all committed — not between them.

If `inspections` itself turns out to lack the `units` data (Task 14 Step 4
checks this), rerun `--step=inspections` before the carriers reload.
