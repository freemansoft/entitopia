# DOT-Commercial Download Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `2023Feb` period in `DOT-Commercial/download_commercial_carriers.sh` and its three `index-config.json` consumers with a shared, configuration-driven year/month and per-dataset naming convention, so future periods and naming-convention changes require editing config, not code.

**Architecture:** A new `DOT-Commercial/configuration/download.env` holds `YEAR`, `MONTH`, and per-dataset (`CENSUS`/`INSPECTION`/`CRASH`) `HOST`, `REMOTE_PATH_TEMPLATE`, and `LOCAL_ZIP_TEMPLATE` values, using literal `{year}`/`{month}` placeholder tokens. The bash download script sources this file for defaults, accepts `--year=`/`--month=` CLI overrides, resolves templates with bash parameter expansion, and uses `curl --fail` plus a check-all/report-missing loop so a period with no data is flagged rather than silently mishandled. The three `index-config.json` files' `source` fields become `{year}{month}`-templated strings, resolved at load time by a new `phase_index_populate.py` helper that reads the same `download.env` — opt-in per project, since projects without a `download.env` (e.g. `CMS-Providers`) get their `source` field back unchanged.

**Tech Stack:** Bash (built-in parameter expansion only), Python 3.12 (stdlib only — `open`/`str.format`), existing `utils/file_utils.py` and `phase_providers/phase_index_populate.py` conventions.

## Global Constraints

- Python 3.11+ required project-wide (`.python-version` pins 3.12; use `.venv/bin/python3` for all verification in this plan).
- No new external dependencies: no `jq`, no `pytest`, no new pip packages. Bash built-ins and Python stdlib only.
- `{year}` and `{month}` are opaque string tokens — no calendar validation, zero-padding, or numeric/name conversion anywhere in this work.
- The `download.env` / template-resolution mechanism must be opt-in per project (based on file presence) and must not change behavior for `CMS-Providers` or any other existing project.
- No CLI overrides for the per-dataset host/path/filename templates — only `--year`/`--month` are CLI-overridable; naming-convention or host changes are made by editing `download.env` directly.

---

### Task 1: Create `configuration/download.env`

**Files:**
- Create: `DOT-Commercial/configuration/download.env`

**Interfaces:**
- Produces: a bash-sourceable key/value file with keys `YEAR`, `MONTH`, `CENSUS_HOST`, `CENSUS_REMOTE_PATH_TEMPLATE`, `CENSUS_LOCAL_ZIP_TEMPLATE`, `INSPECTION_HOST`, `INSPECTION_REMOTE_PATH_TEMPLATE`, `INSPECTION_LOCAL_ZIP_TEMPLATE`, `CRASH_HOST`, `CRASH_REMOTE_PATH_TEMPLATE`, `CRASH_LOCAL_ZIP_TEMPLATE`. Consumed by Task 4 (bash script) and, via `load_key_value_file` (Task 2), by Task 3 (Python `source` resolution).

- [ ] **Step 1: Create the file**

Create `DOT-Commercial/configuration/download.env` with exactly this content:

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

These are the exact values already hardcoded today (`2023Feb`, `ai.fmcsa.dot.gov`, `ftp.senture.com`), just split into template variables — this step alone changes no observable behavior.

- [ ] **Step 2: Verify the file sources cleanly and values are correct**

Run:
```bash
bash -c "set -e; source DOT-Commercial/configuration/download.env; echo \"$YEAR|$MONTH|$CENSUS_HOST|$CENSUS_REMOTE_PATH_TEMPLATE|$CRASH_LOCAL_ZIP_TEMPLATE\""
```

Expected output:
```
2023|Feb|https://ai.fmcsa.dot.gov|/SMS/files/FMCSA_CENSUS1_{year}{month}.zip|Crash_{year}{month}.zip
```

Confirm the `{year}`/`{month}` tokens appear literally (not expanded) — this proves the single-quoting is correct.

