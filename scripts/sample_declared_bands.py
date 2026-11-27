"""Draw the hand-review sample behind a sweep's per-band findings.

    .venv/bin/python scripts/sample_declared_bands.py \\
        --project GLEIF-Entities --step lei-absorption --index <pair index>
    .venv/bin/python scripts/sample_declared_bands.py \\
        --count GLEIF-Entities/data/review/lei-absorption-sample.jsonl

A dataset whose declared links are incomplete has no precision figure: an
undeclared pair may be a real succession the register never recorded. Each
score interval is instead reported with its count of undeclared pairs, its
share of truth, and how many of a hand-reviewed sample look real. This script
draws that sample, one stratum per interval of
utils.declared_measure.summarize's pairs_in_band plus an undated stratum, and
with --count tallies the verdicts a reviewer has written into it.

The rows carry the real names and addresses of the entities the matcher
paired, which is what a reviewer needs and what the repo must never hold, so
the output is refused anywhere but under a data/ directory. --count prints
counts only.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

from matching.declared import split_half
from phase_providers.phase_entity_match import pair_side_names_for
from utils import file_utils
from utils.declared_measure import (
    DEFAULT_BANDS,
    VERDICTS,
    candidate_undated,
    sample_bands,
    step_is_dated,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_SIZE = 2000
CONFIGURATION_DIR = "configuration"
# The per-signal array is most of a pair's bytes and the reviewer reads the
# two sides, not the score's arithmetic.
_EXCLUDED_SOURCE = ["signals"]


def check_out_path(path, repo_root=REPO_ROOT):
    """Refuse an output path git would offer to commit.

    The rows name entities the matcher flagged. A data/ directory is required,
    and inside the repository git must also ignore the path: `*/data/` only
    matches a data/ one level below the root, so a nested data/ directory
    passes the first test and is still committable. Git's own matcher decides
    rather than a re-implementation of it. A path outside the repository is
    accepted, since no commit can reach it.
    """
    resolved = Path(path).resolve()
    if "data" not in resolved.parent.parts:
        raise SystemExit(
            "--out {} is not under a data/ directory; the sample names real "
            "entities and must stay where git ignores it".format(path)
        )
    root = Path(repo_root).resolve()
    if not resolved.is_relative_to(root):
        return
    check = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", str(resolved.relative_to(root))],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 1:
        raise SystemExit(
            "--out {}: git does not ignore this path, so the sample's real "
            "names could be committed; write it under <project>/data/".format(path)
        )
    if check.returncode != 0:
        # Neither "ignored" nor "not ignored": an unanswered check is not a pass.
        raise SystemExit(
            "--out {}: git check-ignore failed: {}".format(path, check.stderr.strip())
        )


def refuse_overwriting_verdicts(path):
    """Refuse to redraw over a file a reviewer has started filling in.

    The draw is seed-stable only against the same pairs. Pointed at a new
    sweep it draws other pairs, and writing them would replace hand-written
    verdicts with nulls, which nothing could restore.
    """
    path = Path(path)
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        judged = sum(
            1
            for line in handle
            if line.strip() and json.loads(line).get("verdict") is not None
        )
    if judged:
        raise SystemExit(
            "{} already holds {} verdict(s); move it aside or pass another "
            "--out".format(path, judged)
        )


def count_verdicts(path):
    """Verdict counts per band from a reviewed sample, unreviewed rows included.

    A verdict outside VERDICTS is refused rather than skipped: a misspelling
    dropped from every column would shrink the sample a band's figure stands
    on with nothing to show it.
    """
    counts = {}
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            verdict = row.get("verdict")
            if verdict is not None and verdict not in VERDICTS:
                raise SystemExit(
                    "{} line {}: verdict {!r} is not one of {}".format(
                        path, number, verdict, ", ".join(VERDICTS)
                    )
                )
            band = counts.setdefault(
                row["band"], dict.fromkeys((*VERDICTS, "unreviewed"), 0)
            )
            band[verdict or "unreviewed"] += 1
    return counts


def read_pair_sources(es, index, cand_side, dated):
    """Every pair's source, with the _id and undated flag sample_bands reads."""
    pairs = []
    for hit in scan(
        es,
        index=index,
        query={"query": {"match_all": {}}},
        _source_excludes=_EXCLUDED_SOURCE,
        size=SCAN_SIZE,
    ):
        source = dict(hit["_source"])
        source["_id"] = hit["_id"]
        source["candidate_undated"] = candidate_undated(source, cand_side, dated)
        pairs.append(source)
    return pairs


def select_half(pairs, side, half):
    """The pairs whose predecessor key split_half files under `half`; all for None.

    The measurement counts pairs_in_band per half by the same split of the
    predecessor key, and verdicts set against the report half's counts must
    be drawn from the pairs those counts include. A pair with no key is
    refused by split_half rather than hashed as the text "None", which would
    file every such pair under one fixed half.
    """
    if half is None:
        return pairs
    kept = []
    for pair in pairs:
        key = (pair.get(side) or {}).get("entity_key")
        if split_half(None if key is None else str(key)) == half:
            kept.append(pair)
    return kept


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--step")
    parser.add_argument("--index", help="The sweep's pair index")
    parser.add_argument("--per-band", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--declared", default="none", choices=("none", "explains", "truth"))
    parser.add_argument(
        "--half",
        choices=("tune", "report"),
        help="Draw only from this half of predecessors; default both",
    )
    parser.add_argument("--out", help="Default: <project>/data/review/<step>-sample.jsonl")
    parser.add_argument("--count", help="Print verdict counts per band from this file")
    parser.add_argument("--host", default="http://localhost:9200")
    args = parser.parse_args()

    if args.count:
        print(json.dumps(count_verdicts(args.count), indent=2))
        return 0
    if not (args.project and args.step and args.index):
        parser.error("--project, --step and --index are required unless --count is given")

    out = Path(args.out or Path(args.project, "data", "review", args.step + "-sample.jsonl"))
    check_out_path(out)
    refuse_overwriting_verdicts(out)

    config = file_utils.load_from_project_file(
        args.project, CONFIGURATION_DIR, args.step, "entity-match.json"
    )
    if config is None:
        raise SystemExit("No entity-match.json for {}/{}".format(args.project, args.step))
    side, cand_side = pair_side_names_for(getattr(config.population, "mode", "lifecycle"))

    es = Elasticsearch(args.host)
    scanned = read_pair_sources(es, args.index, cand_side, step_is_dated(config))
    pairs = select_half(scanned, side, args.half)
    rows = sample_bands(pairs, DEFAULT_BANDS, args.per_band, args.seed, args.declared)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    drawn = {}
    for row in rows:
        drawn[row["band"]] = drawn.get(row["band"], 0) + 1
    print(
        "scanned {} pairs from {}, {} in half {}".format(
            len(scanned), args.index, len(pairs), args.half or "both"
        )
    )
    print("wrote {} {} pairs to {}: {}".format(len(rows), args.declared, out, drawn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
