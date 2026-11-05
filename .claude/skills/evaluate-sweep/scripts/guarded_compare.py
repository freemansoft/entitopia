"""Judge a candidate sweep against a baseline under expectations declared before the run.

The evaluate-sweep skill's gate. It exists because the comparison tools already
in the repo each leave a way to get a green result that means nothing:

- scripts/compare_sweeps.py summarizes with DOT-Commercial's hardcoded metric
  list, so it cannot judge any other project, and its own successor
  (scripts/run_metrics.py) documents that a hardcoded list silently starves a
  newly added predicate.
- scripts/run_metrics.py is config-driven but only prints a record; nothing
  compares two of them, so CMS-Providers' sweep had no regression gate at all.
- Neither refuses to compare an index with itself, and the precision plan
  records exactly that trap: resolving the chameleon-candidates alias during
  an experiment compares a run against itself and prints all-zero deltas that
  read as a clean pass.

So this reuses run_metrics' field projection and utils.metric_runner /
utils.sweep_compare unchanged (one definition of every metric, the project's
metrics.json), and adds the preflight checks that turn the quiet failures into
loud ones.

    .venv/bin/python .claude/skills/evaluate-sweep/scripts/guarded_compare.py \\
        --project DOT-Commercial --step chameleon-detection \\
        --baseline-index chameleon-candidates-2026.08.13-000001 \\
        --candidate-index chameleon-candidates-2026.08.17-000001 \\
        --expectations DOT-Commercial/data/precision/expect-weights.json

Exit 0: no guarded metric regressed. Exit 1: at least one did. Exit 2: the
comparison could not be made honestly (missing index, both sides the same
index, an expectation naming a metric the project does not define) — distinct
from 1 so a caller never mistakes "could not judge" for "judged and failed".
"""

import argparse
import json
import sys
from pathlib import Path

# This file sits four levels below the repo root (.claude/skills/<skill>/scripts),
# and imports the repo's own metric code rather than copying it, so both the
# root and scripts/ go on sys.path before those imports can run.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

from elasticsearch import ApiError, Elasticsearch, TransportError
from elasticsearch.helpers import scan
from run_metrics import SCAN_SIZE, load_metrics, source_fields

from utils.metric_runner import summarize
from utils.sweep_compare import EXPECTATIONS, compare

REPO_ROOT = Path(__file__).resolve().parents[4]

EXIT_OK = 0
EXIT_REGRESSED = 1
EXIT_CANNOT_JUDGE = 2

# More than one fingerprint inside a single pair index means two analysis
# configurations wrote into it — the same-day merge hazard the precision plan
# warns about, where a re-run on the same date unions two configs' pairs.
FINGERPRINTS_SHOWN = 5


class CannotJudge(Exception):
    """A precondition failed; the message says which and why it matters."""


def concrete(es: Elasticsearch, name: str) -> str:
    """The real index behind a name, so both sides of a comparison are pinned.

    An alias is accepted for convenience but never compared as an alias: it is
    the thing an experiment moves, so it is resolved once, here, and the
    concrete name is what gets printed and recorded.
    """
    if es.indices.exists_alias(name=name):
        targets = list(es.indices.get_alias(name=name))
        if len(targets) != 1:
            raise CannotJudge("alias {} points at {} indexes; name one explicitly".format(name, len(targets)))
        return targets[0]
    if not es.indices.exists(index=name):
        raise CannotJudge("index {} does not exist".format(name))
    return name


def fingerprints(es: Elasticsearch, index: str) -> list[str]:
    """The analysis fingerprints recorded on an index's pairs.

    Printed rather than enforced: a fingerprint change is often the change
    under test (an analyzer edit), but a reader comparing two sweeps has to
    know whether tokenization moved underneath the metrics. It covers
    tokenization config only — scoring weights and pipelines are outside it,
    so an unchanged fingerprint does not mean an unchanged matcher.
    """
    response = es.search(
        index=index,
        size=0,
        aggs={"fp": {"terms": {"field": "analysis_fingerprint", "size": FINGERPRINTS_SHOWN}}},
    )
    return [bucket["key"] for bucket in response["aggregations"]["fp"]["buckets"]]


def present_fields(es: Elasticsearch, index: str, fields: list[str]) -> set[str]:
    """Which of the metric-read fields exist on at least one document of the index."""
    return {f for f in fields if es.count(index=index, query={"exists": {"field": f}})["count"]}


def drifted_metrics(es: Elasticsearch, baseline: str, candidate: str, metrics: list[dict]) -> dict[str, list[str]]:
    """Metrics that read a field one sweep has and the other lacks, with the fields at fault.

    Found by running this against real sweeps: the 2026.08.13 index predates
    the predecessor.entity_key field, so `predecessors_with_pairs` read 0 on
    it and 23,040 on its successor — a "+23,040" that was pure schema drift,
    passing because the metric happened to be informational. A guarded metric
    caught the same way would pass or fail on a field rename, not on the
    change under test.
    """
    fields = source_fields(metrics)
    on_baseline = present_fields(es, baseline, fields)
    on_candidate = present_fields(es, candidate, fields)
    lopsided = on_baseline ^ on_candidate
    affected = {}
    for metric in metrics:
        hit = sorted(set(source_fields([metric])) & lopsided)
        if hit:
            affected[metric["name"]] = hit
    return affected


