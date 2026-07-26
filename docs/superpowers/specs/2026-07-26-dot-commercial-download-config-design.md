# DOT-Commercial Download Configuration — Design

## Purpose

`DOT-Commercial/download_commercial_carriers.sh` and the three `index-config.json` files it feeds (`crashes`, `inspections`, `carriers`) are hardcoded to `2023Feb`. FMCSA/Senture publish new data periodically and could also change their file-naming convention — or even their host — over time. This makes the download period (year/month) and, per dataset, the remote host, remote path, and local zip name configuration-driven instead of hardcoded, while keeping the Python-side `source` field in sync automatically.

## Scope

Covers `DOT-Commercial/download_commercial_carriers.sh`, a new `DOT-Commercial/configuration/download.env`, the three `DOT-Commercial/configuration/*/index-config.json` files, and the shared `phase_providers/phase_index_populate.py` / `utils/file_utils.py` code that resolves the `source` field. Does not touch `CMS-Providers` or any other project — the mechanism is opt-in per project and has no effect where it isn't used.

## Note on this revision

An earlier version of this design combined each dataset's remote location into a single `*_REMOTE_URL_TEMPLATE` variable (host + path together). This revision splits that into a separate `*_HOST` and `*_REMOTE_PATH_TEMPLATE` per dataset, so the host can be changed independently of the path/filename convention.

## Shared placeholder convention

`{year}` and `{month}` are literal, opaque template tokens — not calendar-validated, not zero-padded, not converted between numeric/name forms. They're substituted with whatever strings are currently configured (e.g. `2023` / `Feb`, matching FMCSA's own naming convention today). If a provider's convention changes in a way that doesn't fit this pattern for a given month, the template can simply be replaced with a literal filename for that one case — no code change required either way.

## `configuration/download.env`

New file, bash-sourceable, single-quoted values so `{year}`/`{month}` remain literal text (not bash variable expansion) when sourced:

```bash
YEAR='2023'
MONTH='Feb'
CENSUS_HOST='https://ai.fmcsa.dot.gov'
CENSUS_REMOTE_PATH_TEMPLATE='/SMS/files/FMCSA_CENSUS1_{year}{month}.zip'
CENSUS_LOCAL_ZIP_TEMPLATE='FMCSA_CENSUS1_{year}{month}.zip'
INSPECTION_HOST='ftp://ftp.senture.com'
INSPECTION_REMOTE_PATH_TEMPLATE='/Inspection_{year}{month}.zip'
INSPECTION_LOCAL_ZIP_TEMPLATE='Inspection_{year}{month}.zip'
CRASH_HOST='ftp://ftp.senture.com'
CRASH_REMOTE_PATH_TEMPLATE='/Crash_{year}{month}.zip'
CRASH_LOCAL_ZIP_TEMPLATE='Crash_{year}{month}.zip'
```

The host is kept separate from the path template, one pair per dataset (even though `INSPECTION_HOST` and `CRASH_HOST` currently hold the same senture.com value) — so a host change for one dataset doesn't require touching the others, and each dataset's config reads as a complete, independent unit. The full remote URL is `HOST + PATH` at resolution time.

This is the single source of truth for `YEAR`/`MONTH`, read by both the bash download script and (indirectly, via a small Python parser) the `index-config.json` template resolution.

## `download_commercial_carriers.sh` changes

