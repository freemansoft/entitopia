# DOT-Commercial Inspections-Per-Unit (VIN Data) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `inspections-per-unit` dataset (FMCSA `wt8s-2hbx`) as a fourth DOT-Commercial fetch/index target, enrich it onto `inspections` via a new ES enrich policy/pipeline, and fix the pre-existing `crashes.dot_number` float/long mapping bug that makes `crashes-enrichment-into-carriers` return zero matches.

**Architecture:** Reuses the existing config-driven phase pipeline (`execute_project.py` + `phase_providers/*`) unchanged — no Python code changes anywhere in this plan, only new/edited JSON config files under `DOT-Commercial/configuration/` plus one new step (`inspections-ingestion-setup`) inserted into `DOT-Commercial/configuration.json`'s step order: `crashes-ingestion-setup` → `crashes` → `inspections-per-unit` → `inspections-ingestion-setup` → `inspections` → `carriers-ingestion-setup` → `carriers`.

**Tech Stack:** Python 3.12 (`.venv`), Elasticsearch (local, already running at `localhost:9200`), Socrata Open Data API (`data.transportation.gov`), pandas/elasticsearch-py via `execute_project.py`.

## Global Constraints

- No unit test suite exists in this repo (confirmed: zero `test_*.py`/`*_test.py`/`conftest.py` files anywhere). Verification for every task is done against the **real local Elasticsearch cluster** (`localhost:9200`, confirmed reachable) and, where noted, the real Socrata API — matching this project's existing convention (see `DOT-Commercial/README.md`'s validation-by-live-query style and the project's `superpowers/specs/2026-07-28-dot-commercial-inspections-per-unit-design.md`).
- **Reuse already-downloaded data; keep validation windows small.** `DOT-Commercial/data/{crashes,inspections,carriers}/*.csv` already exist on disk at full production scale (crashes 160MB/333,186 rows, inspections 2.4GB/5,647,567 rows, carriers 720MB/2,084,753 rows) and are already indexed in the local ES cluster as of 2026-07-27. **Never re-fetch these three** in this plan — only `inspections-per-unit` needs a fresh fetch, and it must use a small temporary validation window (not the shipped 24-month default) to avoid a multi-million-row pull during iteration. Only Task 6's final regression run touches full-scale data, and even then only re-populates (no re-fetch).
- The shipped `window_months: 24` in `fetch-config.json` (matching crashes/inspections) must never be permanently changed to a smaller value — only the ad hoc validation fetch in Task 1 uses a smaller window, and that override is never committed.
- All `execute_project.py` invocations run from the worktree root (`/Users/joefreemanjoe/Documents/entitopia/.claude/worktrees/dot-commercial-socrata-migration`) using `.venv/bin/python3`. All `fetch_commercial_carriers.py` invocations run with cwd = `DOT-Commercial/` (it loads `configuration/fetch-config.json` as a path relative to cwd).
- Each daily run creates a **new dated index** (e.g. `crashes-2026.07.28-000001`) rather than mutating the prior day's index — confirmed via `utils/elasticsearch_utils.py`'s `replace_index_with_now_version` (`{now/d}` → `datetime.now().strftime('%Y.%m.%d')`). Verification curl commands must target the concrete dated index name from `_cat/indices?v`, not the alias, since put_alias only adds (never atomically swaps) — querying by alias while both a yesterday and today index exist would double-count.
- Commit after every task. Do not wait for user confirmation between tasks (explicit instruction from the user).

---

## File Structure

New files:
- `DOT-Commercial/configuration/inspections-per-unit/index-config.json`
- `DOT-Commercial/configuration/inspections-per-unit/index-mappings.json`
- `DOT-Commercial/configuration/inspections-per-unit/index-settings.json`
- `DOT-Commercial/configuration/inspections-ingestion-setup/enrichment-policies.json`
- `DOT-Commercial/configuration/inspections-ingestion-setup/pipelines.json`
- `DOT-Commercial/configuration/inspections/index-mappings.json`
- `DOT-Commercial/configuration/crashes/index-mappings.json`

