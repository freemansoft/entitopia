"""Pull a stratified sample of scored pairs for a human to adjudicate.

Every metric in DOT-Commercial/precision_metrics.py measures precision-shaped
properties: coherence, corroboration, the canary shape. None of them can
measure recall, because there is no list of known chameleon carriers to check
the sweep against — a change that passes every guarded metric can still have
thrown away real matches. This is the cheapest available substitute: pull a
small sample stratified across score bands (so the low-score noise band and
the high-score canary tier are both represented, not just whichever is
largest) and let a human say, pair by pair, whether it looks like a real
chameleon. It writes a sample, not a verdict — nothing here decides what a
pair is, and nothing downstream reads the verdict field it leaves empty.

Output is gitignored under */data/, which is deliberate: the rows carry the
legal names and addresses of carriers the matcher flagged, and those must not
enter the repo.
"""

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

_PRECISION_METRICS = Path(__file__).resolve().parent.parent / "precision_metrics.py"


def _load_precision_metrics():
    """Load DOT-Commercial/precision_metrics.py by path.

    Same hyphenated-directory problem tests/test_dot_commercial_precision_metrics.py
    solves the same way: DOT-Commercial/ cannot be a dotted module, and this
    script is a sibling of the module it needs rather than something importable
    from the repo root.
    """
    spec = importlib.util.spec_from_file_location("precision_metrics", _PRECISION_METRICS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRIAGE_SCORE = _load_precision_metrics().TRIAGE_SCORE

# Anchored on precision_metrics.TRIAGE_SCORE rather than repeating its value,
# so this sample keeps answering the same question the rest of the precision
# metrics answer. The triage tier has to be a band edge, not a value falling
# mid-band: a reviewer judging "the pairs triage would surface" must not get
# them blended with pairs it would drop. The other edges are the README's own
# reporting bands — below 0.50 is the noise it calls out, and the top band
# isolates the near-certain shapes the canary check watches.
SAMPLE_BANDS = [
    (0.35, 0.50, "0.35-0.50"),
    (0.50, TRIAGE_SCORE, "0.50-0.70"),
    (TRIAGE_SCORE, 0.90, "0.70-0.90"),
    (0.90, 1.00, "0.90-1.00"),
]

SOURCE_FIELDS = [
    "total_score",
    "gap_days",
    "matched_on",
    "predecessor.dot_number",
    "predecessor.legal_name",
    "predecessor.phy_street",
    "predecessor.shutdown_date",
    "successor.dot_number",
    "successor.legal_name",
    "successor.phy_street",
    "successor.add_date",
]


def _band(score):
    """Which review band a score falls in, or None when it falls outside them all.

    None rather than the nearest band: a pair under the sweep's own 0.35 floor
    is not something a reviewer should spend a quota slot on, and silently
    bucketing it into the lowest band would misreport what that band contains.
    """
    for lower, upper, label in SAMPLE_BANDS:
        if lower <= score < upper:
            return label
    if score == SAMPLE_BANDS[-1][1]:
        return SAMPLE_BANDS[-1][2]
    return None


def stratified_sample(pairs, per_band, seed):
    """Up to per_band pairs from each band, chosen deterministically for a given seed.

    Determinism matters here specifically: an unreproducible sample can never
    be handed to a second reviewer to check agreement, which is the first
    thing anyone will want to do with a small human-labelled set.

    Stratified rather than uniform because the population is overwhelmingly
    low-scoring — a uniform draw would spend nearly every slot in the noise
    band and tell a reviewer nothing about the tier that actually gets acted
    on.
    """
    by_band = {}
    for row in pairs:
        label = _band(row.get("total_score") or 0.0)
        if label is None:
            continue
        by_band.setdefault(label, []).append(row)

    rng = random.Random(seed)
    sample = []
    for _, _, label in SAMPLE_BANDS:
        bucket = by_band.get(label, [])
        rng.shuffle(bucket)
        sample.extend(bucket[:per_band])
    return sample


def build_review_row(pair):
    """One JSON line a human reads and fills in. verdict starts null, always.

    A default other than null would read as an answer nobody gave; None is
    the same "not evaluable" distinction the rest of this codebase already
    draws between an unscored signal and a scored zero.
    """
    return {
        "predecessor": pair["predecessor"],
        "successor": pair["successor"],
        "total_score": pair["total_score"],
        "gap_days": pair.get("gap_days"),
        "matched_on": pair.get("matched_on"),
        "verdict": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", required=True)
    parser.add_argument("--per-band", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--out", default="DOT-Commercial/data/precision/review-sample.jsonl")
    args = parser.parse_args()

    es = Elasticsearch(args.host)
    hits = scan(
        es,
        index=args.pairs_index,
        query={"query": {"match_all": {}}},
        _source=SOURCE_FIELDS,
        size=2000,
    )
    rows = [hit["_source"] for hit in hits]
    sample = stratified_sample(rows, args.per_band, args.seed)

    with open(args.out, "w") as handle:
        for row in sample:
            handle.write(json.dumps(build_review_row(row)) + "\n")

    counts = {}
    for row in sample:
        label = _band(row["total_score"])
        counts[label] = counts.get(label, 0) + 1
    print("scanned {} pairs from {}".format(len(rows), args.pairs_index))
    print("wrote {} pairs to {}: {}".format(len(sample), args.out, counts))


if __name__ == "__main__":
    sys.exit(main())