# Predicates whose argument is a list of signal types matched against the
# pair's `matched_on`. A signal rename leaves the field in place but empties
# every one of these on the older side, which no field-existence check sees.
VOCABULARY_PREDICATES = ("has_signal_type", "matched_on_equals", "matched_identity_equals")
VOCABULARY_FIELD = "matched_on"
VOCABULARY_SIZE = 100


def named_signal_types(predicate) -> set[str]:
    """Every signal type a metric's filter names, however deeply nested."""
    found: set[str] = set()
    for name, value in (predicate or {}).items():
        if name in VOCABULARY_PREDICATES:
            found.update(value)
        elif name in ("all", "any"):
            for clause in value:
                found |= named_signal_types(clause)
        elif name == "not":
            found |= named_signal_types(value)
    return found


def signal_vocabulary(es: Elasticsearch, index: str) -> set[str]:
    """The signal types that actually occur in an index's `matched_on`."""
    response = es.search(index=index, size=0, aggs={"v": {"terms": {"field": VOCABULARY_FIELD, "size": VOCABULARY_SIZE}}})
    return {bucket["key"] for bucket in response["aggregations"]["v"]["buckets"]}


def renamed_metrics(es: Elasticsearch, baseline: str, candidate: str, metrics: list[dict]) -> dict[str, list[str]]:
    """Metrics naming a signal type that occurs on one sweep and never on the other.

    Also found against real sweeps: between 2026.08.13 and 2026.08.17 the
    VIN signal was renamed vin-overlap -> shared-token, so `vin_only_identity`
    read 0 on the baseline and 208 on the candidate, and its must_not_fall
    guard printed "ok" over a rename. compare_sweeps.py reports the same.
    """
    lopsided = signal_vocabulary(es, baseline) ^ signal_vocabulary(es, candidate)
    affected = {}
    for metric in metrics:
        hit = sorted(named_signal_types(metric.get("filter")) & lopsided)
        if hit:
            affected[metric["name"]] = hit
    return affected


def refuse_drift(es: Elasticsearch, sides: tuple[str, str], metrics: list[dict], expectations: dict, accepted: set[str]) -> None:
    """Print every drifted metric, and refuse the comparison if a guarded one drifted.

    Informational metrics are only reported, since nothing passes or fails on
    them. A guarded one is refused unless the operator named it in
    --accept-drift, because its verdict would describe the rename, not the
    change under test.
    """
    drift = drifted_metrics(es, *sides, metrics)
    for name, fields in drift.items():
        print("SCHEMA DRIFT: {} reads {}, present on only one side".format(name, ", ".join(fields)))
    for name, types in renamed_metrics(es, *sides, metrics).items():
        print("SCHEMA DRIFT: {} names signal type {}, which occurs on only one side".format(name, ", ".join(types)))
        drift.setdefault(name, []).extend(types)
    for name in sorted(accepted & set(drift)):
        print("ACCEPTED DRIFT: {} is compared anyway, at the operator's explicit request".format(name))
    guarded = sorted(n for n in drift if n not in accepted and expectations.get(n, "informational") != "informational")
    if guarded:
        raise CannotJudge("guarded metrics {} cannot be compared across a field or signal rename; re-sweep the "
                          "baseline with the current code, or pass --accept-drift after ruling each one out".format(
                              ", ".join(guarded)))


def record_for(es: Elasticsearch, index: str, metrics: list[dict]) -> dict:
    """The project's metric record for one pair index, computed exactly as run_metrics.py does."""
    rows = scan(es, index=index, query={"query": {"match_all": {}}}, _source=source_fields(metrics), size=SCAN_SIZE)
    return summarize(metrics, (hit["_source"] for hit in rows))


def checked_expectations(path: Path, defined: set[str]) -> dict:
    """Expectations, refused if any names a metric or an expectation that does not exist.

    utils.sweep_compare raises on an unknown expectation but would raise a bare
    KeyError on an unknown metric; this names both up front, because a typo in
    an expectation file is exactly the kind of mistake that otherwise surfaces
    as a confusing crash after a long scan.
    """
    expectations = json.loads(path.read_text(encoding="utf-8"))
    unknown_metrics = sorted(set(expectations) - defined)
    unknown_kinds = sorted({v for v in expectations.values() if v not in EXPECTATIONS})
    if unknown_metrics:
        raise CannotJudge("{} names metrics this project does not define: {}".format(path, ", ".join(unknown_metrics)))
    if unknown_kinds:
        raise CannotJudge("{} uses unknown expectations {}; known are {}".format(
            path, ", ".join(unknown_kinds), ", ".join(EXPECTATIONS)))
    return expectations


