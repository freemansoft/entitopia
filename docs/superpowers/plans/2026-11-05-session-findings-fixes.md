# Session Findings Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the defects and stale claims found on 2026-11-05 while building the `anonymization-check` and `evaluate-sweep` skills (PRs #29, #30) that were recorded but not fixed.

**Architecture:** Eight independent tasks in three PRs.

- **PR A** (Tasks 1–2) hardens the sweep-comparison harness, the only place where a wrong answer looks like a pass.
- **PR B** (Tasks 3, 4, 7) corrects documentation that tells readers, or Claude, something untrue.
- **PR C** (Tasks 5–6) makes the `run-entitopia` driver safe to run beside the loaded cluster, then replaces the real hospital records it carries with synthetic ones.

Task 8 is a decision for the repository owner and is not executed by this plan.

**Tech Stack:** Python 3.11 in `.venv`, elasticsearch-py 9.4.1 against local Elasticsearch 9.4.1 (Colima), pytest, ruff, bash.

**Spec:** None. These are fixes to existing behaviour, so each task carries its own evidence: what was observed, and where. The evidence was measured against the live cluster on 2026-11-05 unless a task says otherwise.

## Global Constraints

- Every command runs from `.venv`: `.venv/bin/python`, never bare `python3` or `pip3`.
- `.venv/bin/python -m ruff check .` must print `All checks passed!` before any commit. Exemptions need a written reason at the narrowest scope.
- Elasticsearch calls pass explicit keyword arguments. Never `body=`.
- Comments and docstrings state why something exists and what breaks if it changes. Never narrate the steps the code takes.
- **Never name a flagged entity** in code, comments, config, docs, commit messages or PR text. Run the `anonymization-check` skill on the staged diff and the commit message before every commit, and on `--range origin/main...HEAD` plus the PR body before every PR.
- **Cluster writes are confirmed with the user before running.** Reads need no confirmation.
- **All work goes on a new branch and PR; never commit to `main`.** One branch per PR group: `fix/sweep-compare-guards` (A), `docs/session-corrections` (B), `fix/driver-loaded-cluster-and-fixtures` (C).
- The cluster is stopped when this plan is written. Start it with `colima start` then `docker compose -f docker/compose.yml up -d`. Claude's shell may lack Homebrew on PATH; prefix commands with `export PATH="/opt/homebrew/bin:$PATH"`.

## Decisions already taken (recorded so an executor does not reopen them)

- **D1 — `within_10pct` keeps its one-sided meaning.** The precision plan's tests only ever exercise falls, and the metrics it guards (`vin_only`, `triage_bounded`, `identical_name_triage`) are recall tripwires, where a rise is the goal. Changing the semantics would silently re-judge every committed `expect-*.json`. The defect is that the name reads as symmetric, so the fix is an explicit docstring plus a new, genuinely symmetric `stable_within_10pct` for metrics where any large move is suspect, such as CMS `facilities_with_pairs`.
- **D2 — CLAUDE.md's curl claim is corrected, not made true.** Adding `Bash(curl -s http://localhost:9200/*)` to the committed `settings.json` would also auto-allow `curl -s http://localhost:9200/<index> -X DELETE`, because the URL comes first. CLAUDE.md itself says the allowlist cannot tell reads from writes.
- **D3 — committed fixtures use synthetic identities.** CMS facility IDs become `00xxxx`. State code `00` is not assigned, so this keeps the zero-padding the examples exist to show, and Task 6 verifies against the cluster that no `00` prefix exists. Phones use the fictional `555-01xx` line range.

---

### Task 1: `compare_sweeps.py` refuses a signal-type rename

**Evidence:** Between `chameleon-candidates-2026.08.13-000001` and `chameleon-candidates-2026.08.17-000001`, commit `e9e2c59` renamed the VIN signal type `vin-overlap` to `shared-token`, and `d2d411c` renamed `agent` to `rarity-weighted-value`. `DOT-Commercial/precision_metrics.py` knows only the new names, so the baseline reads `vin_only` = 0 and `vin_only_identity` = 0. The `must_not_fall` guard then prints `ok` over 0 → 208, and `compare_sweeps.py` exits 0. In fact both indexes hold 1,051 pairs under their respective names, and `compare_pair_ids.py` reports identical pair sets. `.claude/skills/evaluate-sweep/scripts/guarded_compare.py` already refuses this case; the framework script does not.

