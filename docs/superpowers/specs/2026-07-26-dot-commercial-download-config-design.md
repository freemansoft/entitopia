# DOT-Commercial Download Configuration — Design

## Purpose

`DOT-Commercial/download_commercial_carriers.sh` and the three `index-config.json` files it feeds (`crashes`, `inspections`, `carriers`) are hardcoded to `2023Feb`. FMCSA/Senture publish new data periodically and could also change their file-naming convention over time. This makes the download period (year/month) and the six remote/local file names (3 datasets x remote URL + local zip name) configuration-driven instead of hardcoded, while keeping the Python-side `source` field in sync automatically.

## Scope

Covers `DOT-Commercial/download_commercial_carriers.sh`, a new `DOT-Commercial/configuration/download.env`, the three `DOT-Commercial/configuration/*/index-config.json` files, and the shared `phase_providers/phase_index_populate.py` / `utils/file_utils.py` code that resolves the `source` field. Does not touch `CMS-Providers` or any other project — the mechanism is opt-in per project and has no effect where it isn't used.

## Shared placeholder convention

`{year}` and `{month}` are literal, opaque template tokens — not calendar-validated, not zero-padded, not converted between numeric/name forms. They're substituted with whatever strings are currently configured (e.g. `2023` / `Feb`, matching FMCSA's own naming convention today). If a provider's convention changes in a way that doesn't fit this pattern for a given month, the template can simply be replaced with a literal filename for that one case — no code change required either way.

## `configuration/download.env`

New file, bash-sourceable, single-quoted values so `{year}`/`{month}` remain literal text (not bash variable expansion) when sourced:

```bash
YEAR='2023'
MONTH='Feb'
CENSUS_REMOTE_URL_TEMPLATE='https://ai.fmcsa.dot.gov/SMS/files/FMCSA_CENSUS1_{year}{month}.zip'
CENSUS_LOCAL_ZIP_TEMPLATE='FMCSA_CENSUS1_{year}{month}.zip'
INSPECTION_REMOTE_URL_TEMPLATE='ftp://ftp.senture.com/Inspection_{year}{month}.zip'
INSPECTION_LOCAL_ZIP_TEMPLATE='Inspection_{year}{month}.zip'
CRASH_REMOTE_URL_TEMPLATE='ftp://ftp.senture.com/Crash_{year}{month}.zip'
CRASH_LOCAL_ZIP_TEMPLATE='Crash_{year}{month}.zip'
```

This is the single source of truth for `YEAR`/`MONTH`, read by both the bash download script and (indirectly, via a small Python parser) the `index-config.json` template resolution.

## `download_commercial_carriers.sh` changes

1. `source configuration/download.env` for defaults.
2. Parse `--year=YYYY` and `--month=Mon` CLI flags; if given, they override the sourced `YEAR`/`MONTH`.
3. A `resolve()` helper substitutes `{year}`/`{month}` into a template string using bash's built-in `${var//search/replace}` parameter expansion — no `eval`, no new external dependency (no `jq` needed since bash never parses JSON).
4. The existing per-dataset logic (compute path → skip download if the file already exists → `curl` → `unzip`) is unchanged in structure, just fed the resolved URL and filename instead of a literal string.

No CLI overrides are added for the six URL/filename templates themselves — if a naming convention changes, edit the corresponding line in `download.env` directly. This is expected to be a rare event, not a per-run parameter.

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

## Validation

1. Run `download_commercial_carriers.sh` with no flags (using `download.env` defaults) and confirm it downloads/extracts identically to today's hardcoded `2023Feb` behavior.
2. Run it again with `--year=` / `--month=` overrides pointing at a different (real, available) period, against a scratch data directory, and confirm the resolved URLs and local filenames are correct.
3. Run `execute_project.py --project=DOT-Commercial --step=crashes --phase=index-populate` (and the equivalent for `inspections` and `carriers`) and confirm the templated `source` resolves correctly and the CSV loads successfully.

## Explicitly out of scope

- CLI overrides for the six remote URL / local filename templates (config-file-only, per above).
- Any change to `CMS-Providers` or other projects' download scripts.
- Calendar validation, zero-padding, or numeric/name month conversion — `MONTH` is an opaque string token.
- Automatically detecting new available periods from FMCSA/Senture (still a manual `--year`/`--month` decision).
