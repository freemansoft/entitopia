# Third dataset, part 1: succession windows, declared links, FDIC-Banks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the framework pieces that let a sweep be measured against registered successions, and prove them on FDIC bank data. The pieces are a settable overlap shared by the time gate and the temporal signal, a candidate end-date rule, undated handling, declared-link tagging, retrieval accounting, and a recall/precision script.

**Architecture:**

- One new `matching/window.py` becomes the single source for everything date-based: the gap bounds, undated candidates, undated predecessors, and the candidate's own end date. The candidate search, the scorer gate and the temporal signal all read that one object, so they cannot disagree.
- One new `matching/declared.py` classifies each pair as `truth`, `explains` or `none`, and assigns each truth link to a tuning or report half.
- `FDIC-Banks` is a new project with one load step and two sweeps, reappearance and absorption, that partition time at one `overlap_days` value.

**Tech Stack:** Python 3.15 (`.venv`), pinned elasticsearch-py 9.4.1 with Elasticsearch 9.4.1 (local Colima cluster at `http://localhost:9200`), pytest, ruff, prettier 3.3.3 for Markdown and JSON.

**Spec:** `docs/superpowers/specs/2026-11-26-third-dataset-design.md`

**Scope:** this plan covers the framework changes and `FDIC-Banks`. `GLEIF-Entities`, including the candidate filter (spec framework change 2), gets its own plan after this one lands. Its configuration depends on the windows and tuning figures this plan measures.

**Departure from the spec, from probing the FDIC API on 2026-11-26:**

- Each inactive institution record carries `NEWCERT`, its direct successor certificate, and `ULTCERT`, the last bank in its chain of successors. Example: an inactive record with `NEWCERT` <CERT-B>, `ULTCERT` <CERT-C>, `CHANGEC1` 223.
- So the truth and chain links come straight from the institution record. No `history` index and no enrich policy are needed.
- The history endpoint is still fetched once, in Task 6, to measure whether `NEWCERT` covers its successions. The plan stops for a decision if coverage is below 95%.
- Dates arrive as `MM/DD/YYYY` (four-digit year, so no century pivot) and are converted to ISO in an ingest pipeline.

## Global Constraints

- Run everything through `.venv/bin/python`. Never use bare `python3` or `pip3`.
- `.venv/bin/python -m ruff check .` prints `All checks passed!` before every commit.
- `.venv/bin/python -m pytest` (the whole suite) passes before every commit.
- Run `npx prettier@3.3.3 --check` on every changed Markdown and JSON file.
- Elasticsearch client calls pass explicit keyword arguments, never `body=`.
- Configuration is a `SimpleNamespace` loaded via `file_utils.load_from_file`. Read optional keys with `getattr(obj, "key", default)`.
- Comments and docstrings explain why, never what (`CLAUDE.md`).
- Every new guard gets a test whose module docstring names the silent failure it prevents, plus an entry in the README hazard or re-run table.
- DOT-Commercial's and CMS-Providers' sweeps must be unchanged. Every new setting defaults to today's behaviour.
- Never name an entity from an undeclared pair in anything committed. Entities in a `truth` pair (an FDIC-recorded succession) may be named once Task 8 lands the rule. Until then, name none.
- Data lands in `FDIC-Banks/data/<step>/`, which `.gitignore` already excludes (`*/data/`).
- Before any re-run against the loaded cluster, read the `run-entitopia` skill's re-run table. Local cluster writes are pre-authorized.
- Execute in a fresh worktree branched from `main`, not on `docs/blog-thumbnail` (superpowers:using-git-worktrees).

## Review Focus

- **An inactive bank with no parseable end date.** It cannot be placed in either window. With `undated_predecessors: "skip"` it is skipped and counted, never swept against every candidate. Tested in Task 1.
- **A candidate that ended on the predecessor's shutdown day.** Same-day chain mergers are real (A into B into C on one date). The end-date rule is inclusive, so the candidate is kept. Tested in Task 1.
- **A truth successor that scored but was cut by `max_pairs_per_predecessor`.** It counts as "retrieved, not emitted", not as "not retrieved". Tested in Task 5.
- **`NEWCERT` of `0` or blank, or naming a certificate absent from the index.** It is skipped via `ignore_values`, or excluded from the denominator and printed, never counted as a miss. Tested in Tasks 3 and 5.
- **A truth successor whose establishment date puts it in the other sweep's window.** It is excluded from this sweep's denominator as `target_outside_window` and counted in the other sweep. The two sweeps' denominators sum to the total. Tested in Task 5.

---

### Task 1: `LifecycleWindow`, one source for the gap, undated and end-date rules

**Files:**

- Create: `matching/window.py`
- Modify: `matching/candidates.py` (`__init__`, `sort_fields`, `_window_filter`, `find`)
- Modify: `matching/scorer.py` (`__init__`, `_gap_outside_window`, `score_pair`)
- Modify: `phase_providers/phase_entity_match.py` (`handle`, `_generate_actions`: skip and count undated predecessors)
- Modify: `schema/entity-match.schema.json`
- Modify: `utils/config_liveness.py` (`_declared_paths`: add `lifecycle.end_date`)
- Test: `tests/test_lifecycle_window.py`

