# DOT-Commercial Socrata API Migration — Design

## Purpose

The old FMCSA/Senture FTP and HTTPS zip-file download model (`FMCSA_CENSUS1_*.zip`, `Inspection_*.zip`, `Crash_*.zip`) is obsolete. The site moved to `data.transportation.gov`, a Socrata-based open data portal, which only serves live, continuously-updated tables — there is no more month-specific archive to download. This migrates `DOT-Commercial` to fetch data from the new Socrata SODA API instead.

## Confirmed replacement datasets

Verified live against the real API during design (not guessed):

| Old file | New dataset | Dataset ID | Rows (as of design time) | Update freq |
|---|---|---|---|---|
| `FMCSA_CENSUS1_*.zip` | SMS Input - Motor Carrier Census Information | `kjg3-diqy` | 2,085,534 | Monthly |
| `Crash_*.zip` | Crash File | `aayw-vxb3` | 4,970,017 (back to 1990) | Daily |
| `Inspection_*.zip` | Vehicle Inspection File | `fx4q-ay7w` | 8,310,286 | Daily |

Census match confirmed by field name: `DOT_NUMBER` is the first column, matching the existing `carriers/index-config.json`'s `id_field`.

API access: standard Socrata SODA API, `https://data.transportation.gov/resource/{dataset-id}.csv` (or `.json`). No authentication required for the request volumes tested; `$limit`/`$offset` pagination and `$where` filtering both confirmed working. `$limit=100000` returned exactly 100,000 rows successfully in testing.

**Important gotcha found during design:** the crash/inspection date fields (`report_date`, `insp_date`) are plain **text in `YYYYMMDD` format** (e.g. `"20230810"`), not a typed date column. `$where` filters must compare against this exact string format (e.g. `insp_date > '20260701'`) — a dashed ISO format or incorrect URL encoding can silently return an unfiltered (or wrongly filtered) result with no error, which was observed directly during design testing. The fetch script must build these comparisons carefully and use a request library's own parameter encoding (e.g. Python `requests`' `params=`) rather than hand-built query strings.

## Scope

Full replacement — the old FTP/HTTPS download mechanism, and the year/month-templating mechanism added for it in the prior migration (`configuration/download.env`, `load_key_value_file`, `resolve_source_filename`), are removed entirely. Nothing in this design keeps them as a fallback.

## Architecture

`DOT-Commercial/download_commercial_carriers.sh` is replaced by `DOT-Commercial/fetch_commercial_carriers.py`. It reads `DOT-Commercial/configuration/fetch-config.json`, pages through each of the three Socrata datasets, and writes each to a fixed-name local CSV file — overwritten fresh on every run (full pull, no incremental sync in v1). Everything downstream — `execute_project.py` → `phase_providers` → `CsvLoadUtils` → Elasticsearch — is unchanged: it reads a local CSV file exactly as it does today, unaware of whether that file came from a paginated API or an unzipped archive.