**Files:**

- Modify: `utils/sweep_compare.py` (add `signal_type_drift`)
- Modify: `scripts/compare_sweeps.py` (preflight plus the `--accept-drift` flag)
- Test: `tests/test_sweep_compare.py`

**Interfaces:**

- Produces: `utils.sweep_compare.signal_type_drift(baseline_types: Iterable[str], candidate_types: Iterable[str]) -> frozenset[str]`
- Produces: `compare_sweeps.py` exit status 2 = cannot judge (distinct from 1 = regressed)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_sweep_compare.py`; extend its import line to `from utils.sweep_compare import compare, signal_type_drift`)

```python
def test_signal_type_drift_reports_both_names_of_a_rename():
    # The 2026.08.13 -> 2026.08.17 shape: one signal renamed, the rest stable.
    drift = signal_type_drift({"vin-overlap", "address"}, {"shared-token", "address"})
    assert drift == {"vin-overlap", "shared-token"}


def test_signal_type_drift_is_empty_when_vocabularies_match():
    assert signal_type_drift(["temporal", "address"], ["address", "temporal"]) == frozenset()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: FAIL with `ImportError: cannot import name 'signal_type_drift'`

- [ ] **Step 3: Implement `signal_type_drift`** (append to `utils/sweep_compare.py`; add `from collections.abc import Iterable` to its imports)

```python
def signal_type_drift(baseline_types: Iterable[str], candidate_types: Iterable[str]) -> frozenset[str]:
    """Signal types that occur in one sweep's `matched_on` and never in the other's.

    Non-empty means the two sweeps do not share a vocabulary, so any metric
    that names a signal type reads zero on the side that used the other
    name. Measured: the vin-overlap -> shared-token rename made a
    must_not_fall guard print "ok" over 0 -> 208, a rename passing as a
    measurement. Kept here, free of Elasticsearch, so the decision is
    testable with plain sets.
    """
    return frozenset(baseline_types) ^ frozenset(candidate_types)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Add the preflight to `scripts/compare_sweeps.py`**

Extend the import to `from utils.sweep_compare import compare, signal_type_drift`, and add this function after `summarize_index`:

```python
# Larger than any signal vocabulary this repo has had (eight types); a terms
# aggregation that hit this ceiling would silently drop a type from the check.
VOCABULARY_SIZE = 100


def matched_on_types(es, index):
    """The signal types that actually occur in an index's `matched_on`."""
    response = es.search(
        index=index, size=0, aggs={"v": {"terms": {"field": "matched_on", "size": VOCABULARY_SIZE}}}
    )
    return {bucket["key"] for bucket in response["aggregations"]["v"]["buckets"]}
```

In `main()`, register the flag next to `--write-baseline`:

```python
    parser.add_argument("--accept-drift", action="store_true",
                        help="compare anyway after a signal-type rename; only once each guarded metric is ruled unaffected")
```

Then insert this immediately after `es = Elasticsearch(args.host)` and before either `summarize_index` call:

```python
    drift = signal_type_drift(matched_on_types(es, args.baseline_index), matched_on_types(es, args.candidate_index))
    if drift and not args.accept_drift:
        print("CANNOT JUDGE: signal types {} occur in only one of the two sweeps; every metric naming "
              "them would compare a rename, not a change. Re-sweep the baseline with the current code, "
              "or pass --accept-drift.".format(", ".join(sorted(drift))))
        return 2
```

Add one line to the module docstring: `Exit 0: no guarded metric regressed. 1: at least one did. 2: the two sweeps use different signal-type names and cannot be compared.`

- [ ] **Step 6: Verify against the cluster (read-only)**

```bash
.venv/bin/python scripts/compare_sweeps.py --baseline-index chameleon-candidates-2026.08.13-000001 \
  --candidate-index chameleon-candidates-2026.08.17-000001 \
  --expectations DOT-Commercial/data/precision/expect-weights.json; echo "exit=$?"
