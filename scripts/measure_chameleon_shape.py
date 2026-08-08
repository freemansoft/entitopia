"""Check whether the top-scoring pairs are shaped like chameleons, not just scored like ones.

measure_crash_lift.py tests a PROXY: whether the score predicts a later crash.
That measurement found no lift, but a null proxy result cannot distinguish
"the matcher is wrong" from "the proxy is wrong" — GAO-12-364's crash effect is
itself weak and confounded by fleet size. This script checks the thing the
project actually claims to find, straight from its own definition:
`DOT-Commercial/README.md` defines a chameleon as a carrier "shut down... that
reopen[s] under a new DOT number", which is a temporal claim — the successor
must register AFTER the predecessor's shutdown. `gap_days` (successor
`add_date` minus predecessor `shutdown_date`) already carries that sign on
every emitted pair, so this needs no labelled outcome and no new data: a
negative `gap_days` at a high score is a direct, unambiguous miss.

Baseline measured 2026-08-07: among 1,729 pairs scoring >= 0.70, the gap
distribution is 370 / 519 / 435 / 405 across the four bands below; mean
total_score is 0.4425 over 306,401 pre-shutdown pairs (gap_days < 0) and
0.4520 over 115,445 post-shutdown pairs (gap_days >= 0). Those figures predate
the stop-list correction referenced in the repo's recent commits, so a run
against a re-swept index is expected to differ — but a run against the SAME
index disagreeing means the query changed meaning, not the data, and that is a
bug to find, not a number to adjust the bands to match.
"""

