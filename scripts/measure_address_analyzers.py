"""Replay emitted candidate pairs through two street analyzer configurations.

Exists because an analyzer change cannot be reviewed by reading it: the effect
on scoring depends on how real address text distributes, and the spec's central
claim — that contraction improves precision — was established with this and
would otherwise be an assertion. Run before and after a change to the street
analyzers; it is the acceptance gate for that work.

Reads the CURRENT analyzers from the on-disk settings file, so it never drifts
from what ships. The baseline is passed in as a git revision of the same file,
which keeps the comparison honest without pinning a copy of the old config here.

One limitation that must be stated with any result: the sample is drawn from
pairs the BASELINE configuration already emitted, so it measures precision
changes but structurally cannot show recall gains — a pair the old analyzer
never surfaced is not in the index to sample. Only a full re-sweep shows those.
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_address_analyzers.py`, matching the
# invocation README.md documents for scripts/. That puts scripts/ on sys.path
# rather than the repo root, so importing matching.tokens fails without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch

from matching.tokens import containment

# Mirrors AddressSignal's own arithmetic. Kept in sync by hand rather than
# imported because the signal needs an EntityDoc and a ScoringContext, neither
# of which exists here. This one genuinely is a Python constant — it copies
# CROSS_STATE_FUZZY_PENALTY in matching/signals.py — so it only drifts if that
# code changes, and a code change is something a reader of this file's git
# blame would surface. Contrast the address signal's fuzzy_scale, which is
# NOT copied here: that value lives in entity-match.json and can be retuned
# without touching any Python, so it is a --fuzzy-scale flag instead, with a
# default matching the config's current value.
CROSS_STATE_FUZZY_PENALTY = 0.5
SETTINGS_PATH = "DOT-Commercial/configuration/carriers/index-settings.json"

# Two floats computed from independently-run analyzer calls are "the same score"
# only up to floating-point noise, not bit-for-bit equality; this is that
# tolerance, not a meaningful score threshold.
SCORE_EQUALITY_EPSILON = 1e-9


def analyzer_settings(revision=None):
    """Settings dict from the working tree, or from a git revision for the baseline."""
    if revision is None:
        with open(SETTINGS_PATH) as handle:
            return json.load(handle)["settings"]
    blob = subprocess.run(
        ["git", "show", "{}:{}".format(revision, SETTINGS_PATH)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(blob)["settings"]


def build_index(client, index, settings):
    client.options(ignore_status=404).indices.delete(index=index)
    client.indices.create(index=index, settings=settings)


def analyze_all(client, index, analyzer, texts):
    """Token sets for every text, keyed by text. Threaded because this is thousands of calls."""

    def one(text):
        response = client.indices.analyze(index=index, analyzer=analyzer, text=text)
        return frozenset(token["token"] for token in response["tokens"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(texts, pool.map(one, texts), strict=True))


def score(exact_a, exact_b, fuzzy_a, fuzzy_b, same_state, fuzzy_scale):
    if exact_a and exact_a == exact_b:
        return 1.0
    result = containment(fuzzy_a, fuzzy_b) * fuzzy_scale
    if not same_state:
        result *= CROSS_STATE_FUZZY_PENALTY
    return result


def fetch_pairs(client, index, size, seed):
    response = client.search(
        index=index,
        size=size,
        source=[
            "predecessor.phy_street",
            "successor.phy_street",
            "predecessor.phy_state",
            "successor.phy_state",
            "total_score",
        ],
        query={
            "function_score": {
                "query": {
                    "bool": {
                        "must": [
                            {"exists": {"field": "predecessor.phy_street"}},
                            {"exists": {"field": "successor.phy_street"}},
                        ]
                    }
                },
                "random_score": {"seed": seed, "field": "_seq_no"},
            }
        },
        track_total_hits=False,
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", required=True, help="A chameleon-candidates-* index")
    parser.add_argument("--baseline", required=True, help="git revision holding the OLD settings")
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--floor", type=float, default=0.35, help="scoring.min_total_score")
    parser.add_argument("--weight", type=float, default=0.20, help="address signal weight")
    parser.add_argument("--total-weight", type=float, default=0.94, help="sum of all signal weights")
    parser.add_argument(
        "--fuzzy-scale",
        type=float,
        default=0.7,
        help="address signal fuzzy_scale from entity-match.json",
    )
    args = parser.parse_args()

    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=120
    )
    pairs = fetch_pairs(client, args.pairs_index, args.size, args.seed)
    if not pairs:
        print("No pairs with both streets populated; nothing to measure.", file=sys.stderr)
        return 1
    streets = sorted({p["predecessor"]["phy_street"] for p in pairs}
                     | {p["successor"]["phy_street"] for p in pairs})
    print("pairs {}, unique streets {}".format(len(pairs), len(streets)), file=sys.stderr)

    cache = {}
    for label, revision in (("old", args.baseline), ("new", None)):
        index = "test-address-measure-{}".format(label)
        build_index(client, index, analyzer_settings(revision))
        for analyzer in ("street_clean", "street_tokens"):
            cache[(label, analyzer)] = analyze_all(client, index, analyzer, streets)
        client.options(ignore_status=404).indices.delete(index=index)

    rows = []
    for pair in pairs:
        a = pair["predecessor"]["phy_street"]
        b = pair["successor"]["phy_street"]
        state_a = pair["predecessor"].get("phy_state")
        same_state = bool(state_a) and state_a == pair["successor"].get("phy_state")
        scores = {
            label: score(
                cache[(label, "street_clean")][a],
                cache[(label, "street_clean")][b],
                cache[(label, "street_tokens")][a],
                cache[(label, "street_tokens")][b],
                same_state,
                args.fuzzy_scale,
            )
            for label in ("old", "new")
        }
        rows.append((scores["new"] - scores["old"], pair.get("total_score")))

    changed = [r for r in rows if abs(r[0]) > SCORE_EQUALITY_EPSILON]
    up = [r for r in changed if r[0] > 0]
    down = [r for r in changed if r[0] < 0]
    print("\nchanged {}/{} ({:.1f}%)".format(len(changed), len(rows), 100 * len(changed) / len(rows)))
    if up:
        print("  up   {} (mean {:+.4f})".format(len(up), sum(r[0] for r in up) / len(up)))
    if down:
        print("  down {} (mean {:+.4f})".format(len(down), sum(r[0] for r in down) / len(down)))

    crossed_up = crossed_down = 0
    for delta, total in rows:
        if total is None:
            continue
        shifted = total + delta * args.weight / args.total_weight
        if total < args.floor <= shifted:
            crossed_up += 1
        elif shifted < args.floor <= total:
            crossed_down += 1
    print("approx pairs crossing the {} floor: +{} / -{}".format(args.floor, crossed_up, crossed_down))
    print(
        "\nNOTE: sampled from pairs the BASELINE already emitted, so this shows "
        "precision change only. Recall gains require a full re-sweep."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