.venv/bin/python scripts/compare_sweeps.py --baseline-index chameleon-candidates-2026.08.12-000001 \
  --candidate-index chameleon-candidates-2026.08.13-000001 \
  --expectations DOT-Commercial/data/precision/expect-temporal.json | tail -1; echo "exit=${PIPESTATUS[0]}"
```

Expected: the first prints `CANNOT JUDGE: signal types agent, rarity-weighted-value, shared-token, vin-overlap ...` with `exit=2`. The second prints `no guarded metric regressed` with `exit=0`; those two sweeps share a vocabulary.

- [ ] **Step 7: Lint, test, scan, commit**

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q
git add utils/sweep_compare.py scripts/compare_sweeps.py tests/test_sweep_compare.py
git commit -m "fix: refuse to compare sweeps across a signal-type rename"
```

---

### Task 2: Make `within_10pct`'s one-sidedness explicit and add `stable_within_10pct`

**Evidence:** `utils/sweep_compare.py` implements `within_10pct` as `ok = pct is None or pct >= -TOLERANCE`, so a rise of any size passes. Checked directly: 100 → 500 returns `ok=True`. Nothing in the module, in `compare_pair_ids.py`'s docstring, or in the `evaluate-sweep` skill says so, and an evaluation of the CMS sweep read it as symmetric. See D1 for why the meaning stays as it is.

**Files:**

- Modify: `utils/sweep_compare.py:20-22` and `compare()`
- Modify: `scripts/compare_pair_ids.py:11` (the vocabulary list in its docstring)
- Modify: `.claude/skills/evaluate-sweep/SKILL.md` (the "Expectations" input item)
- Test: `tests/test_sweep_compare.py`

**Interfaces:**

- Consumes: nothing from Task 1
- Produces: expectation name `stable_within_10pct` in `utils.sweep_compare.EXPECTATIONS`, which `.claude/skills/evaluate-sweep/scripts/guarded_compare.py` picks up automatically because it imports `EXPECTATIONS`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_sweep_compare.py`)

```python
def test_within_10pct_accepts_any_rise_because_it_guards_falls_only():
    # Pinned deliberately: within_10pct is a recall tripwire, and a rise is
    # what the precision work wants. Making it symmetric would re-judge every
    # committed expect-*.json.
    deltas = compare({"vin_only": 100}, {"vin_only": 500}, {"vin_only": "within_10pct"})
    assert deltas[0].ok is True


def test_stable_within_10pct_rejects_a_large_rise():
    deltas = compare({"facilities_with_pairs": 1000}, {"facilities_with_pairs": 1200},
                     {"facilities_with_pairs": "stable_within_10pct"})
    assert deltas[0].ok is False


def test_stable_within_10pct_accepts_a_small_move_either_way():
    for after in (950, 1050):
        deltas = compare({"facilities_with_pairs": 1000}, {"facilities_with_pairs": after},
                         {"facilities_with_pairs": "stable_within_10pct"})
        assert deltas[0].ok is True


def test_stable_within_10pct_rejects_movement_off_a_zero_baseline():
    # pct is undefined at a zero baseline; treating that as "ok" is exactly
    # how a renamed signal's 0 -> 208 passed a must_not_fall guard.
    deltas = compare({"vin_only": 0}, {"vin_only": 208}, {"vin_only": "stable_within_10pct"})
    assert deltas[0].ok is False
    deltas = compare({"vin_only": 0}, {"vin_only": 0}, {"vin_only": "stable_within_10pct"})
    assert deltas[0].ok is True
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: `test_within_10pct_accepts_any_rise...` PASSES (it pins current behaviour). The three `stable_within_10pct` tests FAIL with `ValueError: unknown expectation 'stable_within_10pct'`.

- [ ] **Step 3: Implement.** Replace the `EXPECTATIONS` line with:

```python
# What each expectation permits, because the names alone mislead:
#   must_not_fall        any rise, no fall
#   must_not_rise        any fall, no rise
#   within_10pct         any rise, a fall of at most 10% -- ONE-SIDED. It is
#                        the recall tripwire for metrics a change is meant to
#                        grow, so it never fails on a rise.
#   stable_within_10pct  at most 10% either way; from a zero baseline, only zero
#   informational        anything; printed, never judged
EXPECTATIONS = ("must_not_fall", "must_not_rise", "informational", "within_10pct", "stable_within_10pct")
```

Then, in `compare()`, insert this branch before the final `else:`:

```python
        elif expectation == "stable_within_10pct":
            ok = after == before if not before else abs(pct) <= TOLERANCE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_compare.py -v`
Expected: PASS (all)

- [ ] **Step 5: Update the two documents that list the vocabulary**

In `scripts/compare_pair_ids.py`, change `(must_not_fall, must_not_rise, within_10pct,` to `(must_not_fall, must_not_rise, within_10pct, stable_within_10pct,`.

In `.claude/skills/evaluate-sweep/SKILL.md`, replace
``a JSON file mapping metric names to `must_not_fall`, `must_not_rise`, `within_10pct` or `informational`.``
with
``a JSON file mapping metric names to `must_not_fall`, `must_not_rise`, `within_10pct`, `stable_within_10pct` or `informational`. `within_10pct` is one-sided: it allows any rise and fails only on a fall of more than 10%. Use `stable_within_10pct` when a large move in either direction would be suspect.``