Modified files:
- `DOT-Commercial/configuration/fetch-config.json` — new `inspections_per_unit` dataset entry
- `DOT-Commercial/configuration/inspections/index-config.json` — add `pipeline` field
- `DOT-Commercial/configuration.json` — insert `inspections-per-unit` and `inspections-ingestion-setup` steps in order
- `DOT-Commercial/README.md` — step list reflects the new 7-step pipeline
- `README.md` (repo root) — move the `crashes.dot_number` bug from Open to Closed work items

---

### Task 1: Fetch config entry + validation sample for `inspections-per-unit`

**Files:**
- Modify: `DOT-Commercial/configuration/fetch-config.json`

**Interfaces:**
- Produces: `DOT-Commercial/data/inspections-per-unit/inspections_per_unit.csv` (validation-scale sample on disk, used by Task 2) and the committed `inspections_per_unit` entry in `fetch-config.json` with `window_months: 24` (production value).

- [ ] **Step 1: Add the dataset entry**

Edit `DOT-Commercial/configuration/fetch-config.json`, adding to `"datasets"` (after `"inspections"`):

```json
        "inspections_per_unit": {
            "dataset_id": "wt8s-2hbx",
            "output": "data/inspections-per-unit/inspections_per_unit.csv",
            "date_field": "change_date",
            "window_months": 24
        }
```

- [ ] **Step 2: Temporarily narrow the window for a fast validation fetch**

Edit the same file, changing only `"window_months": 24` to `"window_months": 1` for `inspections_per_unit` (temporary — reverted in Step 5). This keeps the validation pull small since the parent table has 13,659,659 rows total.

- [ ] **Step 3: Run the fetch**

```bash
cd DOT-Commercial && ../.venv/bin/python3 fetch_commercial_carriers.py --dataset=inspections_per_unit
cd ..
```

Expected: log line `Fetched N rows for inspections_per_unit` with N in the thousands-to-low-tens-of-thousands range (not millions), and `DOT-Commercial/data/inspections-per-unit/inspections_per_unit.csv` exists.

- [ ] **Step 4: Confirm the window was respected**

```bash
.venv/bin/python3 -c "
import pandas as pd
df = pd.read_csv('DOT-Commercial/data/inspections-per-unit/inspections_per_unit.csv')
print('rows:', len(df))
print('min change_date:', df['change_date'].astype(str).min())
print('max change_date:', df['change_date'].astype(str).max())
print('null VIN count:', df['insp_unit_vehicle_id_number'].isna().sum())
"
```

Expected: `min change_date` is within the last ~35 days (1-month window, 30-day-per-month approximation per `compute_where_clause`), and the columns match the design spec's confirmed schema (`change_date`, `inspection_id`, `insp_unit_id`, `insp_unit_type_id`, `insp_unit_number`, `insp_unit_make`, `insp_unit_company`, `insp_unit_license`, `insp_unit_license_state`, `insp_unit_vehicle_id_number`, `insp_unit_decal`, `insp_unit_decal_number`).

- [ ] **Step 5: Revert the window to the production value**

Edit `DOT-Commercial/configuration/fetch-config.json` back to `"window_months": 24` for `inspections_per_unit`. The already-fetched small CSV stays on disk (gitignored under `*/data/`) and is reused by Task 2 — do not re-fetch.

- [ ] **Step 6: Commit**

```bash
git add DOT-Commercial/configuration/fetch-config.json
git commit -m "Add inspections_per_unit dataset to fetch-config.json"
```

---

### Task 2: `inspections-per-unit` index (config, mappings, settings) + populate

**Files:**
- Create: `DOT-Commercial/configuration/inspections-per-unit/index-config.json`
- Create: `DOT-Commercial/configuration/inspections-per-unit/index-mappings.json`
- Create: `DOT-Commercial/configuration/inspections-per-unit/index-settings.json`
- Modify: `DOT-Commercial/configuration.json`

**Interfaces:**
- Consumes: `DOT-Commercial/data/inspections-per-unit/inspections_per_unit.csv` from Task 1.
- Produces: a populated `inspections-per-unit-YYYY.MM.DD-000001` ES index with `insp_unit_id` as `_id`, consumed by Task 3's enrichment policy (`match_field: inspection_id`).

- [ ] **Step 1: Create `index-config.json`**

```json
{
    "index": "inspections-per-unit-{now/d}-000001",
    "alias": "inspections-per-unit-000001",
    "source": "inspections_per_unit.csv",
    "id_field": "insp_unit_id",
    "num_rows": null,
    "skip_rows": 0
}
```

