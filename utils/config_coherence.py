"""Cross-file and cross-block checks a schema structurally cannot make.

A schema validates one document against one shape. It cannot say "this seed
names a signal nobody configured" or "this gap window has no dates to read",
because those facts live in two places at once — and every one of them produces
a sweep that runs to completion and reports success while doing less than the
operator configured.

That asymmetry is the whole justification for this module. Each rule below
corresponds to a configuration that is individually well-formed, jointly
incoherent, and silent about it.

One small function per rule rather than one long one: each rule's docstring is
where the reasoning gets recorded, and a combined function has nowhere to put
eight of them. Every rule returns zero or more messages and never raises, so
`check` can report everything wrong at once — fixing config is iterative, and a
first-error validator turns a five-mistake config into five runs.
"""

from types import SimpleNamespace

from matching.population import PopulationSelector


def _namespaced(value):
    """Turn plain dicts into the SimpleNamespace shape the matcher consumes.

    Needed because these checks run over raw JSON (which is what schema
    validation wants) while `PopulationSelector` is built for config as the
    rest of the codebase loads it. Reusing that class is deliberate — see
    `_selector_resolves`.
    """
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _namespaced(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_namespaced(v) for v in value]
    return value


def _configured_types(raw) -> set[str]:
    return {s.get("type") for s in raw.get("signals", []) or []}


def _temporal_needs_lifecycle(raw, source) -> list[str]:
    """A temporal signal with no lifecycle block has no dates to score.

    The signal itself raises at build time, which is correct but surfaces as a
    stack trace part way into a run. Reporting it here names the file and the
    missing block instead.
    """
    if "temporal" not in _configured_types(raw) or raw.get("lifecycle"):
        return []
    return [
        "{}: a `temporal` signal is configured but there is no `lifecycle` block; "
        "it names the shutdown_date and registration_date the signal scores "
        "between".format(source)
    ]


def _gap_window_needs_lifecycle(raw, source) -> list[str]:
    """A gap window with no lifecycle block is a gate that silently does nothing.

    Worse than no gate: it reads as a tightening the operator applied, and the
    pairs it was meant to exclude keep being emitted.
    """
    scoring = raw.get("scoring") or {}
    windowed = scoring.get("min_gap_days") is not None or scoring.get("max_gap_days") is not None
    if not windowed or raw.get("lifecycle"):
        return []
    return [
        "{}: scoring sets a gap window (min_gap_days/max_gap_days) but there is "
        "no `lifecycle` block, so the window has no dates to read and the gate "
        "is silently off".format(source)
    ]


def _lifecycle_without_succession(raw, source) -> list[str]:
    """A lifecycle block under all-entities mode expects succession it won't get.

    Not fatal — the matcher ignores it — but it means somebody configured dated
    events for a sweep that emits no gap and asserts no direction, and finding
    that out from an empty gap_days column is an expensive way to learn it.
    """
    population = raw.get("population") or {}
    if population.get("mode") != "all-entities" or not raw.get("lifecycle"):
        return []
    return [
        "{}: `lifecycle` is configured but population.mode is 'all-entities', "
        "which emits no gap_days and makes no succession claim; the block will "
        "have no effect".format(source)
    ]


def _seeds_name_configured_signals(raw, source) -> list[str]:
    """A seed naming no configured signal caps recall at zero for that evidence.

    Retrieval asks each configured signal whether it wants to seed, matching on
    signal type. A seed type nothing matches contributes no clause, so pairs
    resting on that evidence are never retrieved and therefore never scored at
    any threshold — and no count anywhere goes down in a way anyone would
    notice.
    """
    configured = _configured_types(raw)
    seeds = ((raw.get("candidates") or {}).get("seed_signals")) or []
    return [
        "{}: candidates.seed_signals names {!r}, which no configured signal "
        "has as its type; it will retrieve nothing".format(source, seed)
        for seed in seeds
        if seed not in configured
    ]


def _conclusive_signals_can_seed(raw, source) -> list[str]:
    """A conclusive signal that cannot seed can never carry a pair on its own.

    Marking a signal conclusive says a pair resting on it alone should be
    emitted. If it does not also seed, such a pair is never retrieved, so the
    mark is unreachable — the measured case being an entity that changed its
    name, address and phone while keeping its equipment.
    """
    seeds = set(((raw.get("candidates") or {}).get("seed_signals")) or [])
    return [
        "{}: signal type {!r} is marked conclusive but is not in "
        "candidates.seed_signals, so a pair resting on it alone can never be "
        "retrieved and the conclusive mark is unreachable".format(
            source, signal.get("type")
        )
        for signal in raw.get("signals", []) or []
        if signal.get("conclusive") and signal.get("type") not in seeds
    ]


def _selector_resolves(raw, source) -> list[str]:
    """The configured selector must build, following `all`/`any` references.

    Delegates to PopulationSelector rather than reimplementing name resolution
    and cycle detection. Two implementations of "is this selector graph
    acyclic" would be two places to fix and one place to forget, and the point
    of this check is that it agrees with what the sweep will actually do.
    """
    population = raw.get("population")
    if not population:
        return []
    try:
        PopulationSelector(
            es=None, source_index="unused", config=_namespaced(population)
        ).build_query()
    except ValueError as e:
        return ["{}: population: {}".format(source, e)]
    return []


_RULES = (
    _temporal_needs_lifecycle,
    _gap_window_needs_lifecycle,
    _lifecycle_without_succession,
    _seeds_name_configured_signals,
    _conclusive_signals_can_seed,
    _selector_resolves,
)


def check(entity_match: dict, source: str) -> list[str]:
    """Run every coherence rule. Returns messages, empty when coherent.

    Takes the raw parsed JSON rather than loaded config, so it can run in the
    same pass as schema validation and before anything touches a cluster.
    """
    messages = []
    for rule in _RULES:
        messages.extend(rule(entity_match, source))
    return messages
