"""Turn a project's declared metrics plus a stream of pairs into a metric record.

Produces exactly the shape `utils.sweep_compare.compare()` already consumes —
metric name to number — so the expectation files a project has already committed
keep working against a config-driven runner without being rewritten.

Three metric kinds, not two. The third was not in the original design and was
found by reading the hand-written implementation this replaces: a ratio of two
other metrics. It matters more than its lateness suggests — three of the four
expectation files shipped in DOT-Commercial mark the one ratio metric
`must_not_fall`, more than any other single metric, so a runner without ratios
could not express the project's most-guarded number.

Everything is computed in ONE pass over the pairs. A pass per metric would turn
a twelve-metric config into twelve scans of a several-hundred-thousand-pair
index, and the pairs arrive as a stream precisely so they never all sit in
memory at once.
"""

from utils.metric_predicates import evaluate


def _read(document, path):
    """Read a dotted path out of a pair document, or None."""
    current = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _counting_metrics(metrics):
    """Metrics computed by looking at pairs, in declaration order."""
    return [m for m in metrics if "ratio" not in m]


def _ratio_metrics(metrics):
    """Metrics computed from other metrics, after the pass over pairs."""
    return [m for m in metrics if "ratio" in m]


def summarize(metrics: list[dict], pairs) -> dict:
    """Reduce a pair population to the record its project declared.

    `pairs` is any iterable of pair _source dicts, consumed once, so a caller
    can hand over a scan response without holding the population.

    Counts and distincts are computed during the pass; ratios afterwards from
    the resulting record. That ordering is what lets a ratio name any metric
    regardless of declaration order — the alternative, resolving references in
    declaration order, would make a config file's meaning depend on the
    sequence its entries happen to be written in.

    Every configured metric appears in the result even when it counted nothing.
    Zero is a measurement; absence is not, and `compare()` raises on a metric
    the candidate record lacks.
    """
    counting = _counting_metrics(metrics)
    counts = {metric["name"]: 0 for metric in counting}
    distincts = {
        metric["name"]: set() for metric in counting if "distinct" in metric
    }

    for pair in pairs:
        for metric in counting:
            if not evaluate(metric.get("filter") or {}, pair):
                continue
            path = metric.get("distinct")
            if path is None:
                counts[metric["name"]] += 1
                continue
            value = _read(pair, path)
            # A missing value must not become a None bucket, which would
            # inflate the count by one for every malformed pair.
            if value is not None:
                distincts[metric["name"]].add(value)

    record = {
        metric["name"]: (
            len(distincts[metric["name"]])
            if "distinct" in metric
            else counts[metric["name"]]
        )
        for metric in counting
    }

    for metric in _ratio_metrics(metrics):
        ratio = metric["ratio"]
        # KeyError rather than a default: a ratio naming a metric that does not
        # exist is a config error, and silently reporting 0.0 would hide it
        # behind a plausible number. The coherence tier catches this before a
        # run; this is the backstop for a direct caller.
        numerator = record[ratio["numerator"]]
        denominator = record[ratio["denominator"]]
        # 0.0 rather than NaN on an empty denominator: NaN serializes into a
        # baseline file as the bare token NaN, which is not valid JSON, so it
        # would poison every later comparison instead of failing where it arose.
        record[metric["name"]] = (numerator / denominator) if denominator else 0.0

    return record
