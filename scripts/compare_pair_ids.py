"""Compare two pair populations by _id set, not by aggregate counts.

Exists because metric equality is not proof that a sweep produced the same
pairs. Every count in a metric record is an aggregate, and a pair lost and a
pair gained inside the same score band cancel out in all of them at once — so
a refactor that quietly swapped one pair for another would clear the metric
comparison with every number identical. This is the check no count can satisfy.

It is deliberately not part of `utils.sweep_compare`, which answers a different
question: whether an INTENTIONAL change moved the right metrics in the right
direction. Its vocabulary (must_not_fall, must_not_rise, within_10pct,
informational) has no way to express "must not change at all", so running a
must-not-move comparison through it would let a real regression pass wearing
`informational`.

Composite pair ids are label-independent by construction — `compute_id` is
called over literal p/s keys, never the project's own name for its entity key —
so this comparison stays valid across a rename of that key.

    .venv/bin/python scripts/compare_pair_ids.py \\
        --baseline-index chameleon-candidates-2026.08.13-000001 \\
        --candidate-index chameleon-candidates-2026.08.17-000001
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

SCAN_SIZE = 5000

# How many differing ids to print per side. A full list of a large divergence
# scrolls the summary off the screen, and the first handful is what an operator
# actually pulls up to diagnose; the counts above it carry the magnitude.
SAMPLE_SIZE = 20


@dataclass
class IdSetDiff:
    """Which pair ids appear on only one side, and whether the sets agree.

    Both directions are kept rather than a single symmetric difference: "pairs
    the refactor lost" and "pairs it invented" are different defects with
    different causes, and collapsing them would hide which one happened.
    """

    only_in_baseline: list[str] = field(default_factory=list)
    only_in_candidate: list[str] = field(default_factory=list)
    counts: tuple[int, int] = (0, 0)

    @property
    def identical(self) -> bool:
        return not self.only_in_baseline and not self.only_in_candidate


def diff_id_sets(baseline: set, candidate: set) -> IdSetDiff:
    """Compare two pair-id sets, sorted so a report is stable across runs.

    Sorted because an unstable ordering cannot be diffed against a previous
    run's output, which is the first thing anyone does with a failure.
    """
    return IdSetDiff(
        only_in_baseline=sorted(baseline - candidate),
        only_in_candidate=sorted(candidate - baseline),
        counts=(len(baseline), len(candidate)),
    )


def scan_ids(es, index) -> set:
    """Every document _id in an index.

    _source=False because only the ids matter here, which keeps a
    several-hundred-thousand-pair scan to the id field rather than pulling the
    per-signal contribution array with it.
    """
    return {hit["_id"] for hit in scan(es, index=index, _source=False, size=SCAN_SIZE)}


def print_report(diff: IdSetDiff, baseline_index, candidate_index):
    baseline_count, candidate_count = diff.counts
    print("baseline  {}: {:,} pairs".format(baseline_index, baseline_count))
    print("candidate {}: {:,} pairs".format(candidate_index, candidate_count))

    if diff.identical:
        print("\nIDENTICAL: both populations contain exactly the same pair ids")
        return

    print(
        "\nDIFFERENT: {:,} only in baseline, {:,} only in candidate".format(
            len(diff.only_in_baseline), len(diff.only_in_candidate)
        )
    )
    for label, ids in (
        ("lost (in baseline, not candidate)", diff.only_in_baseline),
        ("gained (in candidate, not baseline)", diff.only_in_candidate),
    ):
        if not ids:
            continue
        print("\n{} — first {}:".format(label, min(SAMPLE_SIZE, len(ids))))
        for pair_id in ids[:SAMPLE_SIZE]:
            print("  {}".format(pair_id))
        if len(ids) > SAMPLE_SIZE:
            print("  ... and {:,} more".format(len(ids) - SAMPLE_SIZE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-index", required=True)
    parser.add_argument("--candidate-index", required=True)
    parser.add_argument("--host", default="http://localhost:9200")
    args = parser.parse_args()

    es = Elasticsearch(args.host)
    diff = diff_id_sets(
        scan_ids(es, args.baseline_index), scan_ids(es, args.candidate_index)
    )
    print_report(diff, args.baseline_index, args.candidate_index)
    return 0 if diff.identical else 1


if __name__ == "__main__":
    sys.exit(main())
