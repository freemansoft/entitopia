"""Measure a sweep's recall and precision against the links a dataset declares.

    .venv/bin/python scripts/measure_declared_links.py \\
        --project FDIC-Banks --step bank-absorption \\
        --index <pair index> --half report

Recomputes per link rather than reading the pair index's
`_meta.declared_accounting`, because that total spans every predecessor and
cannot be cut into the tune and report halves. Thresholds are tuned on one half
and quoted on the other so the quoted recall is not fitted to the links it is
quoted on.

The candidate search is re-run with the same finder, selector and scoring
context the sweep builds, since "retrieved but not emitted" versus "never
retrieved" cannot be told from the pair index alone. Output is JSON on stdout
and holds no entity key or name: a score is an analysis, not a finding.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan

from matching.candidates import to_entity_doc
from matching.declared import DeclaredLinks, split_half
from matching.signals import _latest_date
from phase_providers.phase_entity_match import build_sweep_components
from utils import file_utils, id_utils
from utils.declared_measure import LinkOutcome, PairRow, gap_histogram, summarize

SCAN_SIZE = 2000
CONFIGURATION_DIR = "configuration"


def read_pairs(es, index):
    """Every pair's score keyed by _id, plus per-predecessor rows for precision.

    One scan serves both uses: link lookup by the sweep's own id scheme, and
    precision over every emitted pair. Only the fields needed are projected,
    since the per-signal array is most of a pair's bytes.
    """
    scores = {}
    rows = []
    for hit in scan(
        es,
        index=index,
        query={"query": {"match_all": {}}},
        _source=["total_score", "declared", "predecessor.entity_key"],
        size=SCAN_SIZE,
    ):
        source = hit["_source"]
        score = float(source["total_score"])
        scores[hit["_id"]] = score
        pred_key = str((source.get("predecessor") or {}).get("entity_key"))
        rows.append(PairRow(pred_key, source.get("declared", "none"), score))
    return scores, rows


def fetch_target(es, source_index, key_field, key):
    """The target's hit, or None. A term lookup on the configured key column.

    Not a get by _id: the entity key is whichever column the project names,
    which need not be the document id.
    """
    response = es.search(
        index=source_index,
        size=1,
        query={"term": {key_field: key}},
        track_total_hits=False,
    )
    hits = response["hits"]["hits"]
    return hits[0] if hits else None


def classify_target(window, pred_doc, target_doc):
    """None when the window admits the target, else the exclusion that explains it.

    target_undated is split from target_outside_window because they have
    different remedies: one is a data gap, the other a window choice.
    """
    if window is None or window.admits(pred_doc, target_doc):
        return None
    registered = _latest_date(target_doc.value(window.lifecycle.registration_date))
    if registered is None and window.undated_candidates == "exclude":
        return "target_undated"
    return "target_outside_window"


def gap_between(window, pred_doc, target_doc):
    """Whole days from shutdown to registration, or None if either is missing."""
    if window is None:
        return None
    shutdown = window.shutdown(pred_doc.source)
    registered = _latest_date(target_doc.value(window.lifecycle.registration_date))
    if shutdown is None or registered is None:
        return None
    return (registered - shutdown).days


def predecessor_links(env, pred_hit, pred_doc, targets):
    """One predecessor's truth links, each classified.

    Split from measure() so the per-link decision order (skipped predecessor,
    missing target, window, retrieval, emission) reads in one place. The
    candidate search runs only when a target survives the window, because a
    search whose answer cannot change a count is the expensive part of a run.

    Every exclusion, including predecessor_undated and predecessor_not_searched,
    counts LINKS, one per truth target, so the denominator and the exclusions
    share one unit and sum to the total links.

    A failed candidate search aborts the measurement rather than being counted.
    The sweep survives a lookup error by skipping the predecessor, but here a
    skipped predecessor would silently shrink the denominator or inflate
    misses, and a quietly wrong recall is worse than no number. The error is
    re-raised naming the predecessor count reached so the run is not mistaken
    for a complete one.
    """
    window = env.window
    pred_key = pred_doc.entity_key
    if window is not None and window.skips_predecessor(window.shutdown(pred_hit["_source"])):
        return [LinkOutcome(pred_key, t, "predecessor_undated") for t in targets]

    links = []
    in_window = []
    for target in targets:
        hit = fetch_target(env.es, env.source_index, env.key_field, target)
        if hit is None:
            links.append(LinkOutcome(pred_key, target, "target_missing_from_index"))
            continue
        target_doc = to_entity_doc(hit, {}, env.key_field)
        gap = gap_between(window, pred_doc, target_doc)
        exclusion = classify_target(window, pred_doc, target_doc)
        if exclusion:
            links.append(LinkOutcome(pred_key, target, exclusion, None, gap))
        else:
            in_window.append((target, gap))
    if not in_window:
        return links

    try:
        searched_doc, candidates, _ = env.finder.find(pred_hit, env.ctx)
    except Exception as e:
        raise RuntimeError(
            "Candidate lookup failed after {} predecessors; the measurement is "
            "incomplete and must not be quoted: {}".format(env.progress["predecessors"], e)
        ) from e
    if searched_doc is None:
        # The sweep skips a predecessor with no seed clauses before counting
        # its truth links, so calling these misses would charge the matcher for
        # a search that never ran.
        links.extend(
            LinkOutcome(pred_key, t, "predecessor_not_searched", None, g)
            for t, g in in_window
        )
        return links
    retrieved = {c.entity_key for c in candidates}
    for target, gap in in_window:
        if target not in retrieved:
            links.append(LinkOutcome(pred_key, target, "not_retrieved", None, gap))
            continue
        pair_id = id_utils.compute_id({"p": pred_key, "s": target}, ["p", "s"])
        score = env.scores.get(pair_id)
        status = "retrieved_not_emitted" if score is None else "emitted"
        links.append(LinkOutcome(pred_key, target, status, score, gap))
    return links


def measure(es, project, step, index, half):
    """Walk the half's predecessors and classify each of their truth links."""
    config = file_utils.load_from_project_file(
        project, CONFIGURATION_DIR, step, "entity-match.json"
    )
    if config is None:
        raise SystemExit("No entity-match.json for {}/{}".format(project, step))
    declared = DeclaredLinks.from_config(config)
    if declared is None:
        raise SystemExit("{}/{} declares no links to measure".format(project, step))

    source_index = config.source_index
    components = build_sweep_components(es, config, logging.getLogger(__name__))
    finder = components.finder
    selector = components.selector
    window = finder.window
    ctx = components.context_builder.build_context(source_index, config.signals, config)

    scores, rows = read_pairs(es, index)
    key_field = finder.entity_key_field
    env = SimpleNamespace(
        es=es, source_index=source_index, key_field=key_field, window=window,
        finder=finder, ctx=ctx, scores=scores, progress={"predecessors": 0},
    )
    links = []
    for pred_hit in selector.iterate():
        env.progress["predecessors"] += 1
        pred_doc = to_entity_doc(pred_hit, {}, key_field)
        pred_key = pred_doc.entity_key
        if split_half(pred_key) != half:
            continue
        targets = sorted(declared.truth_targets(pred_doc, ctx))
        if not targets:
            continue
        links.extend(predecessor_links(env, pred_hit, pred_doc, targets))

    half_rows = [r for r in rows if split_half(r.pred_key) == half]
    result = {"half": half}
    result.update(summarize(links, half_rows, declared.complete_truth))
    return result, links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--index", required=True, help="The sweep's pair index")
    parser.add_argument("--half", required=True, choices=("tune", "report"))
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument(
        "--gaps",
        action="store_true",
        help="Also bin days from shutdown to target registration",
    )
    args = parser.parse_args()

    es = Elasticsearch(args.host)
    result, links = measure(es, args.project, args.step, args.index, args.half)
    if args.gaps:
        result["gap_histogram"] = gap_histogram(links)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