1. `source configuration/download.env` for defaults.
2. Parse `--year=YYYY` and `--month=Mon` CLI flags; if given, they override the sourced `YEAR`/`MONTH`.
3. A `resolve()` helper substitutes `{year}`/`{month}` into a template string using bash's built-in `${var//search/replace}` parameter expansion — no `eval`, no new external dependency (no `jq` needed since bash never parses JSON). The full remote URL for each dataset is built as `"${HOST}$(resolve "$REMOTE_PATH_TEMPLATE")"`.
4. The existing per-dataset logic (compute path → skip download if the file already exists → `curl` → `unzip`) is unchanged in structure, just fed the resolved URL and filename instead of a literal string.
5. `curl` calls add `--fail` so a missing remote file (HTTP 4xx/5xx, or an FTP file that doesn't exist) causes `curl` to exit non-zero instead of silently writing an error page or empty file as if it were the real archive.
6. The script attempts all three datasets regardless of an earlier failure, collects which one(s) failed, and at the end prints a summary (e.g. `No data found for: inspection, crash (year=2019, month=Feb)`) and exits non-zero if any dataset was missing. This lets you probe whether a given year/month has data across all three datasets in one run rather than stopping at the first miss.

No CLI overrides are added for the host/path/filename templates themselves — if a naming convention or host changes, edit the corresponding line(s) in `download.env` directly. This is expected to be a rare event, not a per-run parameter.

## Python-side changes

**`index-config.json` files** — `source` becomes a template:
- `crashes/index-config.json`: `"source": "{year}{month}_Crash.txt"`
- `inspections/index-config.json`: `"source": "{year}{month}_Inspection.txt"`
- `carriers/index-config.json`: `"source": "FMCSA_CENSUS1_{year}{month}.txt"`

**`utils/file_utils.py`** — new `load_key_value_file(file_name)` function: parses simple `KEY='VALUE'` lines (strips quotes, skips comments/blank lines) into a dict. No new dependency, mirrors the style of the existing `load_from_file` JSON loader.

**`phase_providers/phase_index_populate.py`** — after loading `index_config`, before constructing `CsvLoadUtils`:
1. Attempt to load `<project>/configuration/download.env` via `load_key_value_file`.
2. If it exists, resolve `index_config.source` with `.format(year=..., month=...)` using the loaded values.
3. If it doesn't exist, use `index_config.source` unchanged.

This makes resolution opt-in per project based on file presence — `CMS-Providers` (no `download.env`, no `{year}`/`{month}` tokens in its `source` fields) is completely unaffected, with no project-name special-casing anywhere in the code.

## Edge cases

- **Naming convention changes for one dataset only**: edit that dataset's template line in `download.env`; the other two are untouched.
- **Convention breaks entirely for one month**: replace the template with a literal filename for that run; no code change needed.
- **`download.env` missing but `index-config.json` still has `{year}`/`{month}` in `source`**: the string is left unresolved, and `CsvLoadUtils` fails with a file-not-found when it tries to read the literal `{year}{month}_Crash.txt` path. This is treated as a misconfiguration that should surface immediately rather than fail silently or guess.
- **Requested year/month has no data on the remote server for one or more datasets**: `curl --fail` causes that dataset's download to fail cleanly (no corrupt zip written); the script still attempts the remaining datasets, then reports the full set of missing datasets and exits non-zero.

## Validation

1. Run `download_commercial_carriers.sh` with no flags (using `download.env` defaults) and confirm it downloads/extracts identically to today's hardcoded `2023Feb` behavior.
2. Run it against real periods from **2023, 2024, and 2025** (at least one month per year) and confirm all three datasets download and extract correctly for periods that exist.
3. Run it against at least one year/month combination expected *not* to exist on the remote (e.g., a future month, or a known gap) and confirm the script reports the missing dataset(s) by name and exits non-zero, rather than writing a bad zip or silently succeeding.
4. Run `execute_project.py --project=DOT-Commercial --step=crashes --phase=index-populate` (and the equivalent for `inspections` and `carriers`) against one of the successfully downloaded 2023–2025 periods and confirm the templated `source` resolves correctly and the CSV loads successfully.

## Explicitly out of scope

- CLI overrides for the per-dataset host / remote path / local filename templates (config-file-only, per above).
- Any change to `CMS-Providers` or other projects' download scripts.
- Calendar validation, zero-padding, or numeric/name month conversion — `MONTH` is an opaque string token.
- Automatically detecting new available periods from FMCSA/Senture (still a manual `--year`/`--month` decision).
