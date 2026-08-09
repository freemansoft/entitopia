# Chameleon Precision Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the precision of the chameleon sweep — currently ~5% estimated true-positive share, with only 34.5% of the ≥ 0.70 tier temporally coherent — through three ordered changes, each one measured against a frozen baseline on held-constant data and revertible by an atomic alias repoint.

**Architecture:** The failure mode this plan is built to prevent is the one the repo keeps hitting: a phase logs success and produces quietly wrong output. So no change ships on argument. Task 1 builds a comparison harness that turns a sweep into a fixed set of counted metrics and diffs two sweeps against declared expectations. Tasks 2-4 each make exactly one change, re-sweep, and diff. The carriers reindex in Task 2 copies `_source` from the existing index rather than reloading from CSV, so the mapping is the only variable and the comparison is a controlled experiment rather than two different datasets. Task 5 is the one genuine data change and is deliberately last, outside the A/B chain, because it re-baselines everything before it.

**Tech Stack:** Python 3 in `.venv`, elasticsearch-py 9.4.1 against local Elasticsearch 9.4.1, pytest, ruff.

## Global Constraints

- Every command runs from `.venv`: `.venv/bin/python`, never bare `python3` or `pip3`.
- `.venv/bin/python -m ruff check .` must print `All checks passed!` before any commit. Exemptions need a written reason at the narrowest scope.
- Elasticsearch calls pass explicit keyword arguments. Never `body=`.
- Config objects load through `file_utils.load_from_file` as `SimpleNamespace`. Use attribute access and `getattr(obj, "key", default)` for optional keys.
- Comments and docstrings state why something exists and what breaks if it changes. Never narrate the steps the code takes.
- **Never name a flagged entity** in code, comments, config, docs, commit messages, or PR text — no company names, DOT numbers, addresses, phones, or emails belonging to matched records. Aggregate counts are fine. Baseline artifacts that must carry DOT numbers go under `DOT-Commercial/data/`, which `.gitignore` covers via `*/data/`.
- **Cluster writes are confirmed with the user before running.** Creating, deleting, or reindexing against the loaded cluster destroys hours of work. Reads need no confirmation.
- **Record each task's result index name when its sweep finishes.** Sweeps write to `chameleon-candidates-<run date>-000001`, so the next task's baseline is whatever date the previous sweep ran — which is not knowable in advance and is not necessarily today. Write it into `DOT-Commercial/data/precision/result-indexes.txt` as `task-N <index name>` at the end of each sweep step, and read it back with `grep "^task-N " DOT-Commercial/data/precision/result-indexes.txt`. Never let a comparison resolve the `chameleon-candidates-000001` alias: during an experiment the alias is exactly the thing in motion, so it would compare a run against itself and report all-zero deltas that look like a clean pass.
- **Do not prune superseded indexes during this plan.** Open item 5 in `DOT-Commercial/README.md` calls the accumulated date-stamped indexes an unowned cost; for the duration of this work they are the rollback path. `carriers-2026.08.06-000001` and `chameleon-candidates-2026.08.06-000001` must survive until Task 6.

## Baseline state, measured 2026-08-08 against the live cluster

These are preconditions, not history. Verify them before Task 1 and stop if they disagree.

| Alias                         | Index                                    | Docs      |
| ----------------------------- | ---------------------------------------- | --------- |
| `carriers-000001`             | `carriers-2026.08.06-000001`             | 2,085,534 |
| `chameleon-candidates-000001` | `chameleon-candidates-2026.08.06-000001` | 421,846   |
| `crashes-000001`              | `crashes-2026.08.08-000001`              | 333,120   |
| `chameleon-validation-000001` | `chameleon-validation-2026.08.07-000001` | 148       |

Cluster disk available: 73.3gb. The Task 2 reindex needs ~2gb.

## Two hazards that make a rerun silently wrong

Both are verified in the code, and both are why the protocol steps below look fussier than "just run it again."

1. **A same-day re-sweep merges into the previous sweep's index.** `phase_index_creation.py:97` catches the `BadRequestError` from creating an index that already exists and logs it as a _warning_, then attaches the alias and lets the sweep proceed. Pair `_id` is deterministic (`phase_entity_match.py:545`, predecessor+successor DOT), so a second run on the same calendar day overwrites the pairs it re-emits and **leaves every pair the new config no longer emits sitting in the index**. The result looks like a complete sweep and is a union of two configs. Every sweep step below therefore begins by asserting today's index does not already exist.
2. **A metric that moves for two reasons at once is not evidence.** Task 2 changes a mapping _and_ would change the underlying documents if it reloaded from CSV. It reindexes instead, so `_source` is byte-identical and the mapping is the only variable.

---

## File Structure

**Created:**

- `utils/sweep_compare.py` — pure functions: turn a pair population into a fixed metric record, and diff two records against declared expectations. No Elasticsearch import, so the arithmetic that decides what a number _means_ is testable without a cluster. Mirrors how `utils/crash_lift.py` is already split from `scripts/measure_crash_lift.py`.
- `tests/test_sweep_compare.py` — tests for the above.
- `scripts/compare_sweeps.py` — the Elasticsearch-reading CLI. Scans two pair indexes, summarizes both, prints the diff, exits non-zero on a declared regression.
- `tests/test_predecessors.py` — first tests for `matching/predecessors.py`, which currently has none.

**Modified:**

- `matching/predecessors.py` — nested out-of-service clause (Task 2); temporal coherence predicate (Task 3).
- `DOT-Commercial/configuration/carriers/index-mappings.json:142` — `out_of_service_orders` becomes `nested` (Task 2).
- `DOT-Commercial/configuration/chameleon-detection/entity-match.json` — `predecessors.max_successor_gap_days` (Task 3); signal weights (Task 4).
- `DOT-Commercial/configuration/inspections/index-mappings.json` — pin `insp_carrier_state_id` (Task 5).
- `DOT-Commercial/README.md` — open items updated as each closes (Tasks 2-5).

---

### Task 1: Sweep comparison harness

**Files:**

- Create: `utils/sweep_compare.py`
- Create: `tests/test_sweep_compare.py`
- Create: `scripts/compare_sweeps.py`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `utils.sweep_compare.summarize(pairs: Iterable[dict]) -> dict[str, int | float]`, `utils.sweep_compare.compare(baseline: dict, candidate: dict, expectations: dict[str, str]) -> list[MetricDelta]`, `utils.sweep_compare.METRICS: tuple[str, ...]`, and the dataclass `MetricDelta(name, baseline, candidate, delta, pct, expectation, ok)`. Tasks 2-4 call `scripts/compare_sweeps.py` and read its exit code.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sweep_compare.py`:

```python
"""Metric arithmetic for comparing two sweeps.

Exists so the part that decides whether a re-sweep got better or worse is
testable without a cluster. A banding or sign error here would otherwise only
ever surface as a comparison table that looks reasonable and licenses a bad
change, which is the exact failure this harness is meant to catch.
"""

import pytest

from utils.sweep_compare import METRICS, compare, summarize


def pair(score=0.8, gap=10, matched_on=None, pred="1", succ="2",
         pred_name="ALPHA", succ_name="BETA"):
    return {
        "total_score": score,
        "gap_days": gap,
        "matched_on": matched_on if matched_on is not None else ["name-phonetic"],
        "predecessor": {"dot_number": pred, "legal_name": pred_name},
        "successor": {"dot_number": succ, "legal_name": succ_name},
    }


def test_summarize_reports_every_declared_metric():
    result = summarize([pair()])
    assert set(result) == set(METRICS)


def test_pairs_counts_every_row():
    assert summarize([pair(), pair(succ="3")])["pairs"] == 2


def test_coherent_window_is_inclusive_at_both_edges():
    # -180 is BACKWARD_WINDOW_DAYS, 365 is the temporal signal's max_gap_days.
    # A pair exactly on either edge is inside the model's own claim.
    rows = [pair(gap=-180), pair(gap=365, succ="3"), pair(gap=-181, succ="4"),
            pair(gap=366, succ="5")]
    assert summarize(rows)["coherent_ge_070"] == 2


