"""Produce a project's declared metric record from a scored-pair index.

The config-driven counterpart to a project writing its own summarize() in
Python. Emits exactly the shape `utils.sweep_compare.compare()` consumes, so a
project's committed expectation files keep working unchanged.

    .venv/bin/python scripts/run_metrics.py \\
        --project DOT-Commercial --step chameleon-detection \\
        --index chameleon-candidates-2026.08.17-000001

The `_source` fields fetched are DERIVED from the configured predicates rather
than hardcoded. A hardcoded list is how a scan silently stops feeding a newly
added predicate: the field is simply absent from every document, the predicate
reads it as missing, and the metric quietly reports a smaller number than the
data supports. scripts/compare_sweeps.py carries exactly such a list.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

from utils.metric_runner import summarize

SCAN_SIZE = 2000

# Fields every pair carries that some predicate or metric kind may read. Paths
# named by config (fields_equal, distinct) are added to these at run time.
_ALWAYS_READ = ("total_score", "gap_days", "matched_on", "signals")


def source_fields(metrics: list[dict]) -> list[str]:
    """The _source fields this metrics config actually needs.

    Restricting the scan matters at this scale: a full pair document carries
    the per-signal contribution array, roughly ten times the bytes, and a
    several-hundred-thousand-pair scan reads all of it otherwise.
    """
    wanted = set(_ALWAYS_READ)

    def walk(predicate):
        for name, value in (predicate or {}).items():
            if name == "fields_equal":
                wanted.add("predecessor.{}".format(value))
                wanted.add("successor.{}".format(value))
            elif name in ("all", "any"):
                for clause in value:
                    walk(clause)
            elif name == "not":
                walk(value)

    for metric in metrics:
        walk(metric.get("filter"))
        if "distinct" in metric:
            wanted.add(metric["distinct"])
    return sorted(wanted)


def load_metrics(project: str, step: str) -> dict:
    path = Path(project) / "configuration" / step / "metrics.json"
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument(
        "--write-baseline",
        default=None,
        help="Write the record here as JSON, for use as a future baseline",
    )
    args = parser.parse_args()

    config = load_metrics(args.project, args.step)
    fields = source_fields(config["metrics"])
    es = Elasticsearch(args.host)
    rows = scan(
        es,
        index=args.index,
        query={"query": {"match_all": {}}},
        _source=fields,
        size=SCAN_SIZE,
    )
    record = summarize(config["metrics"], (hit["_source"] for hit in rows))

    print(json.dumps(record, indent=2, sort_keys=True))
    if args.write_baseline:
        Path(args.write_baseline).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        print("\nwrote {}".format(args.write_baseline), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
