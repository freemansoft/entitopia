"""Fixed metrics for judging whether a re-sweep improved or regressed.

Every precision change in DOT-Commercial/README.md's open items is expected to
shrink the pair population; shrinkage alone therefore proves nothing, because
the cheapest way to shrink it is to lose real matches. This module pins the
counts that distinguish the two before any change is made, so a later run is
compared against a number chosen in advance rather than one rationalized after
the fact.

Kept free of Elasticsearch imports on purpose: anything here must be callable
from a test with plain dicts, the same split that keeps utils/crash_lift.py
testable while scripts/measure_crash_lift.py stays integration-shaped.
"""

from dataclasses import dataclass

from matching.scorer import IDENTITY_SIGNAL_TYPES

# The threshold the README's triage set and both validation scripts already
# use. Reused rather than re-chosen: an edge picked after seeing the outcome is
# the standard way this analysis fools its author.
TRIAGE_SCORE = 0.70

# Anchored to matching/signals.py's own BACKWARD_WINDOW_DAYS and the configured
# temporal max_gap_days. A pair outside this window is implausible by the
# scorer's own design, so "coherent" judges the model against its own claim
# rather than a boundary invented here.
COHERENT_MIN_GAP = -180
COHERENT_MAX_GAP = 365

# The signals the triage filter treats as corroboration. Name and address
# similarity are excluded deliberately — a pair resting on those alone is the
# false-positive shape the triage set exists to exclude.
CORROBORATING = frozenset({"vin-overlap", "exact-identifier"})

# A byte-identical legal name reappearing within a week of shutdown at a near
# perfect score is the README's sanity anchor. It is counted rather than named
# because naming a flagged carrier is forbidden; the count survives
# anonymization and still fails loudly if the shape stops being surfaced.
CANARY_SCORE = 0.99
CANARY_MAX_GAP = 7

METRICS = (
    "pairs",
    "pairs_ge_070",
    "coherent_ge_070",
    "coherent_share_ge_070",
    "vin_only",
    "vin_only_identity",
    "triage_unbounded",
    "triage_bounded",
    "identical_name_triage",
    "canary",
    "predecessors_with_pairs",
)

EXPECTATIONS = ("must_not_fall", "must_not_rise", "informational", "within_10pct")

TOLERANCE = 0.10


@dataclass
class MetricDelta:
    """One metric's before/after, and whether that movement was permitted.

    Carries the expectation alongside the numbers so a printed table shows why
    a fall was accepted in one run and rejected in another — the two differ by
    which change is under test, not by the metric.
    """

    name: str
    baseline: float
    candidate: float
    delta: float
    pct: float | None
    expectation: str
    ok: bool


def _is_coherent(gap_days):
    """Whether a pair's timing is inside the window the scorer itself models.

    None means the pair carries an unparseable date on one side and cannot be
    judged temporally at all. That is not the same as "outside the window", but
    it is emphatically not coherent, and counting it as such would inflate the
    one metric this whole plan is trying to move.
    """
    if gap_days is None:
        return False
    return COHERENT_MIN_GAP <= gap_days <= COHERENT_MAX_GAP


def summarize(pairs) -> dict:
    """Reduce a pair population to the fixed metric record.

    Takes any iterable of pair _source dicts so the caller can stream a scan
    response through it without holding 400k pairs in memory.
    """
    counts = dict.fromkeys(METRICS, 0)
    predecessors = set()

    for row in pairs:
        score = row.get("total_score") or 0.0
        gap = row.get("gap_days")
        matched = set(row.get("matched_on") or ())
        pred = row.get("predecessor") or {}
        succ = row.get("successor") or {}

        counts["pairs"] += 1
        predecessors.add(pred.get("dot_number"))

        # Two readings of "shares a vehicle and nothing else", both tracked
        # because they answer different questions and disagreed by 156 pairs
        # on the baseline (519 against 675). The strict one is the literal
        # population; the identity one is the population that exists ONLY
        # because vin-overlap is marked conclusive, since agent (0.04) and
        # temporal (0.05) cannot lift a pair over the 0.35 floor between them.
        # Collapsing them into one metric would silently pick a side.
        if matched == {"vin-overlap"}:
            counts["vin_only"] += 1
        if matched & IDENTITY_SIGNAL_TYPES == {"vin-overlap"}:
            counts["vin_only_identity"] += 1

        if score < TRIAGE_SCORE:
            continue
        counts["pairs_ge_070"] += 1
        if _is_coherent(gap):
            counts["coherent_ge_070"] += 1

        if not matched & CORROBORATING:
            continue
        if gap is not None and gap <= COHERENT_MAX_GAP:
            counts["triage_unbounded"] += 1
            identical = (
                pred.get("legal_name") is not None
                and pred.get("legal_name") == succ.get("legal_name")
            )
            if identical:
                counts["identical_name_triage"] += 1
            if gap >= 0:
                counts["triage_bounded"] += 1
                if identical and gap <= CANARY_MAX_GAP and score >= CANARY_SCORE:
                    counts["canary"] += 1

    counts["predecessors_with_pairs"] = len(predecessors)
    counts["coherent_share_ge_070"] = (
        counts["coherent_ge_070"] / counts["pairs_ge_070"]
        if counts["pairs_ge_070"]
        else 0.0
    )
    return counts


def compare(baseline: dict, candidate: dict, expectations: dict) -> list[MetricDelta]:
    """Diff two metric records against expectations declared before the run.

    Raises rather than defaulting on an unknown expectation or a missing
    baseline metric: a typo that quietly became "no opinion" would let a
    regression through wearing a green check, which is the failure this whole
    harness exists to prevent.
    """
    deltas = []
    for name, expectation in expectations.items():
        if expectation not in EXPECTATIONS:
            raise ValueError(
                "unknown expectation {!r} for metric {!r}; known are {}".format(
                    expectation, name, ", ".join(EXPECTATIONS)
                )
            )
        before = baseline[name]
        after = candidate[name]
        delta = after - before
        pct = (delta / before) if before else None

        if expectation == "informational":
            ok = True
        elif expectation == "must_not_fall":
            ok = delta >= 0
        elif expectation == "must_not_rise":
            ok = delta <= 0
        else:
            ok = pct is None or pct >= -TOLERANCE

        deltas.append(MetricDelta(name, before, after, delta, pct, expectation, ok))
    return deltas