- [ ] **Step 2: Create `index-mappings.json`**

```json
{
    "index": "inspections-per-unit-{now/d}-000001",
    "mappings": {
        "properties": {
            "inspection_id": {
                "type": "long"
            },
            "insp_unit_id": {
                "type": "keyword"
            },
            "insp_unit_type_id": {
                "type": "keyword"
            },
            "insp_unit_number": {
                "type": "keyword"
            },
            "insp_unit_make": {
                "type": "keyword"
            },
            "insp_unit_company": {
                "type": "keyword"
            },
            "insp_unit_license": {
                "type": "keyword"
            },
            "insp_unit_license_state": {
                "type": "keyword"
            },
            "insp_unit_vehicle_id_number": {
                "type": "keyword"
            },
            "insp_unit_decal": {
                "type": "keyword"
            },
            "insp_unit_decal_number": {
                "type": "keyword"
            },
            "change_date": {
                "type": "keyword"
            }
        }
    }
}
```

`insp_unit_vehicle_id_number` (VIN) is `keyword` because it's an exact-match lookup field, not full text — matching the design spec's rationale.

- [ ] **Step 3: Create `index-settings.json`**

```json
{
    "index": "inspections-per-unit-{now/d}-000001",
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 1
        }
    }
}
```

No custom analyzers — VIN/plate/company lookups are exact-match, unlike carriers' fuzzy name/address fields.

- [ ] **Step 4: Insert the step into `configuration.json`**

Edit `DOT-Commercial/configuration.json`, inserting this object into `"steps"` immediately before the existing `"inspections"` step entry:

```json
        {
            "name": "inspections-per-unit",
            "phases": [
                "index-create",
                "index-map",
                "index-populate"
            ]
        },
```

- [ ] **Step 5: Run the step**

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=inspections-per-unit
```

- [ ] **Step 6: Verify against the real cluster**

```bash
curl -s "http://localhost:9200/_cat/indices/inspections-per-unit-*?v"
```

Expected: one index `inspections-per-unit-2026.07.28-000001` with `docs.count` matching Task 1's fetched row count.

```bash
curl -s "http://localhost:9200/inspections-per-unit-2026.07.28-000001/_mapping?pretty" | grep -A2 '"insp_unit_vehicle_id_number"\|"inspection_id"'
```

Expected: `insp_unit_vehicle_id_number` → `"type": "keyword"`, `inspection_id` → `"type": "long"` (not dynamically inferred).

```bash
curl -s "http://localhost:9200/inspections-per-unit-2026.07.28-000001/_search?size=2&pretty"
```

Expected: two real documents with populated `insp_unit_vehicle_id_number`, `insp_unit_make`, etc.

- [ ] **Step 7: Commit**

```bash
git add DOT-Commercial/configuration/inspections-per-unit DOT-Commercial/configuration.json
git commit -m "Add inspections-per-unit index (config, mappings, settings)"
```

---

### Task 3: `inspections-ingestion-setup` enrich policy + pipeline

**Files:**
- Create: `DOT-Commercial/configuration/inspections-ingestion-setup/enrichment-policies.json`
- Create: `DOT-Commercial/configuration/inspections-ingestion-setup/pipelines.json`
- Modify: `DOT-Commercial/configuration.json`

**Interfaces:**
- Consumes: the populated `inspections-per-unit-{now/d}-000001` index from Task 2.
- Produces: enrich policy `inspections-per-unit-enrichment-policy` and ingest pipeline `inspections-pipeline-000001` (target field `units`, `max_matches: 10`), consumed by Task 4 via `inspections/index-config.json`'s `pipeline` field.

- [ ] **Step 1: Create `enrichment-policies.json`**

```json
[
    {
        "name": "inspections-per-unit-enrichment-policy",
        "match": {
            "indices": "inspections-per-unit-{now/d}-000001",
            "match_field": "inspection_id",
            "enrich_fields": [
                "inspection_id",
                "insp_unit_vehicle_id_number",
                "insp_unit_make",
                "insp_unit_company",
                "insp_unit_license",
                "insp_unit_license_state",
                "insp_unit_type_id"
            ]
        }
    }
]
```

- [ ] **Step 2: Create `pipelines.json`**

```json
{
    "name": "inspections-pipeline-000001",
    "processors": [
        {
            "enrich": {
                "description": "slipstream unit-level VIN/vehicle data",
                "policy_name": "inspections-per-unit-enrichment-policy",
                "field": "inspection_id",
                "target_field": "units",
                "max_matches": "10"
            }
        }
    ]
}
```

- [ ] **Step 3: Insert the step into `configuration.json`**

Edit `DOT-Commercial/configuration.json`, inserting this object into `"steps"` immediately after `"inspections-per-unit"` and before `"inspections"`:

```json
        {
            "name": "inspections-ingestion-setup",
            "phases": [
                "enrichment-policies",
                "pipelines"
            ]
        },
