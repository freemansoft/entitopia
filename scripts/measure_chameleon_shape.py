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

The gap bands (`utils.crash_lift.GAP_BANDS`) are anchored to
`matching/signals.py`'s own `BACKWARD_WINDOW_DAYS = 180`, not an arbitrary
round number: that is the pre-shutdown window the `temporal` signal itself
scores as plausible pre-positioning, so a pair outside it is implausible by
the model's own design, and this report judges the model against its own
claim rather than a boundary invented independently.

Baseline measured 2026-08-08: among 1,729 pairs scoring >= 0.70, the gap
distribution is 728 / 161 / 435 / 405 across the four bands below; mean
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

from utils.crash_lift import GAP_BANDS, SCORE_BANDS
from utils.file_utils import load_from_file

# Default location of the live scoring config, so load_signal_weights() reads
# the weights actually driving the sweep instead of a copy pasted into this
# script that would go stale silently the next time entity-match.json is
# retuned.
DEFAULT_ENTITY_MATCH_CONFIG = (
    Path(__file__).resolve().parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)

# The three configured entries that all score name similarity: two phonetic
# encoders (name-phonetic at two different subfields) plus the cleaned token
# form (name-token). Summed together because DOT-Commercial/README.md's open
# item about name similarity being "effectively triple-weighted" is a claim
# about their combined weight, not any one of them individually.
NAME_SIGNAL_TYPES = ("name-phonetic", "name-token")

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


def load_signal_weights(config_path):
    """Configured weight per signal type, summed across entries sharing a type.

    Reads `entity-match.json` directly rather than hardcoding its numbers, so
    this report stays correct the next time the config is retuned instead of
    quietly comparing against stale weights. Summed by type because two
    entries share `name-phonetic` (the plain and Beider-Morse subfields) —
    `load_from_file`'s SimpleNamespace tree keeps each config entry separate,
    and this is the one place that collapses them for the "name signals
    combined" comparison below.
    """
    config = load_from_file(str(config_path))
    weights = {}
    for signal in config.signals:
        weights[signal.type] = weights.get(signal.type, 0.0) + float(signal.weight)
    return weights


def temporal_headroom(weights, separation):
    """How much the `temporal` signal COULD move total_score, against how much the data actually separates on it.

    Computed once, as one structure, so the printed ceiling, the printed
    observed gap and the printed ratio between them can never disagree with
    each other — the three numbers this function returns are read together in
    print_temporal_headroom, not recomputed at each print site.

    max_contribution divides by the FULL configured weight total (0.94), the
    best case for temporal's influence: `matching/scorer.py` renormalizes
    total_score by only the evaluable signals' weight, so on any pair missing
    other evidence temporal's share of that smaller denominator is larger —
    this ceiling is deliberately the loosest one that still uses only the
    published config, not a per-pair maximum that would need fetching
    documents to compute.
    """
    total = sum(weights.values())
    temporal_weight = weights.get("temporal", 0.0)
    name_weight = sum(weights.get(t, 0.0) for t in NAME_SIGNAL_TYPES)
    pre_mean = separation["pre"]["mean"]
    post_mean = separation["post"]["mean"]
    observed_gap = None if pre_mean is None or post_mean is None else post_mean - pre_mean
    return {
        "total_weight": total,
        "temporal_weight": temporal_weight,
        "name_weight": name_weight,
        "observed_gap": observed_gap,
        "max_contribution": None if total <= 0 else temporal_weight / total,
    }


def print_temporal_headroom(headroom):
    """Print the weight-vs-separation comparison: can `temporal` matter at all, given how little the data separates on it.

    The finding this table exists to support is the weight comparison, not
    the ceiling/gap ratio: name signals combined carry roughly 9x temporal's
    configured weight (0.45 vs 0.05 of 0.94), so a pair's rank is set
    overwhelmingly by name similarity. The ceiling/gap ratio printed below
    does NOT support "temporal is too weak to matter" — measured, temporal's
    best-case ceiling (0.0532) is 5.6x the observed pre/post-shutdown gap
    (0.0095), i.e. temporal has more than enough theoretical headroom to
    produce that gap on its own. What the data shows is that it doesn't:
    weight, not ceiling, is what is starving it in practice, which is exactly
    the 9x-weight finding above. This corroborates DOT-Commercial/README.md's
    open item that name similarity is "effectively triple-weighted."
    """
    total = headroom["total_weight"]
    temporal = headroom["temporal_weight"]
    name = headroom["name_weight"]
    gap = headroom["observed_gap"]
    ceiling = headroom["max_contribution"]

    print("\nCAN THE `temporal` SIGNAL EVEN MOVE THE SCORE? (weights read live from entity-match.json)")
    print("  temporal weight               : {:.2f} of {:.2f} configured total".format(temporal, total))
    print(
        "  max possible contribution     : {} (temporal weight x its max score of 1.0, over the full configured total)".format(
            "n/a" if ceiling is None else "{:.4f}".format(ceiling)
        )
    )
    print(
        "  observed pre/post mean gap    : {}".format(
            "n/a" if gap is None else "{:.4f}".format(gap)
        )
    )
    print(
        "  name signals combined weight  : {:.2f} of {:.2f} configured total ({})".format(
            name, total, "n/a" if not total else "{:.1%}".format(name / total)
        )
    )
    if ceiling and temporal and gap:
        if gap > 0:
            # The normal case: post-shutdown pairs score higher on average, so
            # "ceiling is Nx the gap" reads as a magnitude and means what it says.
            ratio_clause = "temporal's ceiling is {:.1f}x the observed pre/post gap".format(
                ceiling / gap
            )
        else:
            # gap < 0 means post-shutdown pairs scored LOWER on average than
            # pre-shutdown ones — the opposite of what a chameleon-detecting
            # score should show. ceiling / gap would still divide cleanly (no
            # zero-division risk), but printing it as "-5.6x the gap" hands a
            # reader a signed ratio in a sentence built to be read as a
            # magnitude, and the minus sign is easy to skim past. State the
            # direction in words instead of leaning on the sign to carry it.
            ratio_clause = (
                "the observed pre/post gap runs the WRONG way (post-shutdown scored "
                "{:.4f} lower than pre-shutdown, not higher)".format(-gap)
            )
        print(
            "  {}; name signals carry {:.1f}x more configured weight than temporal — "
            "ranking is dominated by name similarity, not shutdown timing.".format(
                ratio_clause, name / temporal
            )
        )


def _parse_args():
    """Command-line surface: which pairs index and which scoring config to read.

    Split out of main() so main() reads as the report's outline, matching
    measure_crash_lift.py's convention. --entity-match-config defaults to the
    live DOT-Commercial config rather than a value baked into the report body,
    so pointing this script at a different project's config is a flag, not an
    edit.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", default="chameleon-candidates-000001")
    parser.add_argument("--entity-match-config", default=str(DEFAULT_ENTITY_MATCH_CONFIG))
    return parser.parse_args()


def main():
    """Read the live cluster and print the three-part chameleon-shape report end to end.

    No persistence step (unlike measure_crash_lift.py): this report has no
    proxy outcome to compare across runs, only the sweep's own emitted fields
    and its own scoring config, so there is nothing a later run would need to
    look back at beyond the printed numbers themselves.
    """
    args = _parse_args()
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=300
    )

    matrix = score_gap_matrix(client, args.pairs_index)
    print_matrix(matrix)

    separation = score_separation(client, args.pairs_index)
    print_separation(separation)

    weights = load_signal_weights(args.entity_match_config)
    print_temporal_headroom(temporal_headroom(weights, separation))

    return 0


if __name__ == "__main__":
    sys.exit(main())
