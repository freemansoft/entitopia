# DOT-Commercial Socrata Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `DOT-Commercial`'s defunct FMCSA FTP/HTTPS zip-download pipeline with a paginated fetch from the new data.transportation.gov Socrata API, feeding the existing Elasticsearch load pipeline unchanged.

**Architecture:** A new `DOT-Commercial/fetch_commercial_carriers.py` pages through three Socrata datasets (`kjg3-diqy` census, `aayw-vxb3` crash, `fx4q-ay7w` inspection) via `requests`, writing fixed-name local CSVs (full pull every run; crash/inspection scoped to a rolling 24-month window via `$where`). `execute_project.py` → `phase_providers` → `CsvLoadUtils` → Elasticsearch stays unchanged apart from a global encoding fix and each `index-config.json`'s `source`/`num_rows` fields. The old `download_commercial_carriers.sh` and its FTP/zip logic are deleted.

**Tech Stack:** Python 3.12, `requests` (already a project dependency — no new dependencies), existing `utils/file_utils.py` (`load_from_file`) and `utils/csv_load_utils.py` conventions.

## Global Constraints

- Python 3.11+ required project-wide (`.python-version` pins 3.12; use `.venv/bin/python3` for all verification).
- No new external dependencies — `requests` is already in `requirements.txt`.
- Full pull every run in v1 — no incremental/last-synced-state sync.
- Crash and inspection are scoped to a rolling 24-month window via `$where`; census has no time dimension and is always a full pull.
- The crash/inspection date fields (`report_date`, `insp_date`) are plain text in `YYYYMMDD` format (e.g. `"20230810"`), **not** a typed date column — `$where` comparisons must use this exact format, built via a request library's own parameter encoding (never hand-built query strings).
- No CLI overrides beyond `--dataset=<name>` — dataset IDs, base URL, and window length live only in `configuration/fetch-config.json`.
- The `CsvLoadUtils` encoding change (`windows-1252` → `utf-8`) is global — it also affects `CMS-Providers`, which shares the same class.

---

### Task 1: Core fetch/pagination logic

**Files:**
- Create: `DOT-Commercial/fetch_commercial_carriers.py`

**Interfaces:**
- Produces: `compute_where_clause(date_field, window_months, now) -> str | None` and `fetch_dataset(session, base_url, dataset_id, output_path, date_field=None, window_months=None, page_size=50000, app_token=None, now=None) -> int` (returns total data-row count written). Consumed by Task 2 (same file, `main()` added there).

- [ ] **Step 1: Create a local fixture HTTP server for testing**

Create `/tmp/fixture_server.py`:

```python
import csv
import io
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

FIELDNAMES = ["id", "name", "insp_date"]
START = date(2024, 1, 1)
ROWS = [
    {
        "id": str(i),
        "name": "Row{}".format(i),
        "insp_date": (START + timedelta(days=i)).strftime("%Y%m%d"),
    }
    for i in range(250)
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/resource/baddataset.csv":
            body = b"<html><body>Not Found</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        limit = int(params.get("$limit", ["100"])[0])
        offset = int(params.get("$offset", ["0"])[0])
        where = params.get("$where", [None])[0]

        rows = ROWS
        if where:
            field, _, value = where.partition(">")
            field = field.strip()
            value = value.strip().strip("'")
            rows = [r for r in rows if r[field] > value]

        page = rows[offset:offset + limit]

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(page)
        body = buf.getvalue().encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    HTTPServer(("localhost", port), Handler).serve_forever()
```

This fixture serves 250 rows (real, consecutive calendar dates starting 2024-01-01) at any `/resource/{id}.csv` path, correctly honoring `$limit`/`$offset`/`$where`, plus a special `/resource/baddataset.csv` path that always returns HTML with a 200 status — simulating the exact "fake success" failure mode found in the previous FMCSA migration.

- [ ] **Step 2: Write the failing verification script**

Create `/tmp/verify_fetch_dataset.py`:

```python
import sys, os
sys.path.insert(0, "DOT-Commercial")
import csv as csv_module
from datetime import datetime
import requests

from fetch_commercial_carriers import compute_where_clause, fetch_dataset

result = compute_where_clause(None, None, datetime(2026, 7, 1))
assert result is None, result

result = compute_where_clause("insp_date", None, datetime(2026, 7, 1))
assert result is None, result

result = compute_where_clause("insp_date", 3, datetime(2026, 7, 1))
assert result == "insp_date > '20260402'", result

session = requests.Session()

output_path = "/tmp/fetch_test_output.csv"
total = fetch_dataset(
    session=session,
    base_url="http://localhost:8090",
    dataset_id="fixture",
    output_path=output_path,
    page_size=100,
)
assert total == 250, total

with open(output_path) as f:
    rows = list(csv_module.DictReader(f))
assert len(rows) == 250, len(rows)
assert rows[0]["id"] == "0"
assert rows[-1]["id"] == "249"

output_path2 = "/tmp/fetch_test_output_filtered.csv"
total2 = fetch_dataset(
    session=session,
    base_url="http://localhost:8090",
    dataset_id="fixture",
    output_path=output_path2,
    date_field="insp_date",
    window_months=3,
    page_size=100,
    now=datetime(2024, 7, 1),
)
assert 0 < total2 < 250, total2

with open(output_path2) as f:
    filtered_rows = list(csv_module.DictReader(f))
cutoff = "20240402"
assert all(r["insp_date"] > cutoff for r in filtered_rows), "found a row not satisfying the date filter"
assert len(filtered_rows) == total2

try:
    fetch_dataset(
        session=session,
        base_url="http://localhost:8090",
        dataset_id="baddataset",
        output_path="/tmp/fetch_test_bad.csv",
    )
    raise AssertionError("expected ValueError for bad content type")
except ValueError as e:
    assert "Content-Type" in str(e), str(e)

assert not os.path.exists("/tmp/fetch_test_bad.csv"), "final output should not exist after a failed fetch"

print("OK")
```

- [ ] **Step 3: Run it to verify it fails**

Run from the repo root: `.venv/bin/python3 /tmp/verify_fetch_dataset.py`
Expected: `ModuleNotFoundError: No module named 'fetch_commercial_carriers'`

- [ ] **Step 4: Implement `compute_where_clause` and `fetch_dataset`**

Create `DOT-Commercial/fetch_commercial_carriers.py`:

```python
import logging
import os
from datetime import datetime, timedelta


def compute_where_clause(date_field, window_months, now):
    """
    Returns a Socrata $where clause filtering date_field to values after
    (now - window_months), formatted as YYYYMMDD text to match the
    dataset's native date field format. Returns None if no filtering
    is configured.
    """
    if not date_field or not window_months:
        return None
    cutoff = now - timedelta(days=30 * window_months)
    cutoff_str = cutoff.strftime("%Y%m%d")
    return "{} > '{}'".format(date_field, cutoff_str)


def fetch_dataset(
    session,
    base_url,
    dataset_id,
    output_path,
    date_field=None,
    window_months=None,
    page_size=50000,
    app_token=None,
    now=None,
):
    """
    Pages through a Socrata dataset's CSV export and writes the full
    result to output_path. Returns the total number of data rows written.
    """
    logger = logging.getLogger(__name__)
    if now is None:
        now = datetime.now()
    where_clause = compute_where_clause(date_field, window_months, now)

    tmp_path = output_path + ".tmp"
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    offset = 0
    total_rows = 0
    header_written = False

    with open(tmp_path, "w", newline="", encoding="utf-8") as out_file:
        while True:
            params = {"$limit": page_size, "$offset": offset}
            if where_clause:
                params["$where"] = where_clause
            if app_token:
                params["$$app_token"] = app_token

            url = "{}/resource/{}.csv".format(base_url, dataset_id)
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "text/csv" not in content_type:
                raise ValueError(
                    "Expected text/csv from {} but got Content-Type: {}".format(
                        url, content_type
                    )
                )

            lines = response.text.splitlines(keepends=True)
            if not lines:
                break

            data_lines = lines[1:]

            if not header_written:
                out_file.write(lines[0])
                header_written = True
            out_file.writelines(data_lines)

            page_row_count = len(data_lines)
            total_rows += page_row_count
            logger.info(
                "Fetched {} rows (offset {}) for dataset {}".format(
                    page_row_count, offset, dataset_id
                )
            )

            if page_row_count < page_size:
                break
            offset += page_size

    os.replace(tmp_path, output_path)
    logger.info("Wrote {} total rows to {}".format(total_rows, output_path))
    return total_rows
```

