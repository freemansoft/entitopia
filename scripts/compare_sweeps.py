"""Diff two chameleon sweeps and fail loudly when a guarded metric regressed.

Every change in the precision plan is expected to shrink the pair population,
so "fewer pairs" is not evidence of improvement — losing real matches shrinks
it too. This reads both sweeps by explicit index name rather than through the
chameleon-candidates alias, because during an experiment the alias is exactly
the thing in motion and resolving it would compare a run against itself.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

from utils.sweep_compare import compare

# DOT-Commercial/ cannot be a dotted import (`import DOT_Commercial...` is
# invalid syntax because of the hyphen), and nothing else in this repo has
# ever imported across that directory boundary — fetch_commercial_carriers.py,
# the other module living there, is run as a script instead. Loaded by path
# rather than inventing a rename or symlink, mirroring how
# DOT-Commercial/scripts/measure_chameleon_shape.py reaches its project's JSON config and
# how tests/test_crash_lift.py and tests/test_profile_dataset.py already load
# scripts/ modules that aren't part of an importable package.
_PRECISION_METRICS = Path(__file__).resolve().parent.parent / "DOT-Commercial" / "precision_metrics.py"
_spec = importlib.util.spec_from_file_location("precision_metrics", _PRECISION_METRICS)
precision_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precision_metrics)
METRICS = precision_metrics.METRICS
summarize = precision_metrics.summarize

# Only the fields summarize() reads. A full pair _source carries the per-signal
# contribution array, which is roughly ten times the bytes and is never used
# here; restricting the scan is what keeps a 400k-pair diff to a few minutes.
SOURCE_FIELDS = [
    "total_score",
    "gap_days",
    "matched_on",
    "predecessor.dot_number",
    "predecessor.legal_name",
    "successor.legal_name",
]

SCAN_SIZE = 2000


def summarize_index(es, index):
    rows = scan(
        es,
        index=index,
        query={"query": {"match_all": {}}},
        _source=SOURCE_FIELDS,
        size=SCAN_SIZE,
    )
    return summarize(hit["_source"] for hit in rows)


def load_expectations(path):
    with open(path) as handle:
        return json.load(handle)


def print_table(deltas):
    print("\n{:<24} {:>12} {:>12} {:>12} {:>9}  {}".format(
        "metric", "baseline", "candidate", "delta", "pct", "expectation"))
    for d in deltas:
        pct = "-" if d.pct is None else "{:+.1%}".format(d.pct)
        mark = "ok" if d.ok else "REGRESSED"
        print("{:<24} {:>12} {:>12} {:>12} {:>9}  {} [{}]".format(
            d.name,
            round(d.baseline, 4),
            round(d.candidate, 4),
            round(d.delta, 4),
            pct,
            d.expectation,
            mark,
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-index", required=True)
    parser.add_argument("--candidate-index", required=True)
    parser.add_argument("--expectations", required=True,
                        help="JSON file mapping metric name to expectation")
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--write-baseline", default=None,
                        help="Write the candidate summary here as the new baseline")
    args = parser.parse_args()

    es = Elasticsearch(args.host)
    baseline = summarize_index(es, args.baseline_index)
    candidate = summarize_index(es, args.candidate_index)

    print("baseline  {}: {}".format(args.baseline_index, baseline))
    print("candidate {}: {}".format(args.candidate_index, candidate))

    deltas = compare(baseline, candidate, load_expectations(args.expectations))
    print_table(deltas)

    if args.write_baseline:
        with open(args.write_baseline, "w") as handle:
            json.dump(candidate, handle, indent=2, sort_keys=True)
        print("\nwrote candidate summary to {}".format(args.write_baseline))

    regressed = [d.name for d in deltas if not d.ok]
    unguarded = sorted(set(METRICS) - set(load_expectations(args.expectations)))
    if unguarded:
        print("\nnote: no expectation declared for {}".format(", ".join(unguarded)))
    if regressed:
        print("\nREGRESSED: {}".format(", ".join(regressed)))
        return 1
    print("\nno guarded metric regressed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