import argparse
import sys
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_chameleon_shape.py`, which puts
# scripts/ on sys.path rather than the repo root, so utils.crash_lift is
# unimportable without this. Same fix as measure_crash_lift.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch

from utils.crash_lift import GAP_BANDS, SCORE_BANDS, rate

# One definition of the gap-band edges as an Elasticsearch range spec, reused
# by both the score/gap matrix and the temporal-signal check below. Elastic's
# `range` aggregation and `gap_band()` in utils/crash_lift.py express the same
# four boundaries in two different syntaxes (a range agg has no equivalent of
# gap_band()'s sequential "first limit you're under" loop); keeping the
# derivation in one place is what stops the two definitions drifting apart if
# the bands in the brief ever change.
GAP_RANGES = [{"to": limit} if i == 0 else {"from": GAP_BANDS[i - 1][0], "to": limit}
              for i, (limit, _) in enumerate(GAP_BANDS)]
GAP_RANGES.append({"from": GAP_BANDS[-1][0]})
GAP_LABELS = [label for _, label in GAP_BANDS] + ["1y+ after"]

# Same idea for score bands: SCORE_BANDS is half-open with the top band closed
# (see band_for's docstring in utils/crash_lift.py), so the last range is left
# open-ended rather than bounded at 1.0, letting a perfect score still land
# somewhere instead of falling off the end of the last bucket.
SCORE_RANGES = [{"from": lower, "to": upper} for lower, upper, _ in SCORE_BANDS[:-1]]
SCORE_RANGES.append({"from": SCORE_BANDS[-1][0]})
SCORE_LABELS = [label for _, _, label in SCORE_BANDS]


def score_gap_matrix(client, pairs_index):
    """Pair counts for every (score band, gap band) cell, via one nested aggregation.

    A document fetch over ~422k pairs to bucket them in Python would work but
    is the wrong tool when Elasticsearch can produce the same counts as two
    numbers per cell; this is the version that scales to the next sweep being
    ten times larger without changing shape.
    """
    response = client.search(
        index=pairs_index,
        size=0,
        aggs={
            "score": {
                "range": {"field": "total_score", "ranges": SCORE_RANGES},
                "aggs": {"gap": {"range": {"field": "gap_days", "ranges": GAP_RANGES}}},
            }
        },
        track_total_hits=False,
    )
    buckets = response["aggregations"]["score"]["buckets"]
    return {
        score_label: [gap_bucket["doc_count"] for gap_bucket in score_bucket["gap"]["buckets"]]
        for score_label, score_bucket in zip(SCORE_LABELS, buckets, strict=True)
    }


def print_matrix(matrix):
    """Score band x gap band table, counts and row percentages, denominators explicit.

    Row percentages rather than a single figure because the brief's question
    is directional — does temporal plausibility improve AS SCORE RISES — which
    only shows up by comparing rows to each other, not by reading any one cell
    alone. Denominators are printed on every row because a percentage over a
    handful of pairs (the top band is the smallest by construction) reads very
    differently from the same percentage over hundreds of thousands.
    """
    print("\nGAP DISTRIBUTION PER SCORE BAND (row percentages; denominator = pairs in that score band)")
    header = "  {:<12} {:>9}" + " {:>18}" * len(GAP_LABELS)
    print(header.format("score band", "n", *GAP_LABELS))
    for label in SCORE_LABELS:
        counts = matrix[label]
        total = sum(counts)
        cells = [
            "n/a" if total == 0 else "{:,} ({:.1%})".format(count, count / total)
            for count in counts
        ]
        print(header.format(label, "{:,}".format(total), *cells))


def score_separation(client, pairs_index):
    """Mean total_score and pair count either side of the shutdown date.

    Answers the second brief question directly: if the score is picking out
    genuine chameleons, pairs that are temporally plausible (post-shutdown)
    should score higher on average than pairs that cannot be chameleons at all
    (pre-shutdown) — the score and the ground truth should agree on which
    pairs are more suspicious.
    """
    response = client.search(
        index=pairs_index,
        size=0,
        aggs={
            "sign": {
                "range": {"field": "gap_days", "ranges": [{"to": 0}, {"from": 0}]},
                "aggs": {"mean_score": {"avg": {"field": "total_score"}}},
            }
        },
        track_total_hits=False,
    )
    pre, post = response["aggregations"]["sign"]["buckets"]
    return {
        "pre": {"count": pre["doc_count"], "mean": pre["mean_score"]["value"]},
        "post": {"count": post["doc_count"], "mean": post["mean_score"]["value"]},
    }


def print_separation(separation):
    """Print the pre/post-shutdown mean-score comparison with both denominators visible."""
    print("\nSCORE SEPARATION (mean total_score, pre- vs post-shutdown)")
    for key, title in (("pre", "pre-shutdown  (gap_days <  0)"), ("post", "post-shutdown (gap_days >= 0)")):
        cell = separation[key]
        mean = cell["mean"]
        print(
            "  {:<30} {:>10,} pairs   mean {}".format(
                title, cell["count"], "n/a" if mean is None else "{:.4f}".format(mean)
            )
        )


def temporal_signal_share(client, pairs_index):
    """Share of pairs per gap band whose `matched_on` includes `temporal`.

    Exists to test a specific bug hypothesis: a signal named `temporal` ought
    to fire only when the dates are chameleon-shaped. If it fires at a
    comparable rate on pairs with negative gap_days — pairs that CANNOT be a
    reincarnation, because the successor predates the shutdown — the signal is
    rewarding something other than what its name promises, and every score it
    contributes to is inflated on exactly the pairs this report is checking.
    """
    response = client.search(
        index=pairs_index,
        size=0,
        aggs={
            "gap": {
                "range": {"field": "gap_days", "ranges": GAP_RANGES},
                "aggs": {"temporal": {"filter": {"term": {"matched_on": "temporal"}}}},
            }
        },
        track_total_hits=False,
    )
    buckets = response["aggregations"]["gap"]["buckets"]
    return [
        {
            "band": label,
            "total": bucket["doc_count"],
            "fired": bucket["temporal"]["doc_count"],
        }
        for label, bucket in zip(GAP_LABELS, buckets, strict=True)
    ]


def print_temporal_share(shares):
    """Print the temporal-signal firing rate per gap band, with n/a for an empty band rather than a fabricated 0.0%."""
    print("\nDID THE `temporal` SIGNAL FIRE? (share of pairs in each gap band with `temporal` in matched_on)")
    print("  {:<18} {:>10} {:>10} {:>9}".format("gap band", "n", "fired", "share"))
    for entry in shares:
        proportion = rate(entry["fired"], entry["total"])
        print(
            "  {:<18} {:>10,} {:>10,} {:>9}".format(
                entry["band"],
                entry["total"],
                entry["fired"],
                "n/a" if proportion is None else "{:.1%}".format(proportion),
            )
        )


def _parse_args():
    """Command-line surface: which pairs index to read.

    Split out of main() so main() reads as the report's outline, matching
    measure_crash_lift.py's convention.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", default="chameleon-candidates-000001")
    return parser.parse_args()


def main():
    """Read the live cluster and print the three-part chameleon-shape report end to end.

    No persistence step (unlike measure_crash_lift.py): this report has no
    proxy outcome to compare across runs, only the sweep's own emitted fields,
    so there is nothing a later run would need to look back at beyond the
    printed numbers themselves.
    """
    args = _parse_args()
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=300
    )

    matrix = score_gap_matrix(client, args.pairs_index)
    print_matrix(matrix)

    separation = score_separation(client, args.pairs_index)
    print_separation(separation)

    shares = temporal_signal_share(client, args.pairs_index)
    print_temporal_share(shares)

    return 0


if __name__ == "__main__":
    sys.exit(main())