- [ ] **Step 6: Lint, test, scan, commit**

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q
git add utils/sweep_compare.py scripts/compare_pair_ids.py tests/test_sweep_compare.py .claude/skills/evaluate-sweep/SKILL.md
git commit -m "fix: say within_10pct guards falls only, and add stable_within_10pct"
```

Open **PR A** from `fix/sweep-compare-guards` with Tasks 1–2.

---

### Task 3: Correct CLAUDE.md's curl and Docker notes

**Evidence:**

- CLAUDE.md says `curl`ing the cluster "needs no approval — `.claude/settings.json` allows it". The committed `settings.json` has no `permissions` block. The curl rules that exist are exact-command entries in the gitignored `settings.local.json`, so a fresh clone is prompted for every read.
- The Docker note sends readers to `/Applications/Docker.app`, which is not installed. This machine runs Colima 0.10.3 with Homebrew's Docker CLI, and `~/.docker/config.json` has no `credsStore`, so the error the note describes cannot currently occur.

**Files:**

- Modify: `CLAUDE.md` (the "Local Elasticsearch" section)

- [ ] **Step 1: Replace the curl sentence.** Change

```
The cluster is always at **`http://localhost:9200`**, unauthenticated, and
`curl`ing it needs no approval — `.claude/settings.json` allows it. Query it
```

to

```
The cluster is always at **`http://localhost:9200`**, unauthenticated. Reading
it is always appropriate, though the committed `.claude/settings.json` does not
pre-approve `curl`: a URL-prefix rule would also approve a write whose `-X
DELETE` follows the URL, so read approvals live in each person's
`settings.local.json`. Query it
```

- [ ] **Step 2: Replace the Docker Desktop paragraph.** Change the final paragraph of the section (from `If the build fails with` to the end of the `export PATH=...` line) to

```
The container runtime is Colima, not Docker Desktop. Start the VM first, sized
for the 6 GB heap `docker/compose.yml` sets: `colima start --cpu 6 --memory 12`.
Homebrew's `docker` lives in `/opt/homebrew/bin`, which a non-login shell may
not have on PATH (`export PATH="/opt/homebrew/bin:$PATH"`). A build failing with
`docker-credential-desktop: executable file not found` means
`~/.docker/config.json` still names Docker Desktop's `credsStore`; remove that
key.
```

- [ ] **Step 3: Scan and commit**

```bash
git add CLAUDE.md
git commit -m "docs: correct CLAUDE.md on curl approval and the container runtime"
```

---

### Task 4: Point the stale `dot_number`-to-`long` closed item at its successor

**Evidence:** A closed item in `README.md` (currently line 575) says the padding fix was "an ingest pipeline per dataset converting `dot_number` to a `long`". Every `DOT-Commercial/configuration/*-ingestion-setup/pipelines.json` now normalizes to the canonical unpadded **string** (`ctx.dot_number = String.valueOf(Long.parseLong(raw))`). The closed item further up (currently line 496) records that change. The old item is accurate history, but it reads as the current design.

**Files:**

- Modify: `README.md` (the closed item beginning `**Preserving a source value exactly and making it joinable are different requirements.**`)

- [ ] **Step 1: Append to that item's `Fixed with ...` sentence.** Change

```
Fixed with an ingest pipeline per dataset converting `dot_number` to a `long`, mirroring what `crashes` already did.
```

to

```
Fixed with an ingest pipeline per dataset converting `dot_number` to a `long`, mirroring what `crashes` already did. (Superseded: the pipelines now normalize to the canonical unpadded _string_ the enrich index compares — see "The `dot_number` join now has an enforced contract" above.)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: mark the dot_number-to-long fix as superseded by the string contract"
```

---

### Task 5: Make the `run-entitopia` driver refuse a loaded cluster

**Evidence:** `driver.sh`'s header and `SKILL.md` both promise "a disposable Elasticsearch cluster". But `es_up()` now reuses `docker/compose.yml`, the single cluster definition, which serves the loaded cluster on the same port. `smoke` then runs `execute_project.py` for `hospitals`, `crashes`, `inspections`, `carriers-ingestion-setup` and `carriers`. Against the loaded cluster that creates fixture indexes, repoints `carriers-000001`, `crashes-000001` and `hospitals-000001` at them, and rebuilds the enrich policies from five-row data. `write_fixture` already refuses to overwrite large CSVs; nothing protects the indexes.

**Files:**

- Modify: `.claude/skills/run-entitopia/driver.sh` (header comment, new `refuse_loaded_cluster`, and calls in `smoke` and `run`)
- Modify: `.claude/skills/run-entitopia/SKILL.md:15` ("disposable")

**Interfaces:**

- Produces: env override `ALLOW_LOADED_CLUSTER=1`; Task 6's verification relies on the refusal

- [ ] **Step 1: Add the guard** after `es_up()`:

```bash
# A fixture run writes indexes and moves aliases, and docker/compose.yml is the
# same cluster that holds hours of loaded data. Any index this large cannot be
# fixture output (fixtures are a handful of rows), so its presence means a
# smoke run would repoint real aliases at five-row indexes and rebuild the
# enrich policies from them.
LOADED_CLUSTER_DOCS=100000

refuse_loaded_cluster() {
    [ "${ALLOW_LOADED_CLUSTER:-0}" = "1" ] && return 0
    local largest
    largest=$(curl -s "$ES_URL/_cat/indices?h=docs.count&expand_wildcards=open" | sort -n | tail -1)
    if [ "${largest:-0}" -gt "$LOADED_CLUSTER_DOCS" ]; then
        echo "REFUSING: the cluster at $ES_URL holds an index of $largest documents." >&2
        echo "  A fixture run would repoint its aliases and rebuild its enrich policies." >&2
        echo "  Stop it and run against a fresh volume (see SKILL.md), or set ALLOW_LOADED_CLUSTER=1." >&2
        exit 1
    fi
}
```

In `smoke()`, add `refuse_loaded_cluster` as the line right after `es_up`. Add the same call inside the `run)` dispatch before `run_project`, but only when `DATA_MODE=fixture`: `[ "$DATA_MODE" = "fixture" ] && refuse_loaded_cluster`.

- [ ] **Step 2: Correct the promises.** In the driver header, replace `brings up a disposable Elasticsearch cluster with the` with `brings up (or reuses) the project's Elasticsearch cluster with the`. In `SKILL.md`, replace `it brings up disposable` with `it brings up the project's cluster (refusing to write fixtures into one that holds loaded data)`, and adjust that sentence's line-wrapping so it still reads.

- [ ] **Step 3: Verify the refusal against the loaded cluster** (safe: it exits before writing anything)

```bash
curl -s 'http://localhost:9200/_cat/aliases?h=alias,index&s=alias' | grep -v '^\.' > /tmp/aliases-before.txt
.claude/skills/run-entitopia/driver.sh smoke; echo "exit=$?"
curl -s 'http://localhost:9200/_cat/aliases?h=alias,index&s=alias' | grep -v '^\.' | diff /tmp/aliases-before.txt - && echo "aliases unchanged"
```

Expected: `REFUSING: the cluster at http://localhost:9200 holds an index of 9632353 documents.` (the size of the largest index, `inspections-per-unit`), `exit=1`, and `aliases unchanged`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/run-entitopia/driver.sh .claude/skills/run-entitopia/SKILL.md
git commit -m "fix: stop the driver writing fixtures into the loaded cluster"
```

---

### Task 6: Replace real hospital identities in committed fixtures, tests and docs

**Evidence:**

- The real CMS facility IDs in the `01000N` range (Alabama) are used throughout the repo as zero-padding examples. At least one of them belongs to a hospital that appears in two `hospital-duplicates-*` pairs, which makes it a flagged record under CLAUDE.md. Which one is deliberately not recorded here: this plan outlives the fix, and naming it would keep the leak. They are committed in `README.md` (zero-padding item, currently line 577), `utils/csv_load_utils.py:34`, `tests/test_csv_load_utils.py`, `tests/test_entity_doc.py`, `tests/test_mapping_coverage.py`, `tests/test_new_project.py`, `tests/test_scaffold.py` and `docs/superpowers/plans/2026-08-16-matcher-generalization.md`.
- `run-entitopia/driver.sh:148-153` writes five real hospital records (ID, name, street, city and phone) as "synthetic fixtures". `match_demo()` and `SKILL.md:100-117` repeat one of them.
- `match_demo()` indexes its near-duplicate with fields `City`, `Phone Number` and `Addresss`, but `CMS-Providers/configuration/hospitals/index-mappings.json` maps `City/Town`, `Telephone Number` and `Address`. So its `City.keyword` blocking filter cannot match the CSV-loaded canonical record, and the "verified output" in `SKILL.md` predates the column rename.

**Files:**

- Modify: `.claude/skills/run-entitopia/driver.sh` (CMS fixture heredoc, `match_demo()`)
- Modify: `.claude/skills/run-entitopia/SKILL.md:100-117`
- Modify: `README.md`, `utils/csv_load_utils.py`, `tests/test_csv_load_utils.py`, `tests/test_entity_doc.py`, `tests/test_mapping_coverage.py`, `tests/test_new_project.py`, `tests/test_scaffold.py`, `docs/superpowers/plans/2026-08-16-matcher-generalization.md`

**Interfaces:**

- Consumes: Task 5's `refuse_loaded_cluster` (the verification below runs the driver on a fresh volume)

- [ ] **Step 1: Confirm the synthetic ID range is unused** (cluster up, read-only)

```bash
curl -s 'http://localhost:9200/hospitals-000001/_count' -H 'Content-Type: application/json' \
  -d '{"query":{"prefix":{"Facility ID":"00"}}}'
```

Expected: `"count":0`. If it is not zero, pick an unused two-digit prefix the same way and use it in place of `00` throughout this task.

- [ ] **Step 2: Swap the IDs in code, tests and docs.** Map every real fixture ID `01000N` to the synthetic `00100N` (same final digit), and the test-only `020002` to `002002`. Verify none remain:

```bash
git grep -n -E "(^|[^0-9-])0(1000[1-8]|20002)([^0-9]|$)" -- . ':!*.json' | grep -v -- "-000001"
```

Expected: no output. Then run `.venv/bin/python -m pytest -q`. Expected: all pass; the tests depend on the leading zero, not on the ID's value.

- [ ] **Step 3: Replace the driver's CMS fixture rows** (the heredoc after `write_fixture CMS-Providers/data/hospitals/Hospital_General_Information.csv`) with:

```
Facility ID,Facility Name,Address,City/Town,State,ZIP Code,Telephone Number
001001,EXAMPLE SOUTHEAST HEALTH MEDICAL CENTER,100 EXAMPLE CIRCLE,SPRINGFIELD,AL,36301,(334) 555-0101
001005,EXAMPLE NORTH MEDICAL CENTER,200 EXAMPLE HIGHWAY NORTH,RIVERTON,AL,35957,(256) 555-0105
001006,EXAMPLE MEMORIAL HOSPITAL,300 EXAMPLE STREET,LAKEVIEW,AL,35630,(256) 555-0106
001007,EXAMPLE COMMUNITY HOSPITAL,400 N EXAMPLE ST,FAIRVIEW,AL,36467,(334) 555-0107
001008,EXAMPLE COUNTY HOSPITAL,500 EXAMPLE CIRCLE,MILLBROOK,AL,36049,(334) 555-0108
```

- [ ] **Step 4: Rewrite `match_demo()` against the real column names and the synthetic record.** Make these changes:
  - near-duplicate document: `"Facility Name": "Exampel Sowth East Helth Med Ctr"`, `"Address": "100 Exampel Cir"`, `"City/Town": "SPRINGFIELD"`, `"State": "AL"`, `"Telephone Number": "(334) 555-0101"`
  - absolute match: `{"term": {"Facility Name.keyword": "EXAMPLE SOUTHEAST HEALTH MEDICAL CENTER"}}`
  - soft match: filter `{"term": {"City/Town.keyword": "SPRINGFIELD"}}`, and both `should` clauses querying `"EXAMPLE SOUTHEAST HEALTH MEDICAL CENTER"`
  - the `echo` lines: `Facility ID 001001` and `City/Town.keyword=SPRINGFIELD`

  `City/Town` has a `.keyword` subfield in the hospitals mapping, so the filter is valid; confirm with `jq '.mappings.properties["City/Town"]' CMS-Providers/configuration/hospitals/index-mappings.json`.

- [ ] **Step 5: Run the driver on a fresh volume** (**cluster writes on a separate volume: confirm with the user first**). The loaded data stays in the `docker_esdata` volume throughout.

```bash
docker compose -f docker/compose.yml down                        # loaded cluster stops; volume kept
docker compose -p entitopia-fixture -f docker/compose.yml up -d  # same container name, fresh volume
.claude/skills/run-entitopia/driver.sh smoke                     # Task 5's guard passes: no large index
.claude/skills/run-entitopia/driver.sh match-demo
docker compose -p entitopia-fixture -f docker/compose.yml down -v   # discards only the fixture volume
docker compose -f docker/compose.yml up -d                       # loaded cluster back
```

Expected from `match-demo`:

- the absolute match prints `hits: 1`, the canonical `001001`;
- the soft match prints two lines: `001001 -> EXAMPLE SOUTHEAST HEALTH MEDICAL CENTER` scored higher, then `__match-demo-dup__ -> Exampel Sowth East Helth Med Ctr` scored lower.

If the near-duplicate is missing, the phonetic or fuzzy clauses do not reach the typo. Adjust the typo, not the query, until both appear, because the demo exists to show the soft match working.

- [ ] **Step 6: Update `SKILL.md:100-117`** to name `001001` and the synthetic names and fields from Step 4. Replace its "Verified output" block with the two lines Step 5 actually printed.

- [ ] **Step 7: Scan the changed files, then commit**

```bash
.venv/bin/python .claude/skills/anonymization-check/scripts/scan_identifiers.py --staged
```

Expected: no FLAGGED or REAL finding for any hospital value (the scan exits 0). Then:

```bash
git add -A .claude/skills/run-entitopia README.md utils/csv_load_utils.py tests docs/superpowers/plans/2026-08-16-matcher-generalization.md
git commit -m "fix: use synthetic hospital identities in fixtures, tests and docs"
```

Open **PR C** from `fix/driver-loaded-cluster-and-fixtures` with Tasks 5–6.

---

### Task 7: Correct the CMS README's description of the 0.5–0.6 band

**Evidence:** `CMS-Providers/README.md:67` says the 0.5–0.6 band "is mostly two facilities in one town sharing a place name". An evaluation run measured otherwise: of 1,467 pairs in [0.50, 0.60), 88 (6%) share a city and 1,067 (73%) cross state lines. A further 1,598 pairs sit at exactly 0.60, where both name signals score 1.0 after stopwording and nothing else contributes. Re-measure before writing, because the README must quote figures this plan's executor produced.

**Files:**

- Modify: `CMS-Providers/README.md:67`

- [ ] **Step 1: Measure** (read-only; prints counts only, no facility names)

```bash
.venv/bin/python - <<'EOF'
from collections import Counter
from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

es = Elasticsearch("http://localhost:9200")
bands = Counter()
for hit in scan(es, index="hospital-duplicates-000001", query={"query": {"match_all": {}}},
                _source=["total_score", "left.City/Town", "right.City/Town", "left.State", "right.State"]):
    s = hit["_source"]
    score = s["total_score"]
    band = "[0.50,0.60)" if score < 0.6 - 1e-9 else "=0.60" if abs(score - 0.6) < 1e-9 else "(0.60,0.70)" if score < 0.7 else ">=0.70"
    left, right = s.get("left", {}), s.get("right", {})
    if left.get("State") != right.get("State"):
        where = "cross-state"
    elif str(left.get("City/Town", "")).casefold() == str(right.get("City/Town", "")).casefold():
        where = "same city"
    else:
        where = "same state, other city"
    bands[band, where] += 1
for band in ("[0.50,0.60)", "=0.60", "(0.60,0.70)", ">=0.70"):
    total = sum(n for (b, _), n in bands.items() if b == band)
    print(band, total, {w: bands[band, w] for w in ("same city", "same state, other city", "cross-state")})
EOF
```

Expected (2026-11-05): `[0.50,0.60) 1467 {'same city': 88, 'same state, other city': 312, 'cross-state': 1067}`, `=0.60 1598 {...}`, `(0.60,0.70) 540 {...}`, `>=0.70 22 {'same city': 20, ...}`.

- [ ] **Step 2: Rewrite the sentence with the measured figures.** Replace

```
What sits at 0.5–0.6 is mostly two facilities in one town sharing a place name once the generic hospital words are stripped — genuine ambiguity rather than noise, and resolving it needs a judgement about whether they are one system.
```

with (substituting Step 1's figures if they differ)

```
Below 0.70 the pairs are mostly cross-state name coincidences, not ambiguity within a town. Measured 2026-11-05: of 1,467 pairs in [0.50, 0.60), 88 (6%) share a city and 1,067 (73%) cross state lines. A further 1,598 score exactly 0.60, where two names reduce to the same token set once the generic hospital words are stripped and nothing else contributes. No signal reads `State` or `City/Town`, so geography cannot separate them. The 22 pairs at ≥ 0.70 are the part that behaves like duplicate detection: 20 of them share a city.
```

- [ ] **Step 3: Commit**

```bash
git add CMS-Providers/README.md
git commit -m "docs: replace the CMS band description with what the pairs measure"
```

Open **PR B** from `docs/session-corrections` with Tasks 3, 4 and 7.

---

### Task 8: Flagged names in git history (owner decision only; not executed by this plan)

**Evidence:** Commit `2a18806` anonymized two lines of `DOT-Commercial/README.md` that named flagged carriers. Those original lines remain in every earlier commit, and the repository is public. No later commit can remove them.

**Options, for the owner to choose:**

1. **Leave history as is.** The tip is clean, and the names are as findable as the commit that removed them.
2. **Rewrite history.** Use `git filter-repo --replace-text` on those two lines, then force-push `main`. The cost: every clone and fork must re-clone, open PR refs keep the old commits, and GitHub's cached views persist until GitHub Support purges them.

Nothing in Tasks 1–7 depends on this decision. An executor must not run `filter-repo` or force-push without the owner's explicit instruction.