Crash and inspection are scoped to a rolling 24-month window (matching FMCSA's own SMS scoring lookback, confirmed via prior research) via `$where` filtering, keeping pulls fast and the resulting Elasticsearch indexes a relevant size instead of the full multi-decade history. Census has no time dimension to scope — it's already just the current registry state, so it's always a full pull (~2M rows).

## `configuration/fetch-config.json`

New file:

```json
{
    "base_url": "https://data.transportation.gov",
    "app_token_env_var": "SOCRATA_APP_TOKEN",
    "page_size": 50000,
    "datasets": {
        "carriers": {
            "dataset_id": "kjg3-diqy",
            "output": "data/carriers/carriers.csv",
            "date_field": null,
            "window_months": null
        },
        "crashes": {
            "dataset_id": "aayw-vxb3",
            "output": "data/crashes/crashes.csv",
            "date_field": "report_date",
            "window_months": 24
        },
        "inspections": {
            "dataset_id": "fx4q-ay7w",
            "output": "data/inspections/inspections.csv",
            "date_field": "insp_date",
            "window_months": 24
        }
    }
}
```

`window_months` is a plain configurable number, not hardcoded into the script — changing the lookback window is a config edit, not a code change.

## `fetch_commercial_carriers.py`

Run as `python3 fetch_commercial_carriers.py` from `DOT-Commercial/` (optional `--dataset=carriers` to fetch just one, for testing or re-running a single failed dataset). For each configured dataset:

1. If `date_field` is set, compute a cutoff as `(now - window_months).strftime("%Y%m%d")` and build a `$where` clause comparing `date_field` against it, in the dataset's native `YYYYMMDD` text format.
2. Page through `GET {base_url}/resource/{dataset_id}.csv` using `requests`, with `params={"$limit": page_size, "$offset": offset, "$where": ..., "$$app_token": token_if_present}` — letting `requests` handle URL encoding rather than hand-building query strings (the encoding pitfall found during design testing).
3. Uses Socrata's own `.csv` export endpoint directly (not `.json`), avoiding a JSON→CSV conversion step and leaving quoting/escaping to Socrata.
4. Streams each page to the output file: the first page's response (including its header row) opens the file; subsequent pages have their repeated header line stripped before appending.
5. Stops when a page returns fewer rows than `page_size` (end of data) — this also correctly and cheaply handles the exact-multiple boundary case (one extra zero-row request).
6. Verifies each response's `Content-Type` is `text/csv` before treating it as data, rather than trusting the HTTP status code alone (a direct lesson from the previous migration's discovery that the old FMCSA host returned HTTP 200 for a "file not found" HTML page).
7. Writes to a `.tmp` path and renames to the final output path only on full success — a failed run never leaves a half-written CSV in place of a previously-good one.
8. A failed page (network error, non-200, or a Socrata error body) raises immediately and exits non-zero. No partial-success or retry logic in v1, matching the "simplest, full pull" approach chosen for sync strategy — a failure means rerun the whole fetch.
9. `app_token_env_var` names an environment variable to read for an optional Socrata app token; if unset, requests proceed unauthenticated (subject to Socrata's standard throttling for anonymous callers). No code branches on its presence beyond including or omitting the request parameter.

## `index-config.json` and load-pipeline changes

Each of the three `DOT-Commercial/configuration/*/index-config.json` files:

- `source` becomes a plain fixed filename again: `"carriers.csv"`, `"crashes.csv"`, `"inspections.csv"` — no more `{year}{month}` template.
- `num_rows` is removed so `PhaseIndexingPopulate` indexes everything in the fetched file, not a capped sample. (Previously all three were hardcoded to `50000`, which — because of `islice(records, num_rows)` in `phase_index_populate.py` — silently capped every index-populate run, including the old census load, to its first 50,000 rows regardless of file size. This migration removes that cap so a real full-registry pull actually gets indexed.)
- `skip_rows` stays `0`.

`phase_providers/phase_index_populate.py`'s `handle()` passes `index_config.source` straight to `CsvLoadUtils` again — no more `resolve_source_filename` call.

`utils/csv_load_utils.py`'s `CsvLoadUtils` switches its hardcoded read encoding from `windows-1252` to `utf-8` (Socrata's CSV export is UTF-8). This applies globally, including to `CMS-Providers`, which shares the same `CsvLoadUtils` — no new per-config encoding field.

## Cleanup

- Delete `DOT-Commercial/download_commercial_carriers.sh`.
- Delete `DOT-Commercial/configuration/download.env`.
- Remove `resolve_source_filename` and its call site from `phase_providers/phase_index_populate.py`.
- Remove `load_key_value_file` from `utils/file_utils.py`.
- Update `DOT-Commercial/README.md`'s setup instructions to reference `fetch_commercial_carriers.py` instead of the old download script.

This fully retires the year/month-templating mechanism added by the prior migration PR — after this change it has zero remaining callers anywhere in the codebase.

## Error handling & edge cases

- **A page request fails** (network error, non-200, or a Socrata error body): the script raises immediately, exits non-zero, and (via the `.tmp`-then-rename pattern) leaves any previous good output file untouched rather than half-overwritten.
- **A 200-status response that isn't real data**: caught by the `Content-Type: text/csv` check before the body is trusted, rather than relying on HTTP status alone.
- **Missing app token**: requests proceed unauthenticated; no special handling needed beyond omitting the parameter.
- **Exact page-size-multiple row count**: handled correctly and cheaply by the "stop when a page returns fewer than `page_size` rows" termination check.

## Validation plan

1. Run `fetch_commercial_carriers.py --dataset=carriers` and confirm `data/carriers/carriers.csv` is written with a real header and a row count close to the live ~2M count.
2. Run `--dataset=crashes` and `--dataset=inspections`; confirm each respects the 24-month window (spot-check the oldest date present) and that row counts are far smaller than the full historical tables.
3. Run all three via `execute_project.py --project=DOT-Commercial --step=<carriers|crashes|inspections> --phase=index-populate` against a real Elasticsearch cluster; confirm full row counts land in each index (no 50,000 cap) and that non-ASCII characters (e.g. an accented carrier name) round-trip correctly under UTF-8.
4. Re-run `CMS-Providers`' existing `index-populate` phases after the `CsvLoadUtils` encoding change and confirm no new errors or mangled output.
5. Confirm old artifacts are fully gone: `download_commercial_carriers.sh` and `configuration/download.env` no longer exist; `resolve_source_filename` and `load_key_value_file` have zero remaining references in the codebase.

## Explicitly out of scope

- Incremental sync (only fetching rows changed since the last run) — v1 is always a full pull.
- CLI overrides for dataset IDs, base URL, or window length beyond what `fetch-config.json` and `--dataset` already provide.
- Automatic Socrata app token registration or management — the token is an optional, manually-provisioned environment variable.
- Any change to `CMS-Providers`' data sources or download mechanism (only its `CsvLoadUtils` encoding is affected, and only because that class is shared).