- [ ] **Step 3: Commit**

```bash
git add DOT-Commercial/configuration/download.env
git commit -m "Add configuration/download.env for DOT-Commercial download period and naming templates"
```

---

### Task 2: Add `load_key_value_file` to `utils/file_utils.py`

**Files:**
- Modify: `utils/file_utils.py` (append after `load_from_project_file`, currently ending at line 51)

**Interfaces:**
- Consumes: nothing new (stdlib `logging` only, already imported).
- Produces: `load_key_value_file(file_name: str) -> dict | None` — parses simple `KEY='VALUE'` or `KEY=VALUE` lines (comments starting with `#` and blank lines skipped, surrounding single/double quotes stripped from values) into a `dict`. Returns `None` if the file cannot be opened. Consumed by Task 3.

- [ ] **Step 1: Write the failing verification script**

Create a scratch file at `/tmp/verify_load_key_value_file.py`:

```python
import sys
sys.path.insert(0, ".")
from utils.file_utils import load_key_value_file

with open("/tmp/test_download.env", "w") as f:
    f.write("YEAR='2023'\nMONTH='Feb'\n# a comment\n\nCENSUS_HOST='https://ai.fmcsa.dot.gov'\n")

result = load_key_value_file("/tmp/test_download.env")
assert result == {
    "YEAR": "2023",
    "MONTH": "Feb",
    "CENSUS_HOST": "https://ai.fmcsa.dot.gov",
}, result

missing = load_key_value_file("/tmp/does_not_exist.env")
assert missing is None, missing

print("OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run from the repo root: `.venv/bin/python3 /tmp/verify_load_key_value_file.py`
Expected: `ImportError: cannot import name 'load_key_value_file' from 'utils.file_utils'`

- [ ] **Step 3: Implement `load_key_value_file`**

Append to `utils/file_utils.py`:

```python
def load_key_value_file(file_name):
    """
    Parses simple KEY='VALUE' or KEY=VALUE lines (bash-sourceable) into a dict.
    Returns None if the file cannot be opened.
    """
    logger = logging.getLogger(__name__)
    result = {}
    try:
        with open(file_name) as key_value_file:
            for line in key_value_file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip("'\"")
        logger.debug("Loaded {} info : {}".format(file_name, result))
        return result
    except Exception as e:
        logger.warning("Failed opening:{} error:{}".format(file_name, e))
        return None
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `.venv/bin/python3 /tmp/verify_load_key_value_file.py`
Expected: `OK`

- [ ] **Step 5: Clean up scratch files and commit**

```bash
rm -f /tmp/verify_load_key_value_file.py /tmp/test_download.env
git add utils/file_utils.py
git commit -m "Add load_key_value_file helper for parsing bash-sourceable config files"
```

---

### Task 3: Template the `source` field and resolve it in `phase_index_populate.py`

**Files:**
- Modify: `phase_providers/phase_index_populate.py:1-21` (add constant + module-level function after imports), `phase_providers/phase_index_populate.py:61-79` (use resolved source in `handle`)
- Modify: `DOT-Commercial/configuration/crashes/index-config.json`
- Modify: `DOT-Commercial/configuration/inspections/index-config.json`
- Modify: `DOT-Commercial/configuration/carriers/index-config.json`

**Interfaces:**
- Consumes: `file_utils.load_key_value_file(file_name) -> dict | None` from Task 2.
- Produces: `resolve_source_filename(project: str, source: str) -> str` in `phase_providers/phase_index_populate.py`, used by `PhaseIndexingPopulate.handle()`. Consumed (indirectly, by running the pipeline) in Task 5.

- [ ] **Step 1: Write the failing verification script**

Create `/tmp/verify_resolve_source_filename.py`:

```python
import sys, os, tempfile
sys.path.insert(0, ".")
from phase_providers.phase_index_populate import resolve_source_filename

with tempfile.TemporaryDirectory() as tmp:
    project = os.path.join(tmp, "DOT-Commercial")
    os.makedirs(os.path.join(project, "configuration"))
    with open(os.path.join(project, "configuration", "download.env"), "w") as f:
        f.write("YEAR='2023'\nMONTH='Feb'\n")

    result = resolve_source_filename(project, "{year}{month}_Crash.txt")
    assert result == "2023Feb_Crash.txt", result

    result2 = resolve_source_filename(project, "FMCSA_CENSUS1_{year}{month}.txt")
    assert result2 == "FMCSA_CENSUS1_2023Feb.txt", result2

with tempfile.TemporaryDirectory() as tmp:
    project = os.path.join(tmp, "CMS-Providers")
    os.makedirs(project)
    # no configuration/download.env present for this project

    result3 = resolve_source_filename(project, "DAC_NationalDownloadableFile.csv")
    assert result3 == "DAC_NationalDownloadableFile.csv", result3

print("OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python3 /tmp/verify_resolve_source_filename.py`
Expected: `ImportError: cannot import name 'resolve_source_filename' from 'phase_providers.phase_index_populate'`

- [ ] **Step 3: Implement `resolve_source_filename` and wire it into `handle()`**

In `phase_providers/phase_index_populate.py`, after the existing imports (after line 12, before the `class PhaseIndexingPopulate:` line), add:

```python
DOWNLOAD_ENV_RELATIVE_PATH = "configuration/download.env"


def resolve_source_filename(project, source):
    download_env = file_utils.load_key_value_file(
        "{}/{}".format(project, DOWNLOAD_ENV_RELATIVE_PATH)
    )
    if download_env is None:
        return source
    return source.format(
        year=download_env.get("YEAR"), month=download_env.get("MONTH")
    )
```

Then in `handle()`, replace:

```python
            csv_loader = CsvLoadUtils(
                self.project,
                self.project_config.dataDir,
                self.one_step,
                index_config.source,
                index_config.num_rows,
                index_config.skip_rows,
            )
```

with:

```python
            source_filename = resolve_source_filename(self.project, index_config.source)
            csv_loader = CsvLoadUtils(
                self.project,
                self.project_config.dataDir,
                self.one_step,
                source_filename,
                index_config.num_rows,
                index_config.skip_rows,
            )
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `.venv/bin/python3 /tmp/verify_resolve_source_filename.py`
Expected: `OK`

- [ ] **Step 5: Template the three `index-config.json` `source` fields**

In `DOT-Commercial/configuration/crashes/index-config.json`, change:
```json
    "source": "2023Feb_Crash.txt",
```
to:
```json
    "source": "{year}{month}_Crash.txt",
```

In `DOT-Commercial/configuration/inspections/index-config.json`, change:
```json
    "source": "2023Feb_Inspection.txt",
```
to:
```json
    "source": "{year}{month}_Inspection.txt",
```

In `DOT-Commercial/configuration/carriers/index-config.json`, change:
```json
    "source": "FMCSA_CENSUS1_2023Feb.txt",
```
to:
```json
    "source": "FMCSA_CENSUS1_{year}{month}.txt",
```

- [ ] **Step 6: Verify the JSON files are still valid and the templates resolve as expected**

Run:
```bash
.venv/bin/python3 -c "
import sys
sys.path.insert(0, '.')
from phase_providers.phase_index_populate import resolve_source_filename
import json

for step, expected in [
    ('crashes', '2023Feb_Crash.txt'),
    ('inspections', '2023Feb_Inspection.txt'),
    ('carriers', 'FMCSA_CENSUS1_2023Feb.txt'),
]:
    with open(f'DOT-Commercial/configuration/{step}/index-config.json') as f:
        config = json.load(f)
    resolved = resolve_source_filename('DOT-Commercial', config['source'])
    assert resolved == expected, (step, resolved)