def test_coherent_ignores_pairs_below_the_triage_threshold():
    assert summarize([pair(score=0.69, gap=10)])["coherent_ge_070"] == 0


def test_coherent_share_is_zero_not_error_when_no_pair_reaches_threshold():
    # None would propagate into the diff as an unorderable value; 0.0 keeps a
    # sweep that emitted nothing comparable against one that did.
    assert summarize([pair(score=0.4)])["coherent_share_ge_070"] == 0.0


def test_gap_days_none_is_not_coherent():
    # A pair with an unparseable date on either side cannot be judged
    # temporally, and counting it as coherent would inflate the goal metric.
    assert summarize([pair(gap=None)])["coherent_ge_070"] == 0


def test_vin_only_requires_vin_to_be_the_sole_evidence():
    rows = [
        pair(matched_on=["vin-overlap"]),
        pair(matched_on=["vin-overlap", "address"], succ="3"),
    ]
    assert summarize(rows)["vin_only"] == 1


def test_vin_only_identity_tolerates_corroborating_signals():
    # A pair carrying temporal or agent alongside the VIN is still reachable
    # only because vin-overlap is conclusive — neither can lift it over the
    # 0.35 floor. The strict metric excludes it; this one does not, and the
    # two disagreed by 156 pairs on the baseline index.
    rows = [
        pair(matched_on=["vin-overlap", "temporal"]),
        pair(matched_on=["vin-overlap", "address"], succ="3"),
    ]
    result = summarize(rows)
    assert result["vin_only"] == 0
    assert result["vin_only_identity"] == 1


def test_triage_unbounded_admits_pre_shutdown_pairs():
    # The 906-style filter as actually run: bounded above only.
    rows = [pair(score=0.7, gap=-2000, matched_on=["vin-overlap"])]
    assert summarize(rows)["triage_unbounded"] == 1
    assert summarize(rows)["triage_bounded"] == 0


def test_triage_requires_a_corroborating_identifier():
    rows = [pair(score=0.9, gap=10, matched_on=["name-phonetic", "address"])]
    assert summarize(rows)["triage_unbounded"] == 0


def test_identical_name_triage_compares_exact_bytes():
    rows = [
        pair(score=0.9, gap=1, matched_on=["exact-identifier"],
             pred_name="ALPHA", succ_name="ALPHA"),
        pair(score=0.9, gap=1, matched_on=["exact-identifier"], succ="3",
             pred_name="ALPHA", succ_name="ALPHA CO"),
    ]
    assert summarize(rows)["identical_name_triage"] == 1


def test_canary_counts_near_perfect_immediate_renames():
    # The README's sanity anchor: a byte-identical legal name re-registering
    # within days of shutdown at ~0.9998. If a config change stops surfacing
    # this shape, the change is wrong.
    rows = [pair(score=0.9998, gap=1, matched_on=["exact-identifier"],
                 pred_name="ALPHA", succ_name="ALPHA")]
    assert summarize(rows)["canary"] == 1


def test_predecessors_with_pairs_deduplicates():
    rows = [pair(pred="1", succ="2"), pair(pred="1", succ="3")]
    assert summarize(rows)["predecessors_with_pairs"] == 1


def test_compare_flags_a_must_not_fall_metric_that_fell():
    deltas = compare({"vin_only": 675}, {"vin_only": 600}, {"vin_only": "must_not_fall"})
    assert deltas[0].ok is False
    assert deltas[0].delta == -75


def test_compare_accepts_a_must_not_fall_metric_that_rose():
    deltas = compare({"vin_only": 675}, {"vin_only": 700}, {"vin_only": "must_not_fall"})
    assert deltas[0].ok is True


def test_compare_treats_informational_metrics_as_always_ok():
    deltas = compare({"pairs": 421846}, {"pairs": 100}, {"pairs": "informational"})
    assert deltas[0].ok is True


def test_compare_tolerance_expectation_allows_a_bounded_fall():
    deltas = compare(
        {"predecessors_with_pairs": 1000},
        {"predecessors_with_pairs": 960},
        {"predecessors_with_pairs": "within_10pct"},
    )
    assert deltas[0].ok is True


def test_compare_tolerance_expectation_rejects_an_unbounded_fall():
    deltas = compare(
        {"predecessors_with_pairs": 1000},
        {"predecessors_with_pairs": 800},
        {"predecessors_with_pairs": "within_10pct"},
    )
    assert deltas[0].ok is False


def test_compare_rejects_an_unknown_expectation_rather_than_passing_it():
    # A typo in an expectation name must not silently become "no opinion",
    # which would let a regression through wearing a green check.
    with pytest.raises(ValueError, match="unknown expectation"):
        compare({"pairs": 1}, {"pairs": 1}, {"pairs": "must_not_wobble"})