```

- [ ] **Step 4: Run the step**

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=inspections-ingestion-setup
```

- [ ] **Step 5: Verify against the real cluster**

```bash
curl -s "http://localhost:9200/_enrich/policy/inspections-per-unit-enrichment-policy?pretty"
```

Expected: policy exists with `match_field: inspection_id` and the 7 `enrich_fields` listed above.

```bash
curl -s "http://localhost:9200/_ingest/pipeline/inspections-pipeline-000001?pretty"
```

Expected: pipeline exists with one `enrich` processor, `target_field: units`, `max_matches: "10"`.

- [ ] **Step 6: Commit**

```bash
git add DOT-Commercial/configuration/inspections-ingestion-setup DOT-Commercial/configuration.json
git commit -m "Add inspections-ingestion-setup enrich policy and pipeline"
```

---

### Task 4: Wire the pipeline onto `inspections` + explicit `long` mapping

**Files:**
- Modify: `DOT-Commercial/configuration/inspections/index-config.json`
- Create: `DOT-Commercial/configuration/inspections/index-mappings.json`

**Interfaces:**
- Consumes: `inspections-pipeline-000001` from Task 3.
- Produces: `inspections` documents carrying a populated `units` array (real VIN data) after `index-populate`, and explicit `long` typing for `dot_number`/`inspection_id` on the `inspections` index (defensive, since it's now also an enrich match target for `carriers`).

- [ ] **Step 1: Add the pipeline to `index-config.json`**

Edit `DOT-Commercial/configuration/inspections/index-config.json`, adding `"pipeline": "inspections-pipeline-000001"`:

```json
{
    "alias": "inspections-000001",
    "index": "inspections-{now/d}-000001",
    "source": "inspections.csv",
    "id_field": "inspection_id",
    "pipeline": "inspections-pipeline-000001",
    "num_rows": null,
    "skip_rows": 0
}
```

- [ ] **Step 2: Create `index-mappings.json`**

```json
{
    "index": "inspections-{now/d}-000001",
    "mappings": {
        "properties": {
            "dot_number": {
                "type": "long"
            },
            "inspection_id": {
                "type": "long"
            }
        }
    }
}
```

- [ ] **Step 3: Temporarily reduce `num_rows` for a fast validation run**

`DOT-Commercial/data/inspections/inspections.csv` is 2.4GB / 5,647,567 rows — too slow for iterative validation. Edit `DOT-Commercial/configuration/inspections/index-config.json`, changing `"num_rows": null` to `"num_rows": 5000` (temporary — reverted in Step 5).

- [ ] **Step 4: Run the step**

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=inspections
```

This creates a fresh `inspections-2026.07.28-000001` index (today's date differs from the existing `inspections-2026.07.27-000001`), so `put_mapping` applies cleanly to a brand-new index rather than retyping an already-mapped field.

- [ ] **Step 5: Revert `num_rows` to `null`**

Edit `DOT-Commercial/configuration/inspections/index-config.json` back to `"num_rows": null`.

- [ ] **Step 6: Verify against the real cluster**

```bash
curl -s "http://localhost:9200/_cat/indices/inspections-2026.07.28-000001?v"
curl -s "http://localhost:9200/inspections-2026.07.28-000001/_mapping?pretty" | grep -A2 '"dot_number"\|"inspection_id"'
```

Expected: `docs.count` = 5000; both `dot_number` and `inspection_id` mapped `"type": "long"`.

```bash
curl -s "http://localhost:9200/inspections-2026.07.28-000001/_search?q=_exists_:units&size=3&pretty"
```

Expected: at least one hit with a non-empty `units` array containing a real `insp_unit_vehicle_id_number`. (If zero hits: the 5000-row validation sample's `inspection_id`s may not overlap Task 1's narrow 1-month `inspections-per-unit` sample — this is expected and not a bug, since the two datasets were fetched with independent windows; note it and proceed, since Task 6's full run resolves any true overlap gap.)

- [ ] **Step 7: Commit**

```bash
git add DOT-Commercial/configuration/inspections/index-config.json DOT-Commercial/configuration/inspections/index-mappings.json
git commit -m "Wire inspections-per-unit enrichment pipeline onto inspections index"
```

---

### Task 5: Fix `crashes.dot_number` mapping bug + validate carriers enrichment

> **Revised after a BLOCKED report from the first implementation attempt.** The
> original plan (and the design spec it was based on) assumed an explicit ES
> field-type mapping on the `crashes` index alone would fix the bug. It does
> not: Elasticsearch `match`-type enrich policies build a separate internal
> index by copying each document's raw `_source` verbatim and always indexing
> the match field as `keyword` — mapping coercion on the *live* index changes
> how the field is searched there, but does not rewrite `_source`, and the
> enrich index is built from `_source`, not from the live index's coerced
> doc-values. Since pandas writes `crashes.csv`'s `dot_number` as a float
> (`"2975796.0"`) due to ~19.5% null values in that column, that literal
> string — not `"2975796"` — is what lands in `_source` and therefore in the
> enrich index's keyword field, so it never term-matches carriers' clean
> integer `dot_number` (`"2975796"`). Confirmed live via direct term queries
> during the first attempt. The real fix has to rewrite the JSON value itself
> before `_source` is stored, which an ingest pipeline processor does (it runs
> before indexing) but a mapping type alone cannot.

**Files:**
- Create: `DOT-Commercial/configuration/crashes/index-mappings.json` (still correct and worth keeping — defensive/explicit typing for search-time behavior, consistent with the rest of this plan — but insufficient alone)
- Modify: `DOT-Commercial/configuration/crashes-ingestion-setup/pipelines.json` (add a `convert` processor to the existing `crashes-pipeline-000001`, which already runs a `fingerprint` processor at `index-populate` time)

**Interfaces:**
- Produces: `crashes` documents whose `_source.dot_number` is a real JSON integer (or `null`) after ingestion, plus an explicit `long` mapping — together closing the pre-existing bug documented in the repo-root `README.md`'s Open Work Items (`crashes-enrichment-into-carriers currently returns zero matches`).

- [ ] **Step 1: Create `index-mappings.json`**

```json
{
    "index": "crashes-{now/d}-000001",
    "mappings": {
        "properties": {
            "dot_number": {
                "type": "long"
            }
        }
    }
}
```

- [ ] **Step 2: Add a `convert` processor to the existing `crashes-pipeline-000001`**

Edit `DOT-Commercial/configuration/crashes-ingestion-setup/pipelines.json`, adding a `convert` processor before the existing `fingerprint` processor:

```json
{
    "name": "crashes-pipeline-000001",
    "processors": [
        {
            "convert": {
                "description": "Coerce dot_number to a real JSON integer in _source (not just the live index's search-time type) so the crashes-enrichment-policy's internal enrich index — which copies _source verbatim and always indexes its match field as keyword — stores a clean '2975796' instead of a float-suffixed '2975796.0' that would never term-match carriers' integer dot_number",
                "field": "dot_number",
                "type": "long",
                "ignore_missing": true,
                "ignore_failure": true
            }
        },
        {
            "fingerprint": {
                "description": "Each vehicle in a crash gets the same report number with its own sequence number",
                "fields": [
                    "report_number",
                    "report_seq_no"
                ]
            }
        }
    ]
}
```

`ignore_missing: true` skips documents where the field is absent; `ignore_failure: true` is added defensively since ~19.5% of rows have a `null` `dot_number` (from pandas' `NaN` → `None` conversion in `phase_index_populate.py`) and the exact behavior of the `convert` processor on an explicit JSON `null` (vs. a genuinely missing field) is not being hand-verified here — either way, a `null` `dot_number` must not break ingestion of that row.

- [ ] **Step 3: Run the `crashes-ingestion-setup` step (recreates the pipeline) then the `crashes` step**

`DOT-Commercial/data/crashes/crashes.csv` (160MB / 333,186 rows) is cheap enough to reload in full — no `num_rows` override needed.

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=crashes-ingestion-setup
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=crashes
```

- [ ] **Step 4: Verify the mapping AND the raw `_source` value**

```bash
curl -s "http://localhost:9200/_cat/indices/crashes-2026.07.28-000001?v"
curl -s "http://localhost:9200/crashes-2026.07.28-000001/_mapping?pretty" | grep -A2 '"dot_number"'
curl -s "http://localhost:9200/crashes-2026.07.28-000001/_search?q=_exists_:dot_number&size=1&pretty" | grep dot_number
```

Expected: `docs.count` = 333186; `dot_number` mapping → `"type": "long"`; and critically, the `_search` sample's raw `dot_number` value in `_source` is a bare integer like `2975796`, **not** `2975796.0` — this is the actual fix, the mapping alone was already confirmed insufficient.

- [ ] **Step 5: Re-point the carriers enrichment policy at today's freshly-fixed crashes index**

Elasticsearch cannot update an existing enrich policy in place, and cannot delete a policy that's still referenced by a live ingest pipeline — this is a known, pre-existing limitation of this codebase (`phase_enrichment_policies.py`'s `delete_policy` call catches `ConflictError` and just logs a warning, then proceeds to `put_policy`, which then fails with a swallowed `BadRequestError` because the old policy still exists — silently leaving the stale policy in place). This is listed as an open work item in the repo-root `README.md` ("Deleting enrichment policies when they are tied to pipelines. You have to delete the pipeline manually before policies can be deleted.") — not something to fix in this task, just work around by deleting the dependent pipeline first:

```bash
curl -s -X DELETE "http://localhost:9200/_ingest/pipeline/carrier-enrichment-pipeline-000001"
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
```

The second command recreates both enrich policies (`crashes-enrichment-policy`, now built from the fixed crashes index, and `inspections-enrichment-policy`) and the ingest pipeline that was just deleted.

- [ ] **Step 6: Temporarily reduce `num_rows` on `carriers` for a fast validation run**

`DOT-Commercial/data/carriers/carriers.csv` is 720MB / 2,084,753 rows. Edit `DOT-Commercial/configuration/carriers/index-config.json`, changing `"num_rows": null` to `"num_rows": 5000` (temporary — reverted in Step 8).

- [ ] **Step 7: Run the `carriers` step**

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers
```

- [ ] **Step 8: Revert `num_rows` to `null`**

Edit `DOT-Commercial/configuration/carriers/index-config.json` back to `"num_rows": null`.

- [ ] **Step 9: Verify the bug is fixed**

```bash
curl -s "http://localhost:9200/carriers-2026.07.28-000001/_search?q=_exists_:crashes&size=1&pretty"
```

Expected: **at least one hit** with a non-empty `crashes` array — this is the specific bug fix confirmation (previously always zero matches, per the repo-root README's open work item). Also spot-check the pre-existing inspections enrichment still works as a regression check:

```bash
curl -s "http://localhost:9200/carriers-2026.07.28-000001/_search?q=_exists_:inspections&size=1&pretty"
```

Expected: at least one hit (this enrichment already worked before this change; confirms no regression).

- [ ] **Step 10: Commit**

```bash
git add DOT-Commercial/configuration/crashes/index-mappings.json DOT-Commercial/configuration/crashes-ingestion-setup/pipelines.json
git commit -m "Fix crashes.dot_number type mismatch blocking crashes-enrichment-into-carriers"
```

---

### Task 6: Full pipeline regression run + documentation

**Files:**
- Modify: `DOT-Commercial/README.md`
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: the complete step chain built in Tasks 1-5.
- Produces: a full-scale, production-parity run confirming no regression, plus docs that match the shipped 7-step pipeline.

- [ ] **Step 1: Run the full pipeline end-to-end, no filters**

This is the only full-scale (non-sampled) run in this plan — all `num_rows` are already back to `null` from Tasks 4 and 5's reverts, and `inspections-per-unit`/`inspections-ingestion-setup` need no reduction (already small from Task 1's windowed fetch). This is the authoritative regression check.

```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial
```

- [ ] **Step 2: Verify the full chain against the real cluster**

```bash
curl -s "http://localhost:9200/_cat/indices/crashes-2026.07.28-000001,inspections-2026.07.28-000001,inspections-per-unit-2026.07.28-000001,carriers-2026.07.28-000001?v"
```

Expected: `crashes` = 333186 docs, `inspections` = 5647567 docs, `carriers` = 2084753 docs, `inspections-per-unit` matching Task 1's sample count (fetch wasn't re-run).

```bash
curl -s "http://localhost:9200/carriers-2026.07.28-000001/_count?q=_exists_:crashes&pretty"
curl -s "http://localhost:9200/carriers-2026.07.28-000001/_count?q=_exists_:inspections&pretty"
curl -s "http://localhost:9200/inspections-2026.07.28-000001/_count?q=_exists_:units&pretty"
```

Expected: all three counts > 0 (crashes-enrichment now works; inspections-enrichment still works; unit/VIN enrichment onto inspections works).

- [ ] **Step 3: Update `DOT-Commercial/README.md`**

Edit the `## Fetching Data` line to include the new dataset:

```markdown
Run `python3 fetch_commercial_carriers.py` from this directory to pull the latest carrier census, crash, inspection, and inspection-unit (VIN) data from the data.transportation.gov Socrata API. Optionally pass `--dataset=<carriers|crashes|inspections|inspections_per_unit>` to fetch just one. See `configuration/fetch-config.json` for dataset IDs and the crash/inspection lookback window.
```

Edit the `## Processing Steps` section:

```markdown
This data set is loaded and configured in 7 steps.
1. `crashes-ingestion-setup` - create a pipeline that creates a fingerprint from two fields to be sued as the `id` field
1. `crashes` - create an index and load the crash data
1. `inspections-per-unit` - create an index and load the per-unit VIN/vehicle data (FMCSA `wt8s-2hbx`)
1. `inspections-ingestion-setup` - create the enrichment index on `inspections-per-unit` and an ingestion pipeline that uses it
1. `inspections` - create an index and load the vehicle inpsections data, enriched with per-unit VIN data via the pipeline from `inspections-ingestion-setup`
1. `carriers-ingestion-setup` - create the enrichment indexes on `crashes` and `inspections` and an ingestion pipeline that uses them
1. `carriers` - create an index and load the carriers data using the pipeline to enrich `carriers` with data from `crashes` and `inspections`
```