print('OK')
"
```
Expected: `OK` (this uses the real `DOT-Commercial/configuration/download.env` created in Task 1, whose default is `2023`/`Feb`).

- [ ] **Step 7: Clean up scratch files and commit**

```bash
rm -f /tmp/verify_resolve_source_filename.py
git add phase_providers/phase_index_populate.py DOT-Commercial/configuration/crashes/index-config.json DOT-Commercial/configuration/inspections/index-config.json DOT-Commercial/configuration/carriers/index-config.json
git commit -m "Resolve index-config.json source templates from configuration/download.env"
```

---

### Task 4: Rewrite `download_commercial_carriers.sh` to be configuration-driven

**Files:**
- Modify: `DOT-Commercial/download_commercial_carriers.sh` (full rewrite)

**Interfaces:**
- Consumes: `DOT-Commercial/configuration/download.env` keys from Task 1.
- Produces: a script accepting `--year=YYYY` and `--month=Mon` CLI flags (overriding `download.env` defaults), exiting `0` if all three datasets download successfully (or were already present), exiting `1` and printing a `No data found for: <names> (year=X, month=Y)` summary to stderr if any dataset's remote file doesn't exist. Exercised end-to-end in Task 5.

This is a whole-script rewrite rather than a classic unit-level TDD cycle (bash has no unit test framework here, and the old script's hardcoded structure can't run this task's test meaningfully). Instead: implement first, then verify thoroughly against a local HTTP fixture server (no network dependency, no new tools — `python3 -m http.server` is stdlib).

- [ ] **Step 1: Replace the script content**

Replace the entire contents of `DOT-Commercial/download_commercial_carriers.sh` with:

```bash
#!/usr/bin/env bash
# assumes running in DOT-Commercial dir

mkdir -p configuration
mkdir -p data

source configuration/download.env

while [[ $# -gt 0 ]]; do
    case "$1" in
        --year=*)
            YEAR="${1#*=}"
            ;;
        --month=*)
            MONTH="${1#*=}"
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
done

resolve() {
    local template="$1"
    template="${template//\{year\}/$YEAR}"
    template="${template//\{month\}/$MONTH}"
    echo "$template"
}

MISSING=()

download_dataset() {
    local name="$1"
    local host="$2"
    local path_template="$3"
    local zip_template="$4"
    local data_subdir="$5"

    local url="${host}$(resolve "$path_template")"
    local zip_name
    zip_name="$(resolve "$zip_template")"
    local file="data/${data_subdir}/${zip_name}"

    if [ -f "$file" ]; then
        echo "Already downloaded: $file"
        return
    fi

    mkdir -p "data/${data_subdir}"
    echo "Downloading $name from $url"
    if curl --fail "$url" --output "$file"; then
        unzip "$file" -d "data/${data_subdir}"
    else
        echo "No data found for $name (year=$YEAR, month=$MONTH)" >&2
        rm -f "$file"
        MISSING+=("$name")
    fi
}

download_dataset "census" "$CENSUS_HOST" "$CENSUS_REMOTE_PATH_TEMPLATE" "$CENSUS_LOCAL_ZIP_TEMPLATE" "carriers"
download_dataset "inspection" "$INSPECTION_HOST" "$INSPECTION_REMOTE_PATH_TEMPLATE" "$INSPECTION_LOCAL_ZIP_TEMPLATE" "inspections"
download_dataset "crash" "$CRASH_HOST" "$CRASH_REMOTE_PATH_TEMPLATE" "$CRASH_LOCAL_ZIP_TEMPLATE" "crashes"

