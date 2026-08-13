"""Report which of a project's CSV columns will land as `text` for want of a pin.

Run this before configuring a dataset, and again before any reload. The loader
reads every column as a string so that index mappings decide types -- inference
in pandas destroyed leading zeros before Elasticsearch was reached -- which
means a column absent from index-mappings.json becomes `text`, not a number.

That is easy to miss precisely because nothing fails: the load succeeds, the
phase logs success, and the field is simply the wrong type until someone runs a
range query or an aggregation against it. This project has already paid for
that lesson twice, so the check is a script rather than a paragraph in a README.

    .venv/bin/python scripts/check_mapping_coverage.py --project=DOT-Commercial

Exits non-zero when any column is unpinned, so it can gate a reload.
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.mapping_coverage import compare, looks_like_date, recommend_type

# How many rows to read when guessing what an unpinned column should be. The
# recommendation is advisory, so a sample is enough; reading 5.6M rows to
# suggest a type nobody is obliged to take would make the check too slow to run
# habitually, and a check that is not run habitually is not a check.
SAMPLE_ROWS = 20000


def load_json(path):
    with open(path) as handle:
        return json.load(handle)


def dataset_steps(project, project_config, only_step):
    for step in project_config.get("steps", []):
        name = step.get("name")
        if only_step and name != only_step:
            continue
        config_dir = Path(project) / project_config.get("configurationDir", "configuration") / name
        if not (config_dir / "index-config.json").exists():
            continue
        yield name, config_dir


def sample_columns(csv_path):
    """Header order plus up to SAMPLE_ROWS values per column, as written on disk.

    Uses csv rather than pandas deliberately: this check is about what the file
    literally contains, and pandas' type inference is the very thing whose
    consequences it exists to report.
    """
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], {}
        samples = {name: [] for name in header}
        for count, row in enumerate(reader):
            if count >= SAMPLE_ROWS:
                break
            # strict=False deliberately: a short or ragged row is a data defect
            # for profile_dataset.py to report, and raising here would stop the
            # coverage check over a problem it is not the one to diagnose.
            for name, value in zip(header, row, strict=False):
                samples[name].append(value)
        return header, samples


def report_step(project, project_config, name, config_dir):
    index_config = load_json(config_dir / "index-config.json")
    mappings = load_json(config_dir / "index-mappings.json")
    properties = mappings.get("mappings", {}).get("properties", {})

    # A pin with no `type` is an enrichment target: the processor writes an
    # object there after load, so it is legitimately absent from the CSV.
    enriched = [k for k, v in properties.items() if not v.get("type")]

    csv_path = (
        Path(project)
        / project_config.get("dataDir", "data")
        / name
        / index_config.get("source", "")
    )
    if not csv_path.exists():
        print(f"  {name}: SKIPPED, no source file at {csv_path}")
        return 0

    header, samples = sample_columns(csv_path)
    result = compare(header, list(properties), enriched=enriched)

    if result.covered:
        print(f"  {name}: all {len(header)} columns pinned")
        return 0

    print(f"  {name}: {len(result.unpinned)} of {len(header)} columns unpinned -> will map as text")
    for column in result.unpinned:
        values = samples.get(column, [])
        suggestion = recommend_type(values)
        if suggestion:
            detail = f"suggest {suggestion}"
        elif looks_like_date(values):
            detail = "NON-ISO DATE: lands as text, and `date` mis-pivots the century"
        else:
            detail = "text is probably right"
        print(f"      {column:34} {detail}")
    for column in result.dead:
        print(f"      {column:34} PINNED BUT NOT IN CSV (stale config)")
    return len(result.unpinned)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Ex: --project=DOT-Commercial")
    parser.add_argument("--step", help="Check a single step instead of all of them")
    args = parser.parse_args()

    config_path = os.path.join(args.project, "configuration.json")
    if not os.path.exists(config_path):
        parser.error(f"no configuration.json under {args.project}")
    project_config = load_json(config_path)

    print(f"Mapping coverage for {args.project}")
    unpinned = 0
    for name, config_dir in dataset_steps(args.project, project_config, args.step):
        unpinned += report_step(args.project, project_config, name, config_dir)

    if unpinned:
        print(f"\n{unpinned} unpinned columns. Each one will be indexed as text.")
        return 1
    print("\nEvery column is pinned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