def test_compare_rejects_a_metric_missing_from_the_baseline():
    with pytest.raises(KeyError):
        compare({}, {"pairs": 1}, {"pairs": "informational"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.sweep_compare'`

- [ ] **Step 3: Write the implementation**

Create `utils/sweep_compare.py`:

```python
"""Fixed metrics for judging whether a re-sweep improved or regressed.

Every precision change in DOT-Commercial/README.md's open items is expected to
shrink the pair population; shrinkage alone therefore proves nothing, because
the cheapest way to shrink it is to lose real matches. This module pins the
counts that distinguish the two before any change is made, so a later run is
compared against a number chosen in advance rather than one rationalized after
the fact.

Kept free of Elasticsearch imports on purpose: anything here must be callable
from a test with plain dicts, the same split that keeps utils/crash_lift.py
testable while scripts/measure_crash_lift.py stays integration-shaped.
"""

from dataclasses import dataclass

from matching.scorer import IDENTITY_SIGNAL_TYPES

# The threshold the README's triage set and both validation scripts already
# use. Reused rather than re-chosen: an edge picked after seeing the outcome is
# the standard way this analysis fools its author.
TRIAGE_SCORE = 0.70

# Anchored to matching/signals.py's own BACKWARD_WINDOW_DAYS and the configured
# temporal max_gap_days. A pair outside this window is implausible by the
# scorer's own design, so "coherent" judges the model against its own claim
# rather than a boundary invented here.
COHERENT_MIN_GAP = -180
COHERENT_MAX_GAP = 365

# The signals the triage filter treats as corroboration. Name and address
# similarity are excluded deliberately — a pair resting on those alone is the
# false-positive shape the triage set exists to exclude.
CORROBORATING = frozenset({"vin-overlap", "exact-identifier"})

# A byte-identical legal name reappearing within a week of shutdown at a near
# perfect score is the README's sanity anchor. It is counted rather than named
# because naming a flagged carrier is forbidden; the count survives
# anonymization and still fails loudly if the shape stops being surfaced.
CANARY_SCORE = 0.99
CANARY_MAX_GAP = 7

METRICS = (
    "pairs",
    "pairs_ge_070",
    "coherent_ge_070",
    "coherent_share_ge_070",
    "vin_only",
    "vin_only_identity",
    "triage_unbounded",
    "triage_bounded",
    "identical_name_triage",
    "canary",
    "predecessors_with_pairs",
)

EXPECTATIONS = ("must_not_fall", "must_not_rise", "informational", "within_10pct")

TOLERANCE = 0.10


@dataclass
class MetricDelta:
    """One metric's before/after, and whether that movement was permitted.

    Carries the expectation alongside the numbers so a printed table shows why
    a fall was accepted in one run and rejected in another — the two differ by
    which change is under test, not by the metric.
    """

    name: str
    baseline: float
    candidate: float
    delta: float
    pct: float | None
    expectation: str
    ok: bool


def _is_coherent(gap_days):
    """Whether a pair's timing is inside the window the scorer itself models.

    None means the pair carries an unparseable date on one side and cannot be
    judged temporally at all. That is not the same as "outside the window", but
    it is emphatically not coherent, and counting it as such would inflate the
    one metric this whole plan is trying to move.
    """
    if gap_days is None:
        return False
    return COHERENT_MIN_GAP <= gap_days <= COHERENT_MAX_GAP


def summarize(pairs) -> dict:
    """Reduce a pair population to the fixed metric record.

    Takes any iterable of pair _source dicts so the caller can stream a scan
    response through it without holding 400k pairs in memory.
    """
    counts = dict.fromkeys(METRICS, 0)
    predecessors = set()

    for row in pairs:
        score = row.get("total_score") or 0.0
        gap = row.get("gap_days")
        matched = set(row.get("matched_on") or ())
        pred = row.get("predecessor") or {}
        succ = row.get("successor") or {}

        counts["pairs"] += 1
        predecessors.add(pred.get("dot_number"))

        # Two readings of "shares a vehicle and nothing else", both tracked
        # because they answer different questions and disagreed by 156 pairs
        # on the baseline (519 against 675). The strict one is the literal
        # population; the identity one is the population that exists ONLY
        # because vin-overlap is marked conclusive, since agent (0.04) and
        # temporal (0.05) cannot lift a pair over the 0.35 floor between them.
        # Collapsing them into one metric would silently pick a side.
        if matched == {"vin-overlap"}:
            counts["vin_only"] += 1
        if matched & IDENTITY_SIGNAL_TYPES == {"vin-overlap"}:
            counts["vin_only_identity"] += 1

        if score < TRIAGE_SCORE:
            continue
        counts["pairs_ge_070"] += 1
        if _is_coherent(gap):
            counts["coherent_ge_070"] += 1

        if not matched & CORROBORATING:
            continue
        if gap is not None and gap <= COHERENT_MAX_GAP:
            counts["triage_unbounded"] += 1
            identical = (
                pred.get("legal_name") is not None
                and pred.get("legal_name") == succ.get("legal_name")
            )
            if identical:
                counts["identical_name_triage"] += 1
            if gap >= 0:
                counts["triage_bounded"] += 1
                if identical and gap <= CANARY_MAX_GAP and score >= CANARY_SCORE:
                    counts["canary"] += 1

    counts["predecessors_with_pairs"] = len(predecessors)
    counts["coherent_share_ge_070"] = (
        counts["coherent_ge_070"] / counts["pairs_ge_070"]
        if counts["pairs_ge_070"]
        else 0.0
    )
    return counts


def compare(baseline: dict, candidate: dict, expectations: dict) -> list[MetricDelta]:
    """Diff two metric records against expectations declared before the run.

    Raises rather than defaulting on an unknown expectation or a missing
    baseline metric: a typo that quietly became "no opinion" would let a
    regression through wearing a green check, which is the failure this whole
    harness exists to prevent.
    """
    deltas = []
    for name, expectation in expectations.items():
        if expectation not in EXPECTATIONS:
            raise ValueError(
                "unknown expectation {!r} for metric {!r}; known are {}".format(
                    expectation, name, ", ".join(EXPECTATIONS)
                )
            )
        before = baseline[name]
        after = candidate[name]
        delta = after - before
        pct = (delta / before) if before else None

        if expectation == "informational":
            ok = True
        elif expectation == "must_not_fall":
            ok = delta >= 0
        elif expectation == "must_not_rise":
            ok = delta <= 0
        else:
            ok = pct is None or pct >= -TOLERANCE

        deltas.append(MetricDelta(name, before, after, delta, pct, expectation, ok))
    return deltas
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Write the CLI**

Create `scripts/compare_sweeps.py`:

```python
"""Diff two chameleon sweeps and fail loudly when a guarded metric regressed.

Every change in the precision plan is expected to shrink the pair population,
so "fewer pairs" is not evidence of improvement — losing real matches shrinks
it too. This reads both sweeps by explicit index name rather than through the
chameleon-candidates alias, because during an experiment the alias is exactly
the thing in motion and resolving it would compare a run against itself.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

from utils.sweep_compare import METRICS, compare, summarize

# Only the fields summarize() reads. A full pair _source carries the per-signal
# contribution array, which is roughly ten times the bytes and is never used
# here; restricting the scan is what keeps a 400k-pair diff to a few minutes.
SOURCE_FIELDS = [
    "total_score",
    "gap_days",
    "matched_on",
    "predecessor.dot_number",
    "predecessor.legal_name",
    "successor.legal_name",
]

SCAN_SIZE = 2000


def summarize_index(es, index):
    rows = scan(
        es,
        index=index,
        query={"query": {"match_all": {}}},
        _source=SOURCE_FIELDS,
        size=SCAN_SIZE,
    )
    return summarize(hit["_source"] for hit in rows)


def load_expectations(path):
    with open(path) as handle:
        return json.load(handle)


def print_table(deltas):
    print("\n{:<24} {:>12} {:>12} {:>12} {:>9}  {}".format(
        "metric", "baseline", "candidate", "delta", "pct", "expectation"))
    for d in deltas:
        pct = "-" if d.pct is None else "{:+.1%}".format(d.pct)
        mark = "ok" if d.ok else "REGRESSED"
        print("{:<24} {:>12} {:>12} {:>12} {:>9}  {} [{}]".format(
            d.name,
            round(d.baseline, 4),
            round(d.candidate, 4),
            round(d.delta, 4),
            pct,
            d.expectation,
            mark,
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-index", required=True)
    parser.add_argument("--candidate-index", required=True)
    parser.add_argument("--expectations", required=True,
                        help="JSON file mapping metric name to expectation")
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--write-baseline", default=None,
                        help="Write the candidate summary here as the new baseline")
    args = parser.parse_args()

    es = Elasticsearch(args.host)
    baseline = summarize_index(es, args.baseline_index)
    candidate = summarize_index(es, args.candidate_index)

    print("baseline  {}: {}".format(args.baseline_index, baseline))
    print("candidate {}: {}".format(args.candidate_index, candidate))

    deltas = compare(baseline, candidate, load_expectations(args.expectations))
    print_table(deltas)

    if args.write_baseline:
        with open(args.write_baseline, "w") as handle:
            json.dump(candidate, handle, indent=2, sort_keys=True)
        print("\nwrote candidate summary to {}".format(args.write_baseline))

    regressed = [d.name for d in deltas if not d.ok]
    unguarded = sorted(set(METRICS) - set(load_expectations(args.expectations)))
    if unguarded:
        print("\nnote: no expectation declared for {}".format(", ".join(unguarded)))
    if regressed:
        print("\nREGRESSED: {}".format(", ".join(regressed)))
        return 1
    print("\nno guarded metric regressed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Capture the frozen baseline**

Read-only against the cluster; no confirmation needed.

```bash
mkdir -p DOT-Commercial/data/precision
cat > DOT-Commercial/data/precision/expect-identity.json <<'JSON'
{"pairs": "informational"}
JSON
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index chameleon-candidates-2026.08.06-000001 \
  --candidate-index chameleon-candidates-2026.08.06-000001 \
  --expectations DOT-Commercial/data/precision/expect-identity.json \
  --write-baseline DOT-Commercial/data/precision/baseline-2026.08.06.json
```

Expected: every delta zero, exit 0. This is the harness proving it agrees with itself before it is trusted to judge anything.

- [ ] **Step 7: Check the baseline against the README's published figures**

Read `DOT-Commercial/data/precision/baseline-2026.08.06.json` and confirm against `DOT-Commercial/README.md`'s open item 2: `pairs` = 421,846; `triage_unbounded` = 906; `triage_bounded` = 186; `identical_name_triage` = 436; `vin_only_identity` = 675; `pairs_ge_070` = 1,729; `coherent_ge_070` = 596; `predecessors_with_pairs` = 46,792.

`vin_only` has no README counterpart and is expected to read **519** — the README's 675 is the identity-based reading, which is why both are tracked. Confirmed against the baseline index 2026-08-08 by two `_count` queries differing only in whether `agent` and `temporal` were excluded.

If any disagree, **stop and reconcile before going further.** The harness is wrong or the README is, and either way every later comparison rests on it. A disagreement in `identical_name_triage` most likely means `legal_name` is being read from the `text` field rather than compared as raw `_source` bytes; `_source` is what `summarize` reads, so check the pair document actually carries the field.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -q
git add utils/sweep_compare.py tests/test_sweep_compare.py scripts/compare_sweeps.py
git commit -m "feat: add sweep comparison harness with declared regression expectations

Precision changes all shrink the pair population, so shrinkage alone cannot
distinguish a better filter from lost recall. Pins the counts that can, before
any change is made.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BmGiqBi59L8nj5x6eoqrkz"
```

---

### Task 2: Map `out_of_service_orders` as `nested` and query it as nested

Closes README open item 4. This is first because it repairs `shutdown_date`/`gap_days` — the field Task 3's predicate and the whole `coherent_ge_070` metric are computed from. Doing Task 3 first would build a predicate on a date that may come from an order the selector never intended to match.

**Files:**

- Modify: `DOT-Commercial/configuration/carriers/index-mappings.json:142-150`
- Modify: `matching/predecessors.py:54-70`, `matching/predecessors.py:99-101`
- Create: `tests/test_predecessors.py`

**Interfaces:**

- Consumes: `scripts/compare_sweeps.py` from Task 1.
- Produces: `PredecessorSelector.build_query()` returns a query whose out-of-service clause is `{"nested": {"path": "out_of_service_orders", "query": {...}}}`. Task 3 adds a sibling clause to the same `build_query()` output.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_predecessors.py`:

```python
"""Predecessor selection: which carriers the sweep calls "shut down".

The selector decides the population everything downstream scores, so an error
here does not fail — it quietly changes what the whole sweep is about. These
run without a cluster; build_query() is a pure function of config.
"""

from types import SimpleNamespace

import pytest

from matching.predecessors import PredecessorSelector


def selector(**kwargs):
    config = SimpleNamespace(**kwargs)
    return PredecessorSelector(es=None, source_index="carriers-000001", config=config)


def test_out_of_service_clause_is_nested_on_the_order_path():
    # An object mapping lets status and oos_date match from two different
    # array elements, so a carrier with an ACTIVE 2015 order and an INACTIVE
    # 2022 order is swept even though no single order matches both filters.
    query = selector(selector="out-of-service", oos_status=["ACTIVE"],
                     oos_date_from="2020-01-01").build_query()
    assert "nested" in query
    assert query["nested"]["path"] == "out_of_service_orders"


def test_nested_clause_puts_every_filter_inside_one_order():
    query = selector(selector="out-of-service", oos_status=["ACTIVE"],
                     oos_date_from="2020-01-01").build_query()
    must = query["nested"]["query"]["bool"]["must"]
    assert {"terms": {"out_of_service_orders.status": ["ACTIVE"]}} in must
    assert {"range": {"out_of_service_orders.oos_date": {"gte": "2020-01-01"}}} in must


def test_optional_filters_are_omitted_when_unset():
    # status and date-from are operator knobs for tightening the sweep, not
    # fields every deployment sets; an empty list must not become terms: [].
    query = selector(selector="out-of-service").build_query()
    must = query["nested"]["query"]["bool"]["must"]
    assert must == [{"exists": {"field": "out_of_service_orders.oos_date"}}]


def test_revoked_clause_is_not_nested():
    # auth_history stays an object mapping; only out_of_service_orders changed.
    query = selector(selector="revoked-authority").build_query()
    assert "nested" not in query


def test_both_selector_intersects_the_nested_and_revoked_clauses():
    query = selector(selector="both").build_query()
    clauses = query["bool"]["must"]
    assert any("nested" in c for c in clauses)
    assert len(clauses) == 2


def test_either_selector_unions_them():
    query = selector(selector="either").build_query()
    assert query["bool"]["minimum_should_match"] == 1
    assert len(query["bool"]["should"]) == 2


def test_unknown_selector_is_refused():
    with pytest.raises(ValueError, match="unknown selector"):
        selector(selector="whatever")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_predecessors.py -v`
Expected: FAIL on the four nested tests with `KeyError: 'nested'`. The `both`/`either`/unknown-selector tests should already pass.

- [ ] **Step 3: Change the mapping**

In `DOT-Commercial/configuration/carriers/index-mappings.json`, replace the `out_of_service_orders` block at line 142:

```json
      "out_of_service_orders": {
        "type": "nested",
        "properties": {
          "dot_number": { "type": "long" },
          "oos_date": { "type": "keyword" },
          "oos_reason": { "type": "keyword" },
          "status": { "type": "keyword" },
          "rescind_date": { "type": "keyword" }
        }
      },
```

Leave `auth_history`, `crashes`, `inspections`, and `boc3_agents` alone. Only the out-of-service selector reads two fields of one array element together; the others do not, and making them nested would cost query performance for nothing.

- [ ] **Step 4: Change the query**

In `matching/predecessors.py`, replace `_out_of_service_clause` (lines 54-70):

```python
    def _out_of_service_clause(self):
        """Carriers with a single out-of-service order matching every filter.

        Nested rather than a plain bool over dotted paths because an object
        mapping matches each filter against the flattened union of all the
        carrier's orders: a carrier with an ACTIVE 2015 order and an INACTIVE
        2022 order satisfied status=ACTIVE and oos_date>=2020 from two
        different orders and was swept even though no single order qualified.
        That also let TemporalSignal report a shutdown_date from an order the
        selector never intended to match, so gap_days on an emitted pair
        described the wrong event.

        Only oos_date is required; status and date-from are operator knobs for
        tightening the sweep rather than fields every deployment sets.
        """
        must = [{"exists": {"field": "out_of_service_orders.oos_date"}}]
        if self.oos_status:
            must.append({"terms": {"out_of_service_orders.status": self.oos_status}})
        if self.oos_date_from:
            # oos_date is mapped as keyword, but ISO dates sort lexicographically
            # so a range query still behaves correctly.
            must.append(
                {"range": {"out_of_service_orders.oos_date": {"gte": self.oos_date_from}}}
            )
        return {
            "nested": {
                "path": "out_of_service_orders",
                "query": {"bool": {"must": must}},
            }
        }
```

`build_query()` needs no change — `both` and `either` compose whatever the two clause builders return.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_predecessors.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit the code change before touching the cluster**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -q
git add matching/predecessors.py tests/test_predecessors.py \
        DOT-Commercial/configuration/carriers/index-mappings.json
git commit -m "fix: select predecessors with a nested out-of-service query

An object mapping matched status and oos_date from two different orders, so
carriers with no single qualifying order were swept and shutdown_date could
come from an order the selector never intended to match.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BmGiqBi59L8nj5x6eoqrkz"
```

- [ ] **Step 7: Build the experimental carriers index by reindex, not reload**

**Confirm with the user before running — this writes to the loaded cluster.**

Reindex rather than re-run `--step=carriers` from CSV: `_source` is copied verbatim, so the documents are byte-identical to the baseline and the mapping is the only variable. A CSV reload would change the data underneath the comparison and make the result uninterpretable. It is also minutes instead of hours.

```bash
# Settings and mappings from the same config files the phase would use, so the
# analyzers — and therefore the stamped fingerprint — are unchanged.
.venv/bin/python - <<'PY'
import json
from elasticsearch import Elasticsearch
from utils import analysis_fingerprint
from utils.file_utils import load_from_file

settings = json.loads(json.dumps(load_from_file(
    "DOT-Commercial/configuration/carriers/index-settings.json").settings, default=vars))
mappings = json.loads(json.dumps(load_from_file(
    "DOT-Commercial/configuration/carriers/index-mappings.json").mappings, default=vars))
fp = analysis_fingerprint.fingerprint_analysis(settings, mappings["properties"])
mappings["_meta"] = {"analysis_fingerprint": fp}
print("fingerprint", fp)

es = Elasticsearch("http://localhost:9200")
es.indices.create(index="carriers-nested-exp", settings=settings, mappings=mappings)
PY
```

Expected: `fingerprint 0595ca890d9ec6fb` — **the same value the baseline index carries.** If it differs, the mapping edit touched an analyzer binding and the comparison is no longer controlled. Stop and find out why.

```bash
curl -s -X POST "http://localhost:9200/_reindex?wait_for_completion=false" \
  -H 'Content-Type: application/json' -d '{
    "source": {"index": "carriers-2026.08.06-000001"},
    "dest": {"index": "carriers-nested-exp"}
  }'
# poll: curl -s "http://localhost:9200/_tasks?actions=*reindex&detailed" | head -40
```

- [ ] **Step 8: Verify the reindex before trusting it**

```bash
curl -s "http://localhost:9200/carriers-nested-exp/_refresh"
curl -s "http://localhost:9200/_cat/indices/carriers-nested-exp?h=index,docs.count"
curl -s "http://localhost:9200/carriers-nested-exp/_mapping" | grep -o '"out_of_service_orders":{"type":"nested"'
```

Expected: 2,085,534 docs — exactly the baseline count — and the nested type present. A short count means the reindex dropped documents and everything downstream is measuring a different population. Do not continue on a mismatch.

Then confirm the nested query returns a _smaller_ population than the flat one, which is the whole point:

```bash
curl -s "http://localhost:9200/carriers-nested-exp/_count" -H 'Content-Type: application/json' -d '{
  "query": {"nested": {"path": "out_of_service_orders", "query": {"bool": {"must": [
    {"exists": {"field": "out_of_service_orders.oos_date"}},
    {"terms": {"out_of_service_orders.status": ["ACTIVE"]}},
    {"range": {"out_of_service_orders.oos_date": {"gte": "2020-01-01"}}}
  ]}}}}}'
```

Expected: fewer than 48,540 — the predecessor count the baseline sweep examined. If it is equal or larger, the nested clause is not doing what the test says it does. Record the number; it is the new predecessor population.

- [ ] **Step 9: Sweep against the experimental index**

Guard against the same-day merge hazard first:

```bash
curl -s "http://localhost:9200/_cat/indices/chameleon-candidates-$(date +%Y.%m.%d)-000001?h=index"
```

Expected: empty output. **If it prints an index name, stop** — the sweep would merge into it and produce a union of two configs. Delete it deliberately (confirm with the user) or run on the next day.

Point the sweep at the experimental source by editing one line in `DOT-Commercial/configuration/chameleon-detection/entity-match.json`:

```json
  "source_index": "carriers-nested-exp",
```

Then:

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection
echo "task-2 chameleon-candidates-$(date +%Y.%m.%d)-000001" >> DOT-Commercial/data/precision/result-indexes.txt
```

This creates `chameleon-candidates-<today>-000001` and repoints the `chameleon-candidates-000001` alias at it. The baseline index stays on disk, so the rollback in Step 11 is an alias move.

- [ ] **Step 10: Compare against the frozen baseline**

```bash
cat > DOT-Commercial/data/precision/expect-nested.json <<'JSON'
{
  "pairs": "informational",
  "pairs_ge_070": "informational",
  "coherent_share_ge_070": "must_not_fall",
  "vin_only": "within_10pct",
  "vin_only_identity": "within_10pct",
  "triage_bounded": "within_10pct",
  "identical_name_triage": "within_10pct",
  "canary": "must_not_fall",
  "predecessors_with_pairs": "informational",
  "triage_unbounded": "informational",
  "coherent_ge_070": "informational"
}
JSON
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index chameleon-candidates-2026.08.06-000001 \
  --candidate-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --expectations DOT-Commercial/data/precision/expect-nested.json
```

Why these expectations: this change removes carriers that never had a single qualifying order, so `pairs` and `predecessors_with_pairs` should fall and that is the goal — informational. `coherent_share_ge_070` must not fall, because a repaired `shutdown_date` should make timing _more_ accurate, not less. `canary` must not fall: the byte-identical-name-within-days shape is real by construction and no correct filter removes it. The three `within_10pct` metrics are the recall tripwires — they may move a little as mis-selected predecessors leave, but a double-digit fall means real matches went with them.

- [ ] **Step 11: Decide, and roll back if it regressed**

If exit code is 0: keep the result and go to Step 12.

If exit code is 1, the alias repoint is the rollback and it is atomic:

```bash
curl -s -X POST "http://localhost:9200/_aliases" -H 'Content-Type: application/json' -d '{
  "actions": [
    {"remove": {"index": "chameleon-candidates-'"$(date +%Y.%m.%d)"'-000001", "alias": "chameleon-candidates-000001"}},
    {"add": {"index": "chameleon-candidates-2026.08.06-000001", "alias": "chameleon-candidates-000001"}}
  ]
}'
```

Then investigate which metric moved and why before changing anything else. Do not adjust the expectations to match the result — that turns the guardrail into a rubber stamp.

- [ ] **Step 12: Promote the experimental index and restore the config**

**Confirm with the user before running.**

```bash
# Point carriers-000001 at the nested index; the old one stays on disk.
curl -s -X POST "http://localhost:9200/_aliases" -H 'Content-Type: application/json' -d '{
  "actions": [
    {"remove": {"index": "carriers-2026.08.06-000001", "alias": "carriers-000001"}},
    {"add": {"index": "carriers-nested-exp", "alias": "carriers-000001"}}
  ]
}'
```

Revert `source_index` in `entity-match.json` to `"carriers-000001"` and verify nothing experimental leaked into the commit:

```bash
git diff -- DOT-Commercial/configuration/chameleon-detection/entity-match.json
grep -rn "nested-exp" --include="*.json" --include="*.py" --include="*.md" . | grep -v docs/superpowers/plans
```

Expected: no diff on `source_index`, and no `nested-exp` outside this plan file.

- [ ] **Step 13: Record the measurement and commit**

Update `DOT-Commercial/README.md`: move open item 4 (`entity-match` over-selects predecessors) to closed, and record in open item 2 the measured before/after table from Step 10 — predecessor count, pairs emitted, `coherent_share_ge_070`, and the three tripwires. Anonymize any example. Keep the figures measured, not estimated.

```bash
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index chameleon-candidates-2026.08.06-000001 \
  --candidate-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --expectations DOT-Commercial/data/precision/expect-nested.json \
  --write-baseline DOT-Commercial/data/precision/baseline-nested.json
git add DOT-Commercial/README.md
git commit -m "docs: record the nested out-of-service selector measurement

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BmGiqBi59L8nj5x6eoqrkz"
```

---

### Task 3: Require temporal coherence in the selector instead of scoring it

Closes precision-fix item 3 from the README's ranked table, which currently has no home in the open-items list. This is the single largest expected precision gain: 42.1% of the ≥ 0.70 tier registered more than 180 days _before_ the predecessor's shutdown, which fails the project's own definition of a chameleon while still clearing 0.70 on name and address.

**Files:**

- Modify: `matching/predecessors.py` — no change; the filter belongs in the pair loop, see below
- Modify: `matching/scorer.py` — add the coherence gate
- Modify: `tests/test_scorer.py`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`

**Interfaces:**

- Consumes: `PredecessorSelector.build_query()` from Task 2 (unchanged here).
- Produces: `PairScorer` honours `scoring.min_gap_days` and `scoring.max_gap_days` (both optional, default `None` = no gate), reading the gap from the same `temporal` signal config the score already uses. `PairScorer.score_pair` returns `None` for a pair outside the window.

**Design note — why the scorer and not the selector.** The README's ranked table calls this a "selector predicate", but the gap is a property of the _pair_ (successor `add_date` minus predecessor `oos_date`), not of the predecessor alone. A predecessor query cannot express it. Putting it in `PairScorer` keeps it in the one place that already has both documents and already knows how to compute the gap, and makes it configurable rather than hard-coded. Recording the disagreement here rather than silently doing something different from the table.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scorer.py`. It already defines `cfg`, `doc`, `scoring(**overrides)`, `NAME_SIGNAL`, `VIN_SIGNAL`, and `strong_pair()`; reuse them rather than adding a second set of fixtures. Two new module-level helpers are needed — add them next to `strong_pair()`:

```python
TEMPORAL_SIGNAL = cfg(
    type="temporal",
    weight=0.5,
    predecessor_date="out_of_service_orders.oos_date",
    successor_date="add_date",
    max_gap_days=365,
)


def dated_pair(oos_date, add_date):
    """A pair that would score 1.0 on evidence, differing only in its timing.

    Built on strong_pair() so the gate is the only thing that can reject it:
    if one of these tests fails, the gap window is the cause, not a weakened
    signal somewhere else in the fixture.
    """
    pred, cand = strong_pair()
    pred.source["out_of_service_orders"] = [{"oos_date": oos_date}]
    cand.source["add_date"] = add_date
    return pred, cand
```

Then the tests:

```python
def test_pair_outside_the_configured_gap_window_is_dropped():
    # A successor registered years before the predecessor's shutdown is not a
    # reincarnation by this project's own definition, however well its name
    # and address match. 42.1% of the >= 0.70 tier had this shape.
    scorer = PairScorer(
        [NAME_SIGNAL, VIN_SIGNAL, TEMPORAL_SIGNAL],
        scoring(min_gap_days=-180, max_gap_days=365),
    )
    pred, cand = dated_pair("2022-01-01", "2018-01-01")
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_pair_inside_the_window_is_kept():
    scorer = PairScorer(
        [NAME_SIGNAL, VIN_SIGNAL, TEMPORAL_SIGNAL],
        scoring(min_gap_days=-180, max_gap_days=365),
    )
    pred, cand = dated_pair("2022-01-01", "2022-02-01")
    assert scorer.score_pair(pred, cand, ScoringContext()) is not None


def test_gap_window_edges_are_inclusive():
    scorer = PairScorer(
        [NAME_SIGNAL, VIN_SIGNAL, TEMPORAL_SIGNAL],
        scoring(min_gap_days=-180, max_gap_days=365),
    )
    pred, cand = dated_pair("2022-01-01", "2023-01-01")  # exactly 365 days
    assert scorer.score_pair(pred, cand, ScoringContext()) is not None


def test_unparseable_dates_do_not_drop_the_pair():
    # A pair whose dates cannot be read is "not evaluable", not "incoherent".
    # Dropping it would silently discard every carrier with a malformed legacy
    # date, which is a recall loss disguised as a precision gain.
    scorer = PairScorer(
        [NAME_SIGNAL, VIN_SIGNAL, TEMPORAL_SIGNAL],
        scoring(min_gap_days=-180, max_gap_days=365),
    )
    pred, cand = dated_pair("NOT-A-DATE", "2022-02-01")
    assert scorer.score_pair(pred, cand, ScoringContext()) is not None


def test_gate_is_off_when_unconfigured():
    # Absent config must mean "no gate", so an existing deployment that has not
    # opted in keeps its current population. scoring() ships no gap keys.
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL, TEMPORAL_SIGNAL], scoring())
    pred, cand = dated_pair("2022-01-01", "2010-01-01")
    assert scorer.score_pair(pred, cand, ScoringContext()) is not None


def test_gate_is_off_when_no_temporal_signal_is_configured():
    # The gap comes from the temporal signal's own field paths, so a config
    # with the window set but no temporal signal has nothing to read. Passing
    # the pair through is the safe reading: the alternative is dropping every
    # pair in the sweep on a config the operator thought was a tightening.
    scorer = PairScorer(
        [NAME_SIGNAL, VIN_SIGNAL], scoring(min_gap_days=-180, max_gap_days=365)
    )
    pred, cand = dated_pair("2022-01-01", "2010-01-01")
    assert scorer.score_pair(pred, cand, ScoringContext()) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scorer.py -v -k gap`
Expected: FAIL — the first test returns a pair instead of `None`.

- [ ] **Step 3: Implement the gate in `matching/scorer.py`**

Add to `PairScorer.__init__`, reading from the `scoring` config:

```python
        # A gap window the pair must fall inside to be emitted at all, rather
        # than a 0.05-weighted signal it can simply outvote. The temporal
        # signal carries at most ~0.053 of a 0.94 total while the three name
        # signals carry 0.45, so a pair registered years before the shutdown
        # still cleared 0.70 on name and address alone — 42.1% of the >= 0.70
        # tier had exactly that shape and is not a reincarnation by the
        # project's own definition. None on either bound leaves the gate off,
        # so a deployment that has not opted in keeps its population.
        self.min_gap_days = getattr(scoring_config, "min_gap_days", None)
        self.max_gap_days = getattr(scoring_config, "max_gap_days", None)
        # The raw config, not the built Signal: the gate needs the two field
        # paths, and reading them from the same entry that produces the score
        # is what stops the gate and the score from disagreeing about which
        # dates a pair's gap is measured between.
        self._temporal_config = next(
            (c for c in signal_configs if getattr(c, "type", None) == "temporal"), None
        )
```

`PairScorer.__init__` is `(self, signal_configs, scoring_config)`; both names above match it.

Add the check inside `score_pair`, after the gap is available and before the score thresholds are applied. Compute the gap from the configured `temporal` signal's field paths so it cannot drift from what the score and the emitted document use:

```python
    def _gap_outside_window(self, pred, cand):
        """Whether this pair's timing puts it outside the configured window.

        Returns False when either date is unparseable: that is "not evaluable",
        not "incoherent", and dropping it would discard every carrier carrying
        a malformed legacy date — a recall loss wearing a precision gain's
        clothes. Same distinction the signals draw between None and 0.0.
        """
        if self.min_gap_days is None and self.max_gap_days is None:
            return False
        if self._temporal_config is None:
            return False
        shutdown = _latest_date(pred.value(self._temporal_config.predecessor_date))
        registered = _latest_date(cand.value(self._temporal_config.successor_date))
        if shutdown is None or registered is None:
            return False
        gap = (registered - shutdown).days
        if self.min_gap_days is not None and gap < self.min_gap_days:
            return True
        return self.max_gap_days is not None and gap > self.max_gap_days
```

`_latest_date` already exists at `matching/signals.py:639` and returns the most recent parseable date from a scalar-or-list `_source` value. Add it to `matching/scorer.py`'s existing `from matching.signals import build_signal` line. It is underscore-prefixed but is the same helper `TemporalSignal.score` uses, and reusing it is the point — a second date-picking implementation would let the gate and the score disagree about which order's date a pair's gap is measured from, which is the exact defect Task 2 just fixed one layer down.

Call it from `score_pair` before the score thresholds are applied, so a pair outside the window is never counted toward any guard:

```python
        if self._gap_outside_window(pred, cand):
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scorer.py -v`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 5: Turn it on in config**

In `DOT-Commercial/configuration/chameleon-detection/entity-match.json`, add to `scoring`:

```json
  "scoring": {
    "min_total_score": 0.35,
    "min_signals": 2,
    "require_identity_signal": true,
    "max_pairs_per_predecessor": 10,
    "min_gap_days": -180,
    "max_gap_days": 365
  }
```

`-180` is `BACKWARD_WINDOW_DAYS` from `matching/signals.py` — the pre-positioning window the temporal signal itself already treats as plausible, so the gate admits exactly what the scorer claims to model and nothing wider. `365` is the existing `temporal.max_gap_days`. Both are reused rather than newly chosen for the reason `utils/crash_lift.py` states about its own bands: an edge picked after seeing the outcome is how this analysis fools its author.

- [ ] **Step 6: Lint, test, commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -q
git add matching/scorer.py tests/test_scorer.py \
        DOT-Commercial/configuration/chameleon-detection/entity-match.json
git commit -m "feat: gate pairs on temporal coherence instead of only scoring it

temporal carries 0.05 of the 0.94 total against 0.45 on name, so a successor
registered years before the shutdown still cleared 0.70. Makes the window a
precondition, defaulting off so an unconfigured deployment is unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BmGiqBi59L8nj5x6eoqrkz"
```

- [ ] **Step 7: Re-sweep and compare**

No reload: the sweep reads `carriers-000001` and touches no CSV. Guard the same-day hazard first, exactly as in Task 2 Step 9.

```bash
curl -s "http://localhost:9200/_cat/indices/chameleon-candidates-$(date +%Y.%m.%d)-000001?h=index"   # must be empty
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection
echo "task-3 chameleon-candidates-$(date +%Y.%m.%d)-000001" >> DOT-Commercial/data/precision/result-indexes.txt

cat > DOT-Commercial/data/precision/expect-temporal.json <<'JSON'
{
  "pairs": "informational",
  "pairs_ge_070": "informational",
  "coherent_ge_070": "within_10pct",
  "coherent_share_ge_070": "must_not_fall",
  "vin_only": "informational",
  "vin_only_identity": "informational",
  "triage_bounded": "within_10pct",
  "identical_name_triage": "within_10pct",
  "canary": "must_not_fall",
  "predecessors_with_pairs": "informational",
  "triage_unbounded": "informational"
}
JSON
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index "$(awk '/^task-2 /{print $2}' DOT-Commercial/data/precision/result-indexes.txt)" \
  --candidate-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --expectations DOT-Commercial/data/precision/expect-temporal.json
```

The baseline here is **Task 2's result index, not the original 2026.08.06 one.** Each task is compared against the state it actually changed, so a regression is attributable to one change.

Expectations differ from Task 2 in two places, both deliberate. `vin_only` becomes informational: those pairs are triaged by `gap_days` rather than score, and this gate is a `gap_days` filter, so a large fall there is the intended effect and not a tripwire. `coherent_ge_070` becomes `within_10pct` — the _count_ of coherent pairs must survive even as the incoherent ones leave, and it is the share that should rise sharply. If `coherent_ge_070` falls more than 10% the gate is cutting pairs it should be keeping, most likely through the unparseable-date path.

Expected direction, from the baseline distribution: `pairs_ge_070` falls from 1,729 toward ~596, `coherent_share_ge_070` rises from 0.345 toward 1.0. A share that does not reach ~1.0 means the gate and the metric disagree about the window — find out which is wrong.

- [ ] **Step 8: Roll back or promote**

Same alias mechanics as Task 2 Step 11. On success, update `DOT-Commercial/README.md`: precision-fix item 3 in the ranked table moves from "not yet an item" to closed, with the measured before/after, and open item 2's `coherent` figures are restated against the new run.

---

### Task 4: Rebalance the name weights

Closes README open item 3's remaining half. Deliberately after Tasks 2 and 3, because the README says this decision should be made against real sweep output — and Tasks 2 and 3 change that output materially. Tuning against today's numbers would be tuning against a distribution that is about to be replaced.

**Files:**

- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json:14-71`

**Interfaces:**

- Consumes: `scripts/compare_sweeps.py`, and Task 3's result index as the baseline.
- Produces: no code interface. Config only.

- [ ] **Step 1: Record the current weight distribution**

Read `entity-match.json` and confirm: name signals 0.22 + 0.13 + 0.10 = 0.45; address 0.20; exact-identifier 0.12; vin-overlap 0.08; temporal 0.05; agent 0.04. Total 0.94.

Note that `PairScorer` renormalizes over _evaluable_ signals, so these are relative, not absolute — a pair missing a field is scored over the weights that did evaluate.

- [ ] **Step 2: Write the candidate weighting**

Move 0.15 off name and onto the two signals the validation showed are underweighted relative to what they prove. Name goes 0.45 → 0.30, split 0.15/0.09/0.06 to hold the 22:13:10 ratio between the three arms (to two decimal places) so this tests _how much_ name is worth rather than also changing _which_ name signal matters. `exact-identifier` goes 0.12 → 0.19 and `vin-overlap` 0.08 → 0.16, which is the full 0.15 redistributed.

Check the arithmetic before running: 0.30 + 0.20 address + 0.19 + 0.16 + 0.05 temporal + 0.04 agent = **0.94**, unchanged. This matters because `min_total_score = 0.35` is an absolute threshold — a redistribution that quietly changed the total would move the emit floor's meaning and make the comparison against Task 3's baseline measure two things at once.

```json
    {"type": "name-phonetic", "weight": 0.15, "fields": ["legal_name", "dba_name"], "subfield": "phonetic", "cross_field": true},
    {"type": "name-phonetic", "weight": 0.09, "fields": ["legal_name", "dba_name"], "subfield": "phonetic_bm", "cross_field": true},
    {"type": "name-token", "weight": 0.06, "fields": ["legal_name", "dba_name"], "subfield": "clean", "cross_field": true},
```

and `exact-identifier` to `0.19`, `vin-overlap` to `0.16`. Leave `address` at 0.20, `temporal` at 0.05 (Task 3 made timing a gate, so its weight no longer has to carry that job), and `agent` at 0.04.

- [ ] **Step 3: Re-sweep and compare**

```bash
curl -s "http://localhost:9200/_cat/indices/chameleon-candidates-$(date +%Y.%m.%d)-000001?h=index"   # must be empty
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection
echo "task-4 chameleon-candidates-$(date +%Y.%m.%d)-000001" >> DOT-Commercial/data/precision/result-indexes.txt

cat > DOT-Commercial/data/precision/expect-weights.json <<'JSON'
{
  "pairs": "informational",
  "pairs_ge_070": "informational",
  "coherent_ge_070": "informational",
  "coherent_share_ge_070": "must_not_fall",
  "vin_only": "must_not_fall",
  "vin_only_identity": "must_not_fall",
  "triage_bounded": "must_not_fall",
  "identical_name_triage": "within_10pct",
  "canary": "must_not_fall",
  "predecessors_with_pairs": "informational",
  "triage_unbounded": "informational"
}
JSON
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index "$(awk '/^task-3 /{print $2}' DOT-Commercial/data/precision/result-indexes.txt)" \
  --candidate-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --expectations DOT-Commercial/data/precision/expect-weights.json
```

`identical_name_triage` is `within_10pct` rather than `must_not_fall` here for a reason specific to this change: reducing name weight _should_ pull down some byte-identical-name pairs that had no other corroboration, and that is the intended effect. A fall beyond 10% means the reweighting went too far and is discarding the strongest evidence shape the sweep has.

- [ ] **Step 4: Run both validation scripts and record**

The comparison harness measures the sweep against itself. These measure it against reality, and are the reason to believe the change did anything:

```bash
.venv/bin/python scripts/measure_chameleon_shape.py \
  --pairs-index chameleon-candidates-$(date +%Y.%m.%d)-000001
.venv/bin/python scripts/measure_crash_lift.py \
  --pairs-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --carriers-index carriers-000001 \
  --crashes-index crashes-000001
```

Expected on the shape script: the pre-shutdown bands should be near-empty after Task 3's gate. Expected on the crash lift: with a smaller, more coherent flagged population, the dilution arithmetic predicts the lift moves off 1.10x toward GAO's 3.0x. **It may not, and that is a result, not a failure** — a null there still cannot distinguish a bad scorer from a bad proxy, which is why the shape script is primary. Record what happened either way.

- [ ] **Step 5: Roll back or promote, then commit**

Same alias mechanics. On success, update `DOT-Commercial/README.md` open item 3 to closed with the measured weights and the before/after, and update the tuning table's `scoring.min_signals` row neighbourhood with the new weights.

---

### Task 5: Pin `insp_carrier_state_id` and reload inspections

Closes README open item 1. Last, and outside the A/B chain, because it is the one change that alters the underlying data — after it, every baseline above describes a different corpus. **Do not batch this with Tasks 2-4**; doing so would confound a mapping fix with a data change and make both unmeasurable. (This reverses the batching-for-efficiency reading: batching saves a rebuild but costs the ability to attribute any movement, and attribution is the point of this plan.)

**Files:**

- Modify: `DOT-Commercial/configuration/inspections/index-mappings.json`

- [ ] **Step 1: Confirm the defect is still live**

```bash
curl -s "http://localhost:9200/inspections-000001/_mapping/field/insp_carrier_state_id"
```

Expected: `float` (dynamically inferred), or absent. Then count how many source rows would fail:

```bash
.venv/bin/python -c "
import pandas as pd
df = pd.read_csv('DOT-Commercial/data/inspections.csv', usecols=['insp_carrier_state_id'], dtype=str)
non_numeric = df['insp_carrier_state_id'].dropna()
non_numeric = non_numeric[~non_numeric.str.fullmatch(r'[0-9.]+')]
print('rows', len(df), 'non-numeric', len(non_numeric))
"
```

Record both numbers. The README's figure was 36,788 of 5,647,567; the live index currently holds 5,662,304, so the extract has moved and the measured number will differ.

- [ ] **Step 2: Pin the field**

In `DOT-Commercial/configuration/inspections/index-mappings.json`, add alongside `dot_number` and `inspection_id`:

```json
      "insp_carrier_state_id": {
        "type": "keyword"
      }
```

`keyword`, not a numeric type: the source column genuinely mixes numeric and non-numeric values (`'NONE'`, and state-prefixed identifiers), so no numeric type can hold all of them and any that cannot hold a value drops the entire document under `parallel_bulk`. Mirrors how `dot_number` and `inspection_id` are already pinned rather than inferred.

- [ ] **Step 3: Lint and commit the config before the reload**

```bash
.venv/bin/python -m ruff check .
git add DOT-Commercial/configuration/inspections/index-mappings.json
git commit -m "fix: pin insp_carrier_state_id as keyword

Dynamic inference took float from whichever value parallel_bulk saw first, so
every non-numeric row failed with document_parsing_exception and the load was
lossy on every full run, with which rows dropped varying by thread ordering.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BmGiqBi59L8nj5x6eoqrkz"
```

- [ ] **Step 4: Reload the chain**

**Confirm with the user — this is hours of cluster work and rebuilds indexes.**

The chain matters: inspections feeds the carriers enrich policy, so carriers must be rebuilt after inspections or the VIN path still carries the lossy data.

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=inspections
curl -s -X POST "http://localhost:9200/inspections-000001/_refresh"     # enrich only sees searchable docs
.venv/bin/python execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
.venv/bin/python execute_project.py --project=DOT-Commercial --step=carriers
```

The refresh between steps is not optional — enrich policy execution only sees searchable documents, and the 1-second default refresh interval makes this a timing-dependent silent failure where every phase logs success and the carriers come out with no enrichment.

- [ ] **Step 5: Verify the loss is gone**

```bash
curl -s "http://localhost:9200/_cat/indices/inspections-$(date +%Y.%m.%d)-000001?h=index,docs.count"
wc -l DOT-Commercial/data/inspections.csv
```

Expected: docs.count equals CSV rows minus the header. Any shortfall means documents are still dropping and the pin did not cover every offending column — check the ingest error log rather than accepting the number.

- [ ] **Step 6: Re-baseline**

The corpus changed, so the frozen baselines no longer describe it. Re-sweep and write a new baseline as the reference point for future work:

```bash
curl -s "http://localhost:9200/_cat/indices/chameleon-candidates-$(date +%Y.%m.%d)-000001?h=index"   # must be empty
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection
echo "task-5 chameleon-candidates-$(date +%Y.%m.%d)-000001" >> DOT-Commercial/data/precision/result-indexes.txt
.venv/bin/python scripts/compare_sweeps.py \
  --baseline-index "$(awk '/^task-4 /{print $2}' DOT-Commercial/data/precision/result-indexes.txt)" \
  --candidate-index chameleon-candidates-$(date +%Y.%m.%d)-000001 \
  --expectations DOT-Commercial/data/precision/expect-weights.json \
  --write-baseline DOT-Commercial/data/precision/baseline-post-inspections.json
```

Movement here is expected and is a _data_ difference, not a regression — a guarded metric that falls should be understood, not automatically rolled back. Say so explicitly in the README when recording it, the same way the crash-index note already distinguishes 333,120 from 333,122.

- [ ] **Step 7: Update the README**

Move open item 1 to closed with the measured recovered-row count. Add a line to open item 2 noting that its figures are now measured against the post-fix corpus, and which run_id.

---

### Task 6: Close out

- [ ] **Step 1: Verify the whole suite and the linter**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -v
```

Expected: `All checks passed!` and every test passing. Do not claim completion without seeing both.

- [ ] **Step 2: Check nothing experimental leaked**

```bash
git diff main --stat
grep -rn "nested-exp" --include="*.json" --include="*.py" . | grep -v docs/superpowers
```

Expected: no experimental index names in config or code.

- [ ] **Step 3: Confirm the anonymization rule held**

Review every README edit and commit message from Tasks 2-5 for company names, DOT numbers, addresses, phones, or emails belonging to matched records. Aggregate counts and `ignore_values` placeholders are fine. Anything under `DOT-Commercial/data/` is gitignored and out of scope.

- [ ] **Step 4: Now the superseded indexes can be pruned**

Only after the work is merged and the baselines are recorded. The accumulated date-stamped indexes were the rollback path for this entire plan; deleting them earlier would have removed it. `carriers-2026.08.05-000001`, `carriers-2026.08.06-000001`, `crashes-2026.08.01-000001`, and the four superseded `chameleon-candidates-*` indexes are the current backlog — roughly 5gb. **Confirm each deletion with the user**, and leave README open item 5 open: nothing in this plan decided a retention policy, and pretending otherwise would close an item that is still an unowned operational decision.

---

## Out of scope, and why

- **`AddressSignal` street parsing** (README open item 6). Real, but a second-order false-positive generator next to a top tier that is 65% temporally incoherent. Worth doing after Task 4's measurement shows how much of the remaining noise is address-shaped.
- **Sourcing officer name / EIN / DUNS** (precision-fix item 5). Researched and recorded 2026-08-08: DUNS was retired federally on 2022-04-04 and survives only as a licensed commercial product; EIN is collected by FMCSA and not disseminated, and the IRS publishes it only for tax-exempt organizations; officer name exists only outside FMCSA, in 50 state registries, a paid aggregator, L&I's HTML-only interface, or FOIA. It remains the largest single lever and none of it is cheap. The one free probe — evaluating `boc3_agents.attn_to_or_title`, already indexed at 75.4% populated and used by no signal — is a filing agent's contact rather than a carrier officer, so it will be shared across a whole book of business, which is the exact false-positive shape this plan is trying to reduce. Not worth spending a task on until Tasks 2-4 are measured.
- **Index retention policy** (README open item 5). Deliberately left open; see Task 6 Step 4.
- **Framework-level items** in the top-level README (the `dot_number` type boundary, `csv_load_utils.py` leading zeros, `matching/` not being dataset-agnostic). None of them block this work.

## What this plan cannot tell you

Both validation scripts, and every metric in the harness, measure precision-shaped properties: whether a flagged pair is temporally coherent, whether the flagged population is riskier, whether the strongest known shape still surfaces. **None of them can measure recall.** There is no list of known chameleon carriers to check the sweep against, so a real chameleon the sweep never surfaced is invisible to all of it. The `within_10pct` tripwires on `vin_only`, `triage_bounded`, and `identical_name_triage` are proxies for recall, not measurements of it — they detect a change that destroys evidence shapes the sweep already found, not one that fails to find something new. A change that passes every gate here can still have made recall worse, and nothing in this repo would show it.