- [ ] **Step 4: Update the repo-root `README.md`**

Remove this line from `## Open Work Items`:

```markdown
1. **(priority)** DOT-Commercial: crashes-enrichment-into-carriers currently returns zero matches, silently disabling one of the two enrichment features. `crashes` index's `dot_number` field is dynamically mapped as `float` (pandas infers `float64` due to ~19.5% null `dot_number` values in the crash dataset), while `carriers`/`inspections` map it `long` — the type mismatch prevents the enrich policy from matching. Needs an explicit fix (dtype coercion on CSV load, or an explicit ES mapping template, mirroring how `carriers/index-mappings.json` already pins its own field types) rather than relying on dynamic mapping.
```

Add it to `### Closed work items`:

```markdown
1. DOT-Commercial: fixed `crashes-enrichment-into-carriers` returning zero matches by adding an explicit `long` mapping for `crashes.dot_number` (was dynamically inferred as `float`), mirroring `carriers/index-mappings.json`'s existing pattern of pinning field types explicitly.
```

- [ ] **Step 5: Commit**

```bash
git add DOT-Commercial/README.md README.md
git commit -m "Document the inspections-per-unit pipeline and close the crashes.dot_number bug"
```

---

## Explicitly Out of Scope (per design spec)

- Chaining VIN/unit enrichment through to `carriers` documents.
- Any change to the `carriers-ingestion-setup` pipeline/policies beyond re-running it against the corrected `crashes` mapping.
- The separate, already-deferred ES enrich-coordinator queue-capacity issue (carriers ingestion converging to ~99.96%).
- Incremental sync for the new dataset — it follows the same full-pull-per-run model as the rest of DOT-Commercial.