if [ ${#MISSING[@]} -gt 0 ]; then
    missing_list=$(printf '%s, ' "${MISSING[@]}")
    missing_list="${missing_list%, }"
    echo "No data found for: ${missing_list} (year=$YEAR, month=$MONTH)" >&2
    exit 1
fi
```

- [ ] **Step 2: Build a local test fixture (no real network involved)**

```bash
mkdir -p /tmp/dot_test/serve
.venv/bin/python3 -c "import zipfile; zipfile.ZipFile('/tmp/dot_test/serve/FMCSA_CENSUS1_2023Feb.zip', 'w').writestr('FMCSA_CENSUS1_2023Feb.txt', 'id,name\n1,Test Carrier\n')"
.venv/bin/python3 -c "import zipfile; zipfile.ZipFile('/tmp/dot_test/serve/Inspection_2023Feb.zip', 'w').writestr('2023Feb_Inspection.txt', 'id\n1\n')"
# Crash_2023Feb.zip intentionally NOT created, to simulate a missing dataset
```

- [ ] **Step 3: Start a local HTTP server for the fixture**

```bash
.venv/bin/python3 -m http.server 8080 --directory /tmp/dot_test/serve &
SERVER_PID=$!
sleep 1
```

- [ ] **Step 4: Set up a scratch project pointing at the local server**

```bash
mkdir -p /tmp/dot_test/project/configuration
cat > /tmp/dot_test/project/configuration/download.env <<'EOF'
YEAR='2023'
MONTH='Feb'
CENSUS_HOST='http://localhost:8080'
CENSUS_REMOTE_PATH_TEMPLATE='/FMCSA_CENSUS1_{year}{month}.zip'
CENSUS_LOCAL_ZIP_TEMPLATE='FMCSA_CENSUS1_{year}{month}.zip'
INSPECTION_HOST='http://localhost:8080'
INSPECTION_REMOTE_PATH_TEMPLATE='/Inspection_{year}{month}.zip'
INSPECTION_LOCAL_ZIP_TEMPLATE='Inspection_{year}{month}.zip'
CRASH_HOST='http://localhost:8080'
CRASH_REMOTE_PATH_TEMPLATE='/Crash_{year}{month}.zip'
CRASH_LOCAL_ZIP_TEMPLATE='Crash_{year}{month}.zip'
EOF
cp DOT-Commercial/download_commercial_carriers.sh /tmp/dot_test/project/
```

- [ ] **Step 5: Run the happy-path + missing-dataset scenario**

```bash
cd /tmp/dot_test/project
bash download_commercial_carriers.sh
echo "exit code: $?"
ls data/carriers data/inspections
cd -
```

Expected:
- Output includes `Downloading census ...` and `Downloading inspection ...` followed by successful `unzip` output.
- Output includes `No data found for crash (year=2023, month=Feb)` and, at the end, `No data found for: crash (year=2023, month=Feb)`.
- `data/carriers/FMCSA_CENSUS1_2023Feb.zip` and `data/inspections/Inspection_2023Feb.zip` exist and were extracted (their `.txt` files present).
- `exit code: 1`.

- [ ] **Step 6: Run again to verify idempotency (skip-if-exists)**

```bash
cd /tmp/dot_test/project
bash download_commercial_carriers.sh
cd -
```

Expected: output includes `Already downloaded: data/carriers/FMCSA_CENSUS1_2023Feb.zip` and `Already downloaded: data/inspections/Inspection_2023Feb.zip` (no re-download), crash is still attempted and still reported missing.

- [ ] **Step 7: Run the full-success scenario with a `--year`/`--month` CLI override**

```bash
.venv/bin/python3 -c "import zipfile; zipfile.ZipFile('/tmp/dot_test/serve/FMCSA_CENSUS1_2023Mar.zip', 'w').writestr('FMCSA_CENSUS1_2023Mar.txt', 'id,name\n1,Test Carrier\n')"
.venv/bin/python3 -c "import zipfile; zipfile.ZipFile('/tmp/dot_test/serve/Inspection_2023Mar.zip', 'w').writestr('2023Mar_Inspection.txt', 'id\n1\n')"
.venv/bin/python3 -c "import zipfile; zipfile.ZipFile('/tmp/dot_test/serve/Crash_2023Mar.zip', 'w').writestr('2023Mar_Crash.txt', 'id\n1\n')"

cd /tmp/dot_test/project
bash download_commercial_carriers.sh --year=2023 --month=Mar
echo "exit code: $?"
ls data/carriers data/inspections data/crashes
cd -
```

Expected: all three datasets download and extract successfully (filenames containing `2023Mar`), `exit code: 0`.

- [ ] **Step 8: Stop the local server and clean up**

```bash
kill $SERVER_PID
rm -rf /tmp/dot_test
```

- [ ] **Step 9: Commit**

```bash
git add DOT-Commercial/download_commercial_carriers.sh
git commit -m "Make download_commercial_carriers.sh configuration-driven with year/month CLI overrides"
```

---

### Task 5: Validate against real 2023, 2024, and 2025 data

**Files:** none (validation only; no code changes expected). If validation surfaces a naming-convention difference for a specific period, fix the relevant `download.env` template as a follow-up (not part of this task).

**Interfaces:**
- Consumes: Task 1 (`download.env`), Task 3 (templated `source` resolution), Task 4 (rewritten script). Exercises the whole feature end-to-end against the real FMCSA/Senture hosts.

- [ ] **Step 1: Confirm the unchanged default still works**

From `DOT-Commercial/`, run:
```bash
bash download_commercial_carriers.sh
echo "exit code: $?"
```
Expected: identical behavior to the original hardcoded script — census, inspection, and crash for `2023`/`Feb` download and extract successfully (or report `Already downloaded:` if `data/` already has them from prior runs), `exit code: 0`.

- [ ] **Step 2: Find real available periods for 2023, 2024, and 2025**

Visit `https://ai.fmcsa.dot.gov/SMS/Tools/Downloads.aspx` (the source URL documented in `DOT-Commercial/README.md`) and identify one working year/month combination for each of 2023, 2024, and 2025 (the exact month naming — e.g. `Jan`, `Feb`, `Mar` — should match what FMCSA/Senture currently publish; check an actual download link if unsure).

- [ ] **Step 3: Run the script against each of the three periods**

For each of the three periods found in Step 2, from `DOT-Commercial/`:
```bash
bash download_commercial_carriers.sh --year=<YYYY> --month=<Mon>
echo "exit code: $?"
ls data/carriers data/inspections data/crashes
```
Expected for each period that has data: all three datasets download and extract, `exit code: 0`. Record which of the three years (if any) has a dataset that's actually unavailable — per this feature's design, that should surface as a `No data found for: ...` message and a non-zero exit code rather than a silent failure or corrupted download.

- [ ] **Step 4: Confirm a known-missing period is flagged correctly**

Pick a year/month combination expected to have no data (e.g., a future month beyond the current data or a month before FMCSA began publishing these files) and run:
```bash
bash download_commercial_carriers.sh --year=<YYYY> --month=<Mon>
echo "exit code: $?"
```
Expected: `No data found for: census, inspection, crash (year=<YYYY>, month=<Mon>)` (or whichever subset is actually missing) printed to stderr, `exit code: 1`, and no corrupted `.zip` files left in `data/`.

- [ ] **Step 5: Confirm the Python pipeline loads a successfully-downloaded period**

Requires a running Elasticsearch cluster per `README.md`'s Setup section. Using one of the periods confirmed to work in Step 3 (and with `DOT-Commercial/configuration/download.env`'s `YEAR`/`MONTH` temporarily set to that period, or left at the default `2023`/`Feb` if that period's files are the ones present in `data/`):
```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=crashes --phase=index-populate
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=inspections --phase=index-populate
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers --phase=index-populate
```
Expected: each command logs `Indexing <N> records into index ...` and completes without a file-not-found error, confirming the templated `source` field resolved to the actual downloaded/extracted filename.

No commit for this task — it's a validation pass. If any period is missing data, that's an expected, correctly-flagged outcome per this feature's purpose, not a defect to fix here.