- [ ] **Step 5: Start the fixture server**

```bash
.venv/bin/python3 /tmp/fixture_server.py 8090 &
FIXTURE_PID=$!
sleep 1
```

- [ ] **Step 6: Run the verification script to confirm it passes**

Run from the repo root: `.venv/bin/python3 /tmp/verify_fetch_dataset.py`
Expected: `OK`

- [ ] **Step 7: Stop the fixture server and clean up scratch files**

```bash
kill $FIXTURE_PID
rm -f /tmp/fixture_server.py /tmp/verify_fetch_dataset.py /tmp/fetch_test_output.csv /tmp/fetch_test_output.csv.tmp /tmp/fetch_test_output_filtered.csv /tmp/fetch_test_output_filtered.csv.tmp /tmp/fetch_test_bad.csv.tmp
```

- [ ] **Step 8: Commit**

```bash
git add DOT-Commercial/fetch_commercial_carriers.py
git commit -m "Add core Socrata pagination fetch logic for DOT-Commercial"
```

---

### Task 2: CLI wiring and `fetch-config.json`

**Files:**
- Create: `DOT-Commercial/configuration/fetch-config.json`
- Modify: `DOT-Commercial/fetch_commercial_carriers.py` (append `main()`)

**Interfaces:**
- Consumes: `compute_where_clause`, `fetch_dataset` from Task 1 (same file). `file_utils.load_from_file(file_name) -> SimpleNamespace | None` from `utils/file_utils.py` (already exists, unchanged).
- Produces: CLI entrypoint `python3 fetch_commercial_carriers.py [--dataset=NAME]`, run from `DOT-Commercial/`.

- [ ] **Step 1: Create the production fetch config**

Create `DOT-Commercial/configuration/fetch-config.json`:

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

- [ ] **Step 2: Write the failing verification script**

Create `/tmp/verify_main.py`:

```python
import subprocess, os, json

os.makedirs("/tmp/main_test_config_dir/configuration", exist_ok=True)
test_config = {
    "base_url": "http://localhost:8090",
    "app_token_env_var": "SOCRATA_APP_TOKEN_TEST_UNUSED",
    "page_size": 100,
    "datasets": {
        "fixture_a": {"dataset_id": "fixture", "output": "/tmp/main_test_a.csv", "date_field": None, "window_months": None},
        "fixture_b": {"dataset_id": "fixture", "output": "/tmp/main_test_b.csv", "date_field": None, "window_months": None},
    },
}
with open("/tmp/main_test_config_dir/configuration/fetch-config.json", "w") as f:
    json.dump(test_config, f)

script_path = os.path.abspath("DOT-Commercial/fetch_commercial_carriers.py")

result = subprocess.run(
    ["python3", script_path],
    cwd="/tmp/main_test_config_dir",
    capture_output=True,
    text=True,
)
assert result.returncode == 0, result.stderr
assert os.path.exists("/tmp/main_test_a.csv")
assert os.path.exists("/tmp/main_test_b.csv")

with open("/tmp/main_test_a.csv") as f:
    lines = f.readlines()
assert len(lines) == 251, len(lines)  # header + 250 rows

os.remove("/tmp/main_test_a.csv")
os.remove("/tmp/main_test_b.csv")

result2 = subprocess.run(
    ["python3", script_path, "--dataset=fixture_a"],
    cwd="/tmp/main_test_config_dir",
    capture_output=True,
    text=True,
)
assert result2.returncode == 0, result2.stderr
assert os.path.exists("/tmp/main_test_a.csv")
assert not os.path.exists("/tmp/main_test_b.csv"), "fixture_b should not have been fetched"

result3 = subprocess.run(
    ["python3", script_path, "--dataset=nonexistent"],
    cwd="/tmp/main_test_config_dir",
    capture_output=True,
    text=True,
)
assert result3.returncode != 0, "expected non-zero exit for unknown dataset"

print("OK")
```