**Interfaces:**

- Produces `class LifecycleWindow`, a frozen dataclass:
  - Fields: `lifecycle`, `min_gap_days: float | None`, `max_gap_days: float | None`, `undated_candidates: str` (`"keep"` or `"exclude"`, default `"keep"`), `undated_predecessors: str` (`"unbounded"` or `"skip"`, default `"unbounded"`).
  - `LifecycleWindow.from_config(lifecycle, scoring_config) -> LifecycleWindow | None`. Returns `None` when `lifecycle` is `None`.
  - `.shutdown(pred_source: dict) -> datetime.date | None` reads `lifecycle.shutdown_date` with `_latest_date(read_path(...))`, exactly as today.
  - `.skips_predecessor(shutdown: datetime.date | None) -> bool` is true only when `shutdown is None` and `undated_predecessors == "skip"`.
  - `.query_filter(shutdown: datetime.date | None) -> list[dict]` combines the existing range-or-missing clause (with the missing arm dropped when `undated_candidates == "exclude"`) and the end-date clause below.
  - `.admits(pred: EntityDoc, cand: EntityDoc) -> bool` is the scorer's gate, the exact inverse of today's `_gap_outside_window` plus the same undated and end-date rules.
  - `.backward_window_days() -> float` returns `abs(min_gap_days)` when it is set and negative, else `DEFAULT_BACKWARD_WINDOW_DAYS = 180`.
- Schema additions:
  - `lifecycle.end_date`: optional string. The path on the candidate to its own end date.
  - `scoring.undated_candidates`: enum `keep` / `exclude`.
  - `scoring.undated_predecessors`: enum `unbounded` / `skip`.
  - Each description says why.
- End-date rule: when `lifecycle.end_date` is set and a shutdown is known, keep a candidate whose end date is missing or on or after the shutdown (`gte` the shutdown, rounded `||/d`). Inclusive bounds, as `_window_filter` already uses.
- `CandidateFinder.sort_fields()` also returns `lifecycle.end_date: "date"` when set, so the existing preflight refuses a keyword-mapped end date.
- `CandidateFinder.__init__` and `PairScorer.__init__` build `self.window = LifecycleWindow.from_config(lifecycle, scoring_config)` and stop reading `min_gap_days`/`max_gap_days` themselves.
- `PhaseEntityMatch._generate_actions` adds `stats["undated_predecessors"]`. A skipped predecessor gets no candidate search. The completion log line reports the count.

- [ ] **Step 1: Write the failing tests** in `tests/test_lifecycle_window.py`. Module docstring: retrieval and the scorer gate once each read the window separately; and an undated candidate kept by both sweeps would be emitted twice and counted twice in recall. Tests:

  - `test_dot_config_filter_unchanged`: `LifecycleWindow.from_config` on the shipped DOT `chameleon-detection` lifecycle and scoring returns a `query_filter(date(2021,1,1))` equal to the clause today's `_window_filter` builds. Copy the expected dict from the current code: range `gte 2020-07-05||/d`, `lte 2022-01-01||/d`, plus the must-not-exists arm.
  - `test_exclude_drops_missing_arm`: with `undated_candidates="exclude"`, the filter has no `must_not exists` arm, and `admits` is `False` for a candidate with no registration date.
  - `test_keep_admits_undated`: the default admits a candidate with no registration date.
  - `test_end_date_inclusive`: with `end_date="ended"` and shutdown 2021-01-01, `admits` is `True` for a candidate ending 2021-01-01 and `False` for one ending 2020-12-31. `query_filter` holds a clause equivalent to `ended gte 2021-01-01||/d OR ended missing`.
  - `test_filter_and_gate_agree`: for gaps `[-400,-181,-180,-1,0,1,365,366]` days and both undated settings, a candidate passes `admits` exactly when its registration date satisfies the `query_filter` bounds (evaluate the range in Python from the clause).
  - `test_skips_undated_predecessor`: `skips_predecessor(None)` is `True` only under `"skip"`.
  - `test_backward_window_from_min_gap`: `min_gap_days=-90` gives `90`; unset gives `180`.
  - `test_phase_counts_skipped_predecessor`: using the fakes pattern of `tests/test_scoring_error_rate.py`, a predecessor with no shutdown date under `"skip"` triggers no `finder.find` call and adds 1 to `stats["undated_predecessors"]`.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_lifecycle_window.py -v`. Expected: an import error for `matching.window`.

- [ ] **Step 3: Implement `matching/window.py`, then rewire `CandidateFinder`, `PairScorer` and the phase to use it.**
      Move today's `_window_filter` and `_gap_outside_window` logic into the class rather than duplicating it. Keep `_sort` in `candidates.py`, reading the shutdown via `self.window.shutdown`. Update the schema and `_declared_paths`.

- [ ] **Step 4: Run the new tests and the existing guards.**
      Run `.venv/bin/python -m pytest tests/test_lifecycle_window.py tests/test_candidate_retrieval_order.py tests/test_scorer.py tests/test_lifecycle.py tests/test_entity_match_schema.py tests/test_config_liveness.py -v`. Expected: PASS.

- [ ] **Step 5: Run the full suite and ruff, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add matching/window.py matching/candidates.py matching/scorer.py phase_providers/phase_entity_match.py schema/entity-match.schema.json utils/config_liveness.py tests/test_lifecycle_window.py
git commit -m "feat(matching): one LifecycleWindow for gap, undated and end-date rules"
```