def baseline_record(es: Elasticsearch, args: argparse.Namespace, config: dict, metrics: list[dict]) -> tuple[str, dict]:
    """The baseline side, from an index, an explicit record file, or the project's declared baseline."""
    if args.baseline_index:
        index = concrete(es, args.baseline_index)
        return index, record_for(es, index, metrics)
    path = Path(args.baseline_record) if args.baseline_record else None
    if path is None and config.get("baseline"):
        path = REPO_ROOT / args.project / config["baseline"]
    if path is None or not path.exists():
        raise CannotJudge("no baseline: pass --baseline-index or --baseline-record (declared baseline {} not found)".format(
            config.get("baseline")))
    return str(path), json.loads(path.read_text(encoding="utf-8"))


def print_table(deltas) -> None:
    print("\n{:<26} {:>12} {:>12} {:>12} {:>9}  {}".format("metric", "baseline", "candidate", "delta", "pct", "expectation"))
    for d in deltas:
        pct = "-" if d.pct is None else "{:+.1%}".format(d.pct)
        print("{:<26} {:>12} {:>12} {:>12} {:>9}  {} [{}]".format(
            d.name, round(d.baseline, 4), round(d.candidate, 4), round(d.delta, 4), pct, d.expectation,
            "ok" if d.ok else "REGRESSED"))


def run(args: argparse.Namespace) -> int:
    es = Elasticsearch(args.host)
    config = load_metrics(str(REPO_ROOT / args.project), args.step)
    metrics = config["metrics"]
    expectations = checked_expectations(Path(args.expectations), {m["name"] for m in metrics})

    candidate_index = concrete(es, args.candidate_index)
    baseline_source, baseline = baseline_record(es, args, config, metrics)
    if baseline_source == candidate_index:
        raise CannotJudge("baseline and candidate are both {}; every delta would be zero and read as a pass".format(
            candidate_index))

    print("baseline   {}".format(baseline_source))
    if args.baseline_index:
        print("           fingerprint {}".format(", ".join(fingerprints(es, baseline_source)) or "none"))
    print("candidate  {}".format(candidate_index))
    candidate_prints = fingerprints(es, candidate_index)
    print("           fingerprint {}".format(", ".join(candidate_prints) or "none"))
    if len(candidate_prints) > 1:
        print("WARNING: the candidate holds pairs from more than one analysis configuration (same-day merge?)")

    if args.baseline_index:
        refuse_drift(es, (baseline_source, candidate_index), metrics, expectations, set(args.accept_drift or []))
    else:
        print("note: a baseline record cannot be checked for schema drift; prefer --baseline-index when it still exists")

    candidate = record_for(es, candidate_index, metrics)
    missing = sorted(name for name in expectations if name not in baseline)
    if missing:
        raise CannotJudge("baseline record lacks {}; it predates those metrics, so re-take it from an index".format(
            ", ".join(missing)))

    if not args.baseline_index and all(baseline.get(name) == value for name, value in candidate.items()):
        # Found by the skill's own eval: CMS-Providers' declared baseline was
        # written 94 seconds after the only sweep on the cluster, so comparing
        # that sweep against it is the index-against-itself trap in a form the
        # name check above cannot see. A genuine no-op change also lands here,
        # and that question belongs to compare_pair_ids.py, which needs indexes.
        raise CannotJudge("the baseline record equals the candidate on every metric, so it was most likely taken "
                          "from this same sweep; compare against an earlier sweep index, or for a no-op change run "
                          "scripts/compare_pair_ids.py against one")

    deltas = compare(baseline, candidate, expectations)
    print_table(deltas)
    unguarded = sorted({m["name"] for m in metrics} - set(expectations))
    if unguarded:
        print("\nnot guarded by this expectations file: {}".format(", ".join(unguarded)))
    if args.write_record:
        Path(args.write_record).write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("\nwrote candidate record to {}".format(args.write_record))

    regressed = [d.name for d in deltas if not d.ok]
    if regressed:
        print("\nREGRESSED: {}".format(", ".join(regressed)))
        return EXIT_REGRESSED
    print("\nno guarded metric regressed")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--candidate-index", required=True)
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument("--baseline-index")
    baseline.add_argument("--baseline-record", help="a record written earlier by --write-record or run_metrics.py")
    parser.add_argument("--expectations", required=True, help="JSON mapping metric name to expectation, written before the sweep")
    parser.add_argument("--write-record", help="also save the candidate's record, for use as the next baseline")
    parser.add_argument(
        "--accept-drift", nargs="+", metavar="METRIC",
        help="compare these drifted metrics anyway; only after deciding the drift cannot move them",
    )
    parser.add_argument("--host", default="http://localhost:9200")
    args = parser.parse_args()
    try:
        return run(args)
    except CannotJudge as reason:
        print("CANNOT JUDGE: {}".format(reason))
        return EXIT_CANNOT_JUDGE
    except (ApiError, TransportError) as error:
        print("CANNOT JUDGE: cluster at {} failed ({}: {})".format(args.host, type(error).__name__, error))
        return EXIT_CANNOT_JUDGE


if __name__ == "__main__":
    sys.exit(main())