- [ ] **Step 3: Run it to verify it fails**

Ensure the fixture server from Task 1 is running (if not: `.venv/bin/python3 /tmp/fixture_server.py 8090 &` — reuse the script content from Task 1 Step 1 if it was cleaned up, since Task 1 deleted it in its own Step 7; recreate it with the identical content shown in Task 1 Step 1 if needed).

Run: `.venv/bin/python3 /tmp/verify_main.py`
Expected: `AssertionError` (non-zero exit) because `fetch_commercial_carriers.py` has no `--dataset` argument handling yet and doesn't read `configuration/fetch-config.json` from the invoking directory.

- [ ] **Step 4: Implement `main()`**

Append to `DOT-Commercial/fetch_commercial_carriers.py`:

```python
import argparse
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils.file_utils as file_utils

FETCH_CONFIG_FILE_NAME = "configuration/fetch-config.json"


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        required=False,
        default=None,
        help="Fetch a single dataset by name (e.g. carriers, crashes, inspections). Fetches all configured datasets if omitted.",
    )
    args = parser.parse_args()

    config = file_utils.load_from_file(FETCH_CONFIG_FILE_NAME)
    if not config:
        logger.critical("Could not load {}".format(FETCH_CONFIG_FILE_NAME))
        sys.exit(1)

    app_token = os.environ.get(config.app_token_env_var)

    dataset_names = list(vars(config.datasets).keys())
    if args.dataset:
        if args.dataset not in dataset_names:
            logger.critical(
                "Unknown dataset: {}. Known datasets: {}".format(
                    args.dataset, dataset_names
                )
            )
            sys.exit(1)
        dataset_names = [args.dataset]

    session = requests.Session()
    for name in dataset_names:
        dataset_config = getattr(config.datasets, name)
        logger.info("Fetching dataset: {}".format(name))
        total = fetch_dataset(
            session=session,
            base_url=config.base_url,
            dataset_id=dataset_config.dataset_id,
            output_path=dataset_config.output,
            date_field=dataset_config.date_field,
            window_months=dataset_config.window_months,
            page_size=config.page_size,
            app_token=app_token,
        )
        logger.info("Fetched {} rows for {}".format(total, name))


if __name__ == "__main__":
    main()
```

The `import os` and `from datetime import datetime, timedelta` from Task 1 already cover this addition's needs except for the three new imports (`argparse`, `sys`, `requests`) and the `utils.file_utils` import — add those at the top of the file alongside Task 1's existing imports.

- [ ] **Step 5: Run the verification script to confirm it passes**

Run: `.venv/bin/python3 /tmp/verify_main.py`
Expected: `OK`

- [ ] **Step 6: Stop the fixture server and clean up scratch files**

```bash
kill $FIXTURE_PID 2>/dev/null
rm -f /tmp/fixture_server.py /tmp/verify_main.py /tmp/main_test_a.csv /tmp/main_test_a.csv.tmp /tmp/main_test_b.csv /tmp/main_test_b.csv.tmp
rm -rf /tmp/main_test_config_dir
```

- [ ] **Step 7: Commit**

```bash
git add DOT-Commercial/fetch_commercial_carriers.py DOT-Commercial/configuration/fetch-config.json
git commit -m "Add CLI entrypoint and fetch-config.json for DOT-Commercial Socrata fetch"
```