### Task 2: The temporal signal reads its overlap from the window

**Files:**

- Modify: `matching/signals.py` (`TemporalSignal`, `build_signal`, the `BACKWARD_WINDOW_DAYS` and `BACKWARD_SCALE` constants)
- Modify: `matching/candidates.py`, `matching/scorer.py`, `phase_providers/phase_entity_match.py:772` (pass `window` to `build_signal`)
- Modify: `schema/entity-match.schema.json` (temporal signal: `before_shutdown_scale`, a number from 0 to 1)
- Test: `tests/test_lifecycle.py` (add cases; update the module docstring's last paragraph)

**Interfaces:**

- Consumes `LifecycleWindow.backward_window_days()` (Task 1).
- Produces `build_signal(config, lifecycle=None, window=None) -> Signal`. `TemporalSignal.__init__(config, lifecycle=None, window=None)`.
- The backward score is `max(0, (1 - min(1, |gap| / window.backward_window_days())) * scale)`, where `scale = getattr(config, "before_shutdown_scale", 0.5)`.
- With `window=None` (no scoring config, as in some unit tests), it uses `DEFAULT_BACKWARD_WINDOW_DAYS`.
- `BACKWARD_WINDOW_DAYS` is deleted. `BACKWARD_SCALE` is renamed `DEFAULT_BEFORE_SHUTDOWN_SCALE = 0.5`.

- [ ] **Step 1: Write the failing tests** in `tests/test_lifecycle.py`:

  - `test_backward_window_follows_configured_min_gap`: shutdown 2021-01-01, candidate opened 2020-11-02 (−60 days). With `min_gap_days=-90` the score is `(1 - 60/90) * 0.5`. With `-180` it is `(1 - 60/180) * 0.5`. Compare with `pytest.approx`.
  - `test_before_shutdown_scale_full_credit`: `before_shutdown_scale=1.0`, `min_gap_days=-180`, gap −1 day gives `pytest.approx(1 - 1/180)`.
  - `test_dot_scores_unchanged`: build the signal from the shipped DOT config and its window. A −90-day gap scores `0.25`, matching today's `(1 - 90/180) * 0.5`.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_lifecycle.py -v`. Expected: the first two fail (unexpected keyword or wrong value).

- [ ] **Step 3: Implement.** Thread `window` from all three `build_signal` call sites. Add `before_shutdown_scale` to the temporal signal's schema entry.

- [ ] **Step 4: Run the full suite and ruff.**
      Run `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`. Expected: PASS, `All checks passed!`.

- [ ] **Step 5: Commit.**

```bash
git add matching/signals.py matching/candidates.py matching/scorer.py phase_providers/phase_entity_match.py schema/entity-match.schema.json tests/test_lifecycle.py
git commit -m "feat(matching): temporal signal takes its overlap from the configured window"
```

### Task 3: Declared links and the tune/report split

**Files:**

- Create: `matching/declared.py`
- Modify: `schema/entity-match.schema.json` (`declared_links`)
- Modify: `utils/config_liveness.py` (`_declared_paths`: each rule's `field`)
- Modify: `phase_providers/phase_entity_match.py` (`handle` builds it; `_to_action` writes `declared` and `declared_by`)
- Test: `tests/test_declared_links.py`

**Interfaces:**

- Config shape:

```json
"declared_links": {
  "complete_truth": true,
  "rules": [
    {"name": "fdic-successor", "role": "truth", "kind": "key", "field": "NEWCERT"},
    {"name": "fdic-chain-end", "role": "explains", "kind": "key", "field": "ULTCERT"}
  ]
}
```

- Each rule:
  - `role` is `truth` or `explains`. `kind` is `key` or `shared`.
  - A `key` rule fires when a value at `field` on the predecessor equals the candidate's `entity_key`. With `"reverse": true`, it fires when a value at `field` on the candidate equals the predecessor's key (the GLEIF child case).
  - A `shared` rule fires when the two sides share a non-ignored value at `field`.
- Values compare as `str`. Values that `ScoringContext.is_ignored(field, value)` reports as ignored are skipped, so `ignore_values: {"NEWCERT": ["0"]}` silences the placeholder.
- Produces:
  - `DeclaredLinks.from_config(config) -> DeclaredLinks | None`.
  - `.classify(pred: EntityDoc, cand: EntityDoc, ctx: ScoringContext) -> tuple[str, str | None]`. Returns `("truth", rule_name)`, `("explains", rule_name)` or `("none", None)`. `truth` wins over `explains`, and rule order breaks ties within a role.
  - `.truth_targets(pred: EntityDoc, ctx) -> set[str]`, from forward `key` rules with role `truth`.
  - `.complete_truth: bool`.
  - `split_half(entity_key: str) -> str` (module function) returns `"tune"` when `hashlib.sha256(key.encode()).digest()[0]` is even, else `"report"`.
- Pair documents gain `declared` (one of the three strings) and, when not `none`, `declared_by` (the rule name).
- The pair index mapping (`index-mappings.json` of each sweep step) pins both as `keyword`. Task 7 does this for the FDIC sweeps.

- [ ] **Step 1: Write the failing tests** in `tests/test_declared_links.py`. Module docstring: a declared path that never fires makes every pair read as undeclared and recall read as zero, which looks like a bad matcher rather than bad configuration. Tests:

  - `test_key_rule_truth`: predecessor `{"NEWCERT": "990002"}`, candidate key `"990002"` gives `("truth", "fdic-successor")`.
  - `test_truth_beats_explains`: `NEWCERT` and `ULTCERT` both equal the candidate key gives `truth`.
  - `test_explains_chain`: only `ULTCERT` matches gives `("explains", "fdic-chain-end")`.
  - `test_ignored_value_never_fires`: `NEWCERT` `"0"` in `ignore_values` and a candidate keyed `"0"` gives `("none", None)`, and `truth_targets` is empty.
  - `test_reverse_key_rule`: a candidate whose `parent` field holds the predecessor's key fires a reverse rule.
  - `test_shared_rule`: both sides with `hc` `"77"` fire; both blank do not.
  - `test_split_half_stable`: assert the exact outputs for invented keys `"990005"`, `"990002"` and `"ZZZZ00INVENTEDLEI000"`. Compute them once with `hashlib` and pin the literals, then assert that about half of `range(10000)` goes each way (between 4,800 and 5,200).
  - `test_pair_document_carries_tag`: `PhaseEntityMatch._to_action` on a fake pair with a `DeclaredLinks` set writes `declared == "truth"` and `declared_by == "fdic-successor"`. With no `declared_links` configured, it writes neither key.
  - `test_liveness_reports_missing_rule_field`: `config_liveness` with a rule whose `field` is absent from the mapping reports it.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_declared_links.py -v`. Expected: an import error.

- [ ] **Step 3: Implement** `matching/declared.py`, the schema block, the liveness paths and the two pair-document keys.

- [ ] **Step 4: Run the full suite and ruff.**
      Run `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add matching/declared.py schema/entity-match.schema.json utils/config_liveness.py phase_providers/phase_entity_match.py tests/test_declared_links.py
git commit -m "feat(matching): tag pairs truth/explains/none from declared links"
```

### Task 4: Retrieval accounting and the "no truth retrieved" guard

**Files:**

- Modify: `phase_providers/phase_entity_match.py` (`_generate_actions`, `handle`, a new `_raise_if_no_truth_retrieved`)
- Test: `tests/test_declared_accounting.py`

**Interfaces:**

- Consumes `DeclaredLinks.truth_targets` (Task 3).
- `stats` gains three counters:
  - `truth_links`: truth targets of swept predecessors.
  - `truth_retrieved`: targets present among `cand_docs`.
  - `truth_emitted`: targets in the pairs yielded after the `max_pairs` cut.
- The completion log line reports all three.
- `handle` writes them to the pair index's `_meta.declared_accounting` through the same `put_mapping` call `_stamp_provenance` uses, so they survive the run.
- `_raise_if_no_truth_retrieved(stats)` raises `RuntimeError` when `truth_links > 0` and `truth_retrieved == 0`. The message names the `declared_links` fields and says the tag or the search cannot reach the targets.

- [ ] **Step 1: Write the failing tests** in `tests/test_declared_accounting.py`. Module docstring: a sweep whose truth targets are never retrieved produces recall that means nothing, and it used to look like a clean run. Use the `tests/test_scoring_error_rate.py` fake style. Tests:

  - `test_counts_retrieved_and_emitted`: one predecessor with truth target `"B"`, candidates `B` and `C`, both scored. Expect `truth_links == 1`, `truth_retrieved == 1`, `truth_emitted == 1`.
  - `test_cut_by_max_pairs_is_retrieved_not_emitted`: with `max_pairs=1`, `C` scores higher than `B`. Expect `truth_retrieved == 1` and `truth_emitted == 0`.
  - `test_no_truth_retrieved_raises`: `truth_links == 3`, `truth_retrieved == 0`. Expect `RuntimeError` whose message contains `"declared_links"`.
  - `test_no_declared_links_no_accounting`: with no `declared_links` configured, the counters stay 0 and nothing raises.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_declared_accounting.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run the full suite and ruff.**
      Run `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add phase_providers/phase_entity_match.py tests/test_declared_accounting.py
git commit -m "feat(entity-match): account for truth links and fail when none are retrieved"
```

### Task 5: `scripts/measure_declared_links.py`

**Files:**

- Create: `scripts/measure_declared_links.py`
- Create: `utils/declared_measure.py` (the pure computation, so it can be tested without a cluster)
- Test: `tests/test_measure_declared_links.py`

**Interfaces:**

- CLI:

```
.venv/bin/python scripts/measure_declared_links.py --project FDIC-Banks --step bank-absorption --index <pair index> --half tune|report [--gaps]
```

- It loads the step's `entity-match.json` and builds `PopulationSelector`, `CandidateFinder`, `LifecycleWindow` and `DeclaredLinks` from it.
- It walks the population, keeps predecessors whose `split_half` equals `--half`, and for each truth target:
  - Fetches the target by key from `source_index`. If missing, the exclusion is `target_missing_from_index`.
  - Tests it against `LifecycleWindow.admits`. If outside, the exclusion is `target_outside_window`. If undated and excluded, it is `target_undated`.
  - Otherwise it is in the denominator. Re-run `finder.find(pred_hit)`; the order is total, so it matches the sweep. A target absent from the candidates counts as `not_retrieved`.
  - Otherwise, look the pair up in `--index` by its `_id` (the `compute_id({"p":…,"s":…})` scheme). Absent means `retrieved_not_emitted`; present means emitted, with its `total_score`.
- `utils/declared_measure.py` holds `summarize(links: list[LinkOutcome], pairs: list[PairRow], complete_truth: bool, bands: tuple[float, ...] = (0.35, 0.5, 0.6, 0.7, 0.8, 0.9)) -> dict`.
  - `LinkOutcome` and `PairRow` are dataclasses: `LinkOutcome(pred_key, target_key, status, score)` with `status` one of `"emitted"`, `"not_retrieved"`, `"retrieved_not_emitted"`, `"target_missing_from_index"`, `"target_outside_window"`, `"target_undated"`; and `PairRow(pred_key, declared, score)`.
- Output is JSON to stdout:

```json
{
  "half": "...",
  "denominator": 0,
  "exclusions": { "...": 0 },
  "recall": 0.0,
  "recall_by_band": { "0.35": 0.0 },
  "misses": { "not_retrieved": 0, "retrieved_not_emitted": 0 },
  "precision_by_band": { "0.35": 0.0 },
  "explains_by_band": { "0.35": 0 },
  "gap_histogram": { "...": 0 }
}
```

- Restrict pairs to predecessors in the same half.
- Precision per band counts truth over truth plus none, leaving `explains` out, and is present only when `complete_truth`.
- `gap_histogram` appears with `--gaps`, binned in days `<-3650, -3650..-366, -365..-181, -180..-1, 0..365, >365`, from truth-link predecessor shutdown to target registration.
- The output holds no entity keys or names.

- [ ] **Step 1: Write the failing tests** in `tests/test_measure_declared_links.py`. Module docstring: a recall figure over links the sweep could never reach reads as matcher failure. Tests on `summarize`:

  - `test_exclusions_leave_denominator`: 2 emitted, 1 not retrieved, 1 missing from the index, 1 outside the window. Expect `denominator == 3`, `recall == pytest.approx(2/3)`, and every exclusion count printed.
  - `test_recall_by_band`: emitted scores `[0.4, 0.75]` give recall of 2/2 at 0.35 and 1/2 at 0.7.
  - `test_precision_only_when_complete`: with `complete_truth=False`, there is no `precision_by_band` key. With `True` and pairs `truth@0.8, none@0.8, explains@0.8`, precision at 0.7 is 0.5.
  - `test_output_has_no_keys`: `json.dumps(summarize(...))` contains none of the predecessor or target keys used.
  - `test_denominators_partition`: for the same links, the reappearance and absorption windows (built from FDIC's two sweep configs, Task 7) put each dated target in exactly one denominator.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_measure_declared_links.py -v`. Expected: FAIL. (`test_denominators_partition` is skipped until Task 7 creates the configs; mark it `skipif` on the config file's absence.)

- [ ] **Step 3: Implement** `utils/declared_measure.py` and the script. Read every pair by `scan` with explicit keyword arguments.

- [ ] **Step 4: Run the full suite and ruff.**
      Run `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add scripts/measure_declared_links.py utils/declared_measure.py tests/test_measure_declared_links.py
git commit -m "feat(scripts): measure recall and precision against declared links"
```

### Task 6: FDIC-Banks: fetch, profile and load institutions

**Files:**

- Create: `FDIC-Banks/fetch_fdic.py`
- Create: `FDIC-Banks/configuration.json`
- Create: `FDIC-Banks/configuration/institutions-ingestion-setup/pipelines.json`
- Create: `FDIC-Banks/configuration/institutions/{index-config,index-mappings,index-settings}.json`
- Create: `FDIC-Banks/README.md` (start it with the profile figures)
- Test: `tests/test_fdic_fetch.py`

**Interfaces:**

- `fetch_fdic.py`:
  - `fetch(endpoint: str, fields: list[str], dest: Path, page_size: int = 10000) -> int`. It pages `https://api.fdic.gov/banks/<endpoint>?format=csv&limit=…&offset=…&fields=…` until a short page, writes one CSV with a single header, and returns the row count. The limit of 10,000 per page was verified 2026-11-26.
  - It sets a generic `User-Agent`, uses no key, and treats a non-200 response or an HTML body as an error, so the CMS 404-page incident cannot recur.
  - It refuses to leave a file under 50 lines in place (the CMS plausibility floor).
  - `--dataset institutions|history`.
- Institution fields fetched: `CERT, NAME, PRIORNAME1, ADDRESS, ADDRESS2, CITY, STALP, ZIP, WEBADDR, ESTYMD, ENDEFYMD, ACTIVE, INACTIVE, NEWCERT, ULTCERT, CHANGEC1, FED_RSSD`. Add a holding-company field only if profiling finds one.
- History fields fetched: `OUT_CERT, SUR_CERT, ACQ_CERT, CHANGECODE, CHANGECODE_DESC, EFFDATE`.
- Data paths: `data/institutions/institutions.csv` and `data/history/history.csv`. History is never loaded; it is read only by the coverage check in Step 5.
- Pipeline `institutions-pipeline-000001` converts `ESTYMD` and `ENDEFYMD` from `MM/dd/yyyy` to ISO with a `date` processor (`formats: ["MM/dd/yyyy"]`, `target_field` the same field, `output_format: "yyyy-MM-dd"`). Each processor has an `on_failure` that removes the field, so a bad date drops a value, not a bank (README hazard 2).
- Mappings:
  - `CERT`, `NEWCERT`, `ULTCERT`, `FED_RSSD`, `STALP`, `ZIP`, `ACTIVE`, `INACTIVE`, `CHANGEC1`: `keyword`.
  - `ESTYMD`, `ENDEFYMD`: `date` with ISO format only.
  - `NAME`, `PRIORNAME1`: `text` with `phonetic`, `phonetic_bm` and `clean` subfields.
  - `ADDRESS`: `text` with `clean` and `tokens` subfields.
  - `WEBADDR`: `keyword` plus a normalized subfield.
  - Copy analyzer definitions from `DOT-Commercial/configuration/carriers/index-settings.json` and change the stop list in Task 7.
- `index-config.json`: `id_field: "CERT"`, `num_rows: null`.

- [ ] **Step 1: Write the failing tests** in `tests/test_fdic_fetch.py`. The module docstring cites the CMS 404-page and stub incidents. Using a fake HTTP opener:

  - `test_pages_until_short_page`: pages of 3, 3 and 1 rows give a returned count of 7 and one header line.
  - `test_html_body_raises`: a body starting with `<` raises `RuntimeError`.
  - `test_stub_not_kept`: a 10-line result raises and leaves no file at `dest`.

- [ ] **Step 2: Run them to make sure they fail, implement the fetch script, then run them to make sure they pass.**
      Run `.venv/bin/python -m pytest tests/test_fdic_fetch.py -v`.

- [ ] **Step 3: Fetch and profile.**

```bash
.venv/bin/python FDIC-Banks/fetch_fdic.py --dataset institutions
.venv/bin/python FDIC-Banks/fetch_fdic.py --dataset history
.venv/bin/python scripts/profile_dataset.py FDIC-Banks/data/institutions/institutions.csv --key CERT
```

Expected:

- About 27,800 rows. The API reported 27,838 institutions and 23,634 inactive on 2026-11-26.
- `CERT` is unique.
- `NEWCERT` holds no `.0` suffix. If it does, normalize it in the fetch script and add a test.

Record in the README: distinct counts, blank rates for `ESTYMD`, `ENDEFYMD`, `NEWCERT` and `WEBADDR`, the head of the `NEWCERT` distribution (expect `0` as the placeholder), and the `CHANGEC1` distribution among inactive rows.

- [ ] **Step 4: Check whether `NEWCERT` covers the history.**
      This is a one-off command, not committed code. Read `history.csv` and keep succession events: change codes whose FDIC description is a merger, a consolidation, a failure with an acquirer, or a purchase and assumption. List the codes used in the README.
      Count events whose `OUT_CERT` row in `institutions.csv` has `NEWCERT` equal to `SUR_CERT` or `ACQ_CERT`.

  - **At least 95%:** record the figure and continue.
  - **Below 95%:** stop and report back with the figure. Building an enrich policy from history is a design change.

- [ ] **Step 5: Configure, check coverage, and load.**

```bash
.venv/bin/python scripts/check_mapping_coverage.py --project=FDIC-Banks --step=institutions
.venv/bin/python execute_project.py --project=FDIC-Banks --step=institutions-ingestion-setup
.venv/bin/python execute_project.py --project=FDIC-Banks --step=institutions
curl -s "localhost:9200/institutions-000001/_count"
curl -s "localhost:9200/institutions-000001/_mapping/field/ENDEFYMD"
```

Expected:

- The coverage check reports no unpinned columns.
- The loader logs `0 of N documents failed`.
- `_count` equals the CSV row count.
- `ENDEFYMD` is `date`.
- A `_search` for inactive institutions with `ENDEFYMD` missing returns a count. Record it: these are the predecessors `undated_predecessors: "skip"` will skip.

- [ ] **Step 6: Run the full suite, ruff and prettier, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check FDIC-Banks
git add FDIC-Banks tests/test_fdic_fetch.py
git commit -m "feat(FDIC-Banks): fetch, profile and load FDIC institutions"
```

### Task 7: FDIC-Banks: the two sweeps and the window-pair guard

**Files:**

- Create: `FDIC-Banks/configuration/bank-reappearance/{entity-match,index-config,index-mappings,metrics}.json`
- Create: `FDIC-Banks/configuration/bank-absorption/{entity-match,index-config,index-mappings,metrics}.json`
- Modify: `FDIC-Banks/configuration.json` (two sweep steps: `index-create`, `index-map`, `validate`, `entity-match`)
- Modify: `FDIC-Banks/configuration/institutions/index-settings.json` (the measured stop list)
- Test: `tests/test_succession_window_pairs.py`

**Interfaces:**

- Both sweeps share:
  - `source_index: "institutions-000001"` and `entity.key: "CERT"`.
  - `lifecycle`: `{shutdown_date: "ENDEFYMD", registration_date: "ESTYMD", end_date: "ENDEFYMD"}`.
  - Population selector `{"term": {"INACTIVE": "1"}}`.
  - `scoring.undated_predecessors: "skip"`.
  - The `declared_links` block from Task 3, with `complete_truth: true`.
  - `ignore_values: {"NEWCERT": ["0"], "ULTCERT": ["0"]}`, plus any placeholders the profile found.
  - If Task 6 found a holding-company field, a third rule `{"name": "fdic-holding-company", "role": "explains", "kind": "shared", "field": <that field>}`. If it found none, the README records that same-holding-company pairs count against precision, as a known caveat.
  - Signals: `name-phonetic` (NAME, PRIORNAME1, `cross_field`), `name-token` (clean), `address` (`state_field: "STALP"`), and `exact-identifier` with `text_fields: ["WEBADDR"]`.
- `bank-reappearance`: `scoring.min_gap_days: -180`, `max_gap_days: 365`, `undated_candidates: "exclude"`, plus a `temporal` signal with `max_gap_days: 365` and `before_shutdown_scale: 1.0`.
- `bank-absorption`: `scoring.max_gap_days: -181`, no `min_gap_days`, `undated_candidates: "keep"`, no `temporal` signal.
- Starting weights and guards are copied from DOT's `chameleon-detection` and renormalized over the signals present. Task 8 tunes them.
- 180 is the starting `overlap_days`. Task 8 sets it from the gap histogram.
- Pair index mappings pin `declared` and `declared_by` as `keyword`.
- `metrics.json` holds `pairs`, `pairs_ge_070`, `pairs_ge_090`, and identical-name and identical-address counts. It uses no chameleon vocabulary (`canary`, `triage`).

- [ ] **Step 1: Write the failing test** `tests/test_succession_window_pairs.py`. Module docstring: two sweep configs edited apart can overlap, so a pair is counted twice, or leave a gap, so a succession is in neither. The test lists `PAIRS = [("FDIC-Banks", "bank-reappearance", "bank-absorption")]` and asserts for each:

  - `absorption.scoring.max_gap_days == reappearance.scoring.min_gap_days - 1`;
  - absorption has no `min_gap_days`;
  - `reappearance.scoring.undated_candidates == "exclude"` and absorption's is `"keep"`;
  - identical `lifecycle`, `population` and `declared_links` blocks;
  - the reappearance temporal signal's `before_shutdown_scale == 1.0`, and absorption has no temporal signal.

- [ ] **Step 2: Run it to make sure it fails, write the configs, then run it to make sure it passes, along with Task 5's `test_denominators_partition`.**
      Run `.venv/bin/python -m pytest tests/test_succession_window_pairs.py tests/test_measure_declared_links.py -v`.

- [ ] **Step 3: Measure the stop list.**
      Run `compare_phonetic_encoders.py` on `NAME` and count the share of names holding each word. Stop every word that appears in more than about 1% of names: expect `BANK`, `NATIONAL`, `TRUST`, `SAVINGS`, `STATE`, `COMPANY` and the like. Record the percentages in the README, then reload `institutions`.

- [ ] **Step 4: Run both sweeps.**

```bash
.venv/bin/python execute_project.py --project=FDIC-Banks --step=bank-reappearance
.venv/bin/python execute_project.py --project=FDIC-Banks --step=bank-absorption
```

Expected:

- Each completion line shows a non-zero `truth_links`, a non-zero `truth_retrieved`, and the `undated_predecessors` count from Task 6.
- Neither run raises.

- [ ] **Step 5: Run the full suite, ruff and prettier, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check FDIC-Banks
git add FDIC-Banks tests/test_succession_window_pairs.py
git commit -m "feat(FDIC-Banks): reappearance and absorption sweeps with a shared window"
```

### Task 8: Tune on the tuning half, measure the report half, record the results

**Files:**

- Modify: `FDIC-Banks/configuration/bank-*/entity-match.json` (tuned values)
- Modify: `FDIC-Banks/README.md`

- [ ] **Step 1: Look at the gaps.**
      Run `.venv/bin/python scripts/measure_declared_links.py --project FDIC-Banks --step bank-absorption --index <alias> --half tune --gaps`.

  - If the histogram shows a valley between reappearance-shaped and absorption-shaped truth links, set `overlap_days` there in both configs. The Task 7 guard keeps them paired.
  - Otherwise keep 180 and say in the README that FDIC has too few reappearance-shaped links to show a valley.

- [ ] **Step 2: Tune.**
      Iterate on the `tune` half only: weights, `min_total_score`, `max_candidates` and seed signals. After each change, re-run the sweep and the script on `--half tune`, and compare pairs by id with `scripts/compare_pair_ids.py`. Stop when changes stop moving tune-half recall at 0.70 or above without lowering precision there.

- [ ] **Step 3: Check the "FDIC records every succession" assumption.**
      Read the 30 highest-scoring `declared: none` pairs from each sweep locally. Count how many are real successions missing from FDIC's record and how many are holding-company affiliates. Record only the counts, with no names.
      If more than 5 of 30 are real successions, report precision as a lower bound and say so.

- [ ] **Step 4: Measure the report half.**
      Run the script with `--half report` for both sweeps. Paste both JSON outputs into the README under "Measured against FDIC's recorded successions", dated, with the command lines. Write a short reading of what it shows: recall by band, the not-retrieved versus not-emitted split, precision by band, and how absorption compares with reappearance.

- [ ] **Step 5: Run prettier and the anonymization check, then commit.**
      Run the `anonymization-check` skill on the README diff, then:

```bash
npx prettier@3.3.3 --check FDIC-Banks/README.md
git add FDIC-Banks
git commit -m "docs(FDIC-Banks): tuned windows and measured recall and precision"
```

### Task 9: Documentation, the naming rule, and the guard tables

**Files:**

- Modify: `CLAUDE.md` (the naming section: add the declared-succession case, worded as in the spec's Naming section)
- Modify: `.claude/skills/anonymization-check/SKILL.md` (the `truth`/`explains`/`none` distinction: truth pairs may be named, `none` pairs never)
- Modify: `README.md`:
  - Glossary: "Declared link", "Truth link", "Explains link", "Overlap window", "Absorption sweep".
  - "Inside `entity-match`": the window object and declared links.
  - Reference implementations table: an `FDIC-Banks` row.
  - Hazards / open items: name each new guard test.
- Modify: `docs/adding-a-dataset.md` (a short section "If your data records successions, use them as known matches", pointing at the script and the tune/report split)
- Modify: `.claude/skills/run-entitopia/SKILL.md` (FDIC-Banks steps; re-run table rows for the two sweeps: same-day generations apply, and `_meta.declared_accounting` is per index)
- Modify: `schema/README.md` (new keys)

- [ ] **Step 1: Write the documentation changes.**
      Every guard named in the README names its test: `test_lifecycle_window.py`, `test_lifecycle.py`, `test_declared_links.py`, `test_declared_accounting.py`, `test_measure_declared_links.py`, `test_succession_window_pairs.py` and `test_fdic_fetch.py`.

- [ ] **Step 2: Run prettier on every changed Markdown file, then run the anonymization check.**

```bash
npx prettier@3.3.3 --check CLAUDE.md README.md docs/adding-a-dataset.md schema/README.md .claude/skills/anonymization-check/SKILL.md .claude/skills/run-entitopia/SKILL.md
```

- [ ] **Step 3: Run the full suite and ruff, since the skill files sit beside configs tests load. Then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add CLAUDE.md README.md docs/adding-a-dataset.md schema/README.md .claude/skills
git commit -m "docs: declared-link measurement, FDIC-Banks, and the declared-succession naming rule"
```

## After this plan

Write `docs/superpowers/plans/<date>-third-dataset-gleif.md` from the same spec. It will cover:

- The GLEIF Level 1 and Level 2 downloads.
- The candidate filter (spec framework change 2), if profiling shows funds flood candidates.
- Parent, child and shared-ultimate-parent `explains` rules.
- The US-legal-address population.
- Band-based findings with hand-reviewed samples.

It reuses everything above unchanged.