---

### Task 3: Update `index-config.json` files

**Files:**
- Modify: `DOT-Commercial/configuration/carriers/index-config.json`
- Modify: `DOT-Commercial/configuration/crashes/index-config.json`
- Modify: `DOT-Commercial/configuration/inspections/index-config.json`

**Interfaces:**
- Consumes: the fixed output filenames `fetch_dataset` writes to (`carriers.csv`, `crashes.csv`, `inspections.csv`, per Task 2's `fetch-config.json`).
- Produces: `source` fields these three files present to `phase_index_populate.py`'s `handle()` (unchanged code, existing behavior).

**Important:** `phase_index_populate.py`'s `handle()` constructs `CsvLoadUtils(..., index_config.num_rows, ...)` via **direct attribute access with no `try/except`** (a second, later use of `num_rows` for the ES-indexing cap does have a try/except, but this earlier one does not). Fully **removing** the `num_rows` key would make that direct access raise `AttributeError` and crash the phase. Setting it to JSON `null` (Python `None`) is required instead — `pandas.read_csv(nrows=None, ...)` and `itertools.islice(records, None)` both correctly mean "no limit," so `null` achieves "index everything fetched" without changing any Python code.

- [ ] **Step 1: Update `carriers/index-config.json`**

Replace its contents with:

```json
{
    "index": "carriers-{now/d}-000001",
    "alias": "carriers-000001",
    "source": "carriers.csv",
    "id_field": "DOT_NUMBER",
    "pipeline": "carrier-enrichment-pipeline-000001",
    "num_rows": null,
    "skip_rows": 0
}
```

- [ ] **Step 2: Update `crashes/index-config.json`**

Replace its contents with:

```json
{
    "index": "crashes-{now/d}-000001",
    "alias": "crashes-000001",
    "source": "crashes.csv",
    "pipeline": "crashes-pipeline-000001",
    "num_rows": null,
    "skip_rows": 0
}
```

- [ ] **Step 3: Update `inspections/index-config.json`**

Replace its contents with:

```json
{
    "alias": "inspections-000001",
    "index": "inspections-{now/d}-000001",
    "source": "inspections.csv",
    "id_field": "UNIQUE_ID",
    "num_rows": null,
    "skip_rows": 0
}
```

- [ ] **Step 4: Verify all three parse correctly and resolve as expected**

Run:
```bash
.venv/bin/python3 -c "
import json
for step, expected_source in [
    ('carriers', 'carriers.csv'),
    ('crashes', 'crashes.csv'),
    ('inspections', 'inspections.csv'),
]:
    with open(f'DOT-Commercial/configuration/{step}/index-config.json') as f:
        config = json.load(f)
    assert config['source'] == expected_source, (step, config['source'])
    assert config['num_rows'] is None, (step, config['num_rows'])
print('OK')
"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add DOT-Commercial/configuration/carriers/index-config.json DOT-Commercial/configuration/crashes/index-config.json DOT-Commercial/configuration/inspections/index-config.json
git commit -m "Point DOT-Commercial index-config.json at fetched CSVs, remove 50000-row cap"
```

---

### Task 4: Switch `CsvLoadUtils` to UTF-8

**Files:**
- Modify: `utils/csv_load_utils.py:29` (the `encoding="windows-1252"` argument inside `pd.read_csv(...)`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `CsvLoadUtils.load_csv()` now decodes files as UTF-8 instead of windows-1252. This affects every project using `CsvLoadUtils`, including `CMS-Providers` — validated for that project in Task 6.

- [ ] **Step 1: Write the failing verification script**

Create `/tmp/verify_csv_encoding.py`:

```python
import sys, os, tempfile
sys.path.insert(0, ".")
from utils.csv_load_utils import CsvLoadUtils

with tempfile.TemporaryDirectory() as tmp:
    project = os.path.join(tmp, "TestProject")
    step_dir = os.path.join(project, "data", "carriers")
    os.makedirs(step_dir)
    csv_path = os.path.join(step_dir, "carriers.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("DOT_NUMBER,LEGAL_NAME\n")
        f.write("123,CAFÉ TRUCKING LLC\n")

    loader = CsvLoadUtils(project, "data", "carriers", "carriers.csv", None, 0)
    data = loader.load_csv()
    assert len(data) == 1, len(data)
    assert data.iloc[0]["LEGAL_NAME"] == "CAFÉ TRUCKING LLC", data.iloc[0]["LEGAL_NAME"]

print("OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python3 /tmp/verify_csv_encoding.py`
Expected: `AssertionError: CAFÃ‰ TRUCKING LLC` (the UTF-8 bytes for "É" get mis-decoded as two separate windows-1252 characters — a mangled but non-crashing result, since windows-1252 has a defined mapping for those byte values).

- [ ] **Step 3: Change the encoding**

In `utils/csv_load_utils.py`, change:
```python
        results = pd.read_csv(
            file_path,
            encoding="windows-1252",
            nrows=self.num_rows,
            header=0,
            skiprows=skip_rows,
        )
```
to:
```python
        results = pd.read_csv(
            file_path,
            encoding="utf-8",
            nrows=self.num_rows,
            header=0,
            skiprows=skip_rows,
        )
```

- [ ] **Step 4: Run the verification script to confirm it passes**

Run: `.venv/bin/python3 /tmp/verify_csv_encoding.py`
Expected: `OK`

- [ ] **Step 5: Clean up and commit**

```bash
rm -f /tmp/verify_csv_encoding.py
git add utils/csv_load_utils.py
git commit -m "Switch CsvLoadUtils to utf-8 to match Socrata CSV export encoding"
```

---

### Task 5: Remove the old download script, update docs

**Files:**
- Delete: `DOT-Commercial/download_commercial_carriers.sh`
- Modify: `DOT-Commercial/README.md:1`
- Modify: `README.md:100` (root)

- [ ] **Step 1: Delete the old download script**

```bash
git rm DOT-Commercial/download_commercial_carriers.sh
```

- [ ] **Step 2: Update `DOT-Commercial/README.md`**

Replace line 1 (`DOT Commercial https://ai.fmcsa.dot.gov/SMS/Tools/Downloads.aspx`) with:

```markdown
DOT Commercial https://data.transportation.gov/Trucking-and-Motorcoaches/

## Fetching Data
Run `python3 fetch_commercial_carriers.py` from this directory to pull the latest carrier census, crash, and inspection data from the data.transportation.gov Socrata API. Optionally pass `--dataset=<carriers|crashes|inspections>` to fetch just one. See `configuration/fetch-config.json` for dataset IDs and the crash/inspection lookback window.
```

- [ ] **Step 3: Update the root `README.md`'s generic download-script reference**

In `README.md`, change:
```markdown
    * Use the `download.....sh` script in one of the example directories
```
to:
```markdown
    * Use the download or fetch script in one of the example directories (e.g. `download_cms_provider.sh`, `fetch_commercial_carriers.py`)
```

- [ ] **Step 4: Verify cleanup is complete**

```bash
test ! -f DOT-Commercial/download_commercial_carriers.sh && echo "old script gone"
grep -c "ai.fmcsa.dot.gov/SMS/Tools/Downloads.aspx" DOT-Commercial/README.md || echo "old URL removed"
```
Expected: `old script gone` printed, and the `grep` reports no matches (its own non-zero exit triggers the `|| echo` fallback, printing `old URL removed`).

- [ ] **Step 5: Commit**

```bash
git add DOT-Commercial/README.md README.md
git commit -m "Remove old FMCSA download script, document the new fetch script"
```

---

### Task 6: Validate against the real Socrata API and real Elasticsearch

**Files:** none (validation only; no code changes expected).

**Interfaces:**
- Consumes: Tasks 1-5 (the complete fetch script, config, index-config.json changes, and encoding fix).

- [ ] **Step 1: Fetch each dataset from the real API**

From `DOT-Commercial/`:
```bash
../.venv/bin/python3 fetch_commercial_carriers.py --dataset=carriers
../.venv/bin/python3 fetch_commercial_carriers.py --dataset=crashes
../.venv/bin/python3 fetch_commercial_carriers.py --dataset=inspections
```
Expected: each completes with exit code 0, logs a total row count, and writes `data/carriers/carriers.csv`, `data/crashes/crashes.csv`, `data/inspections/inspections.csv` respectively. Census should be roughly 2 million rows; crash and inspection should each be noticeably smaller than their full historical totals (millions), reflecting the 24-month window.

- [ ] **Step 2: Spot-check the date window**

```bash
.venv/bin/python3 -c "
import csv
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=30*24)).strftime('%Y%m%d')
with open('DOT-Commercial/data/inspections/inspections.csv') as f:
    reader = csv.DictReader(f)
    dates = [row['insp_date'] for row in reader]
assert all(d > cutoff for d in dates), 'found a row older than the 24-month window'
print('OK, {} rows, oldest date {}'.format(len(dates), min(dates)))
"
```
Expected: `OK, <N> rows, oldest date <YYYYMMDD near the cutoff>` with no assertion error.

- [ ] **Step 3: Set up Elasticsearch connectivity**

Ensure a local Elasticsearch cluster is reachable (`curl -s localhost:9200` should return cluster info) and `es_config.json` exists at the repo root (copy from wherever it's already configured in this environment if missing — it's gitignored, e.g. `{"timeout": 180, "host": "localhost", "port": 9200, "scheme": "http", "username": "", "password": ""}`).

- [ ] **Step 4: Run the full pipeline end-to-end**

From the repo root:
```bash
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=crashes-ingestion-setup
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=crashes --phase=index-populate
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=inspections --phase=index-populate
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers-ingestion-setup
.venv/bin/python3 execute_project.py --project=DOT-Commercial --step=carriers --phase=index-populate
```
Expected: each logs `Indexing <N> records into index ...` with `<N>` matching (or very close to, since the live table can change between fetch and this check) the row counts from Step 1/2 — not capped at 50,000. No file-not-found errors (confirms `source` correctly resolves to the fetched CSV) and no encoding-related errors.

- [ ] **Step 5: Confirm non-ASCII characters round-trip correctly**

```bash
curl -s "localhost:9200/carriers-000001/_search?q=LEGAL_NAME:*&size=1&pretty" | grep -i "LEGAL_NAME" | head -5
```
Manually inspect the output for any carrier name containing accented or non-ASCII characters and confirm it reads correctly (not mangled/mojibake). If none appear in the first few results, this is a soft check — not a hard failure condition, since it depends on what's actually in the live dataset at fetch time.

- [ ] **Step 6: Regression-check `CMS-Providers` after the encoding change**

From the repo root (only the `hospitals` step has local data already downloaded in this environment):
```bash
.venv/bin/python3 execute_project.py --project=CMS-Providers --step=hospitals --phase=index-populate
```
Expected: completes without error, indexes the expected number of hospital records, and any text fields with special characters (e.g. hospital names with punctuation) still look correct — confirming the global `utf-8` switch didn't break this project's existing data.

- [ ] **Step 7: Confirm old artifacts are fully gone**

```bash
test ! -f DOT-Commercial/download_commercial_carriers.sh && echo "confirmed: old script removed"
grep -rn "windows-1252" utils/csv_load_utils.py || echo "confirmed: encoding switched"
grep -rn "resolve_source_filename\|load_key_value_file" --include="*.py" . | grep -v .venv || echo "confirmed: no leftover PR #1 templating code"
```

No commit for this task — it's a validation pass. If any step surfaces a real defect (not an expected outcome like "row counts drifted slightly because the live table changed between runs"), stop and report it rather than proceeding.
