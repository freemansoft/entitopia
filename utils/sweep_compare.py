"""Generic diff engine for judging whether a re-run of a pair-scoring sweep improved or regressed.

Declares an expectation for each metric before the run (must not fall, must
not rise, informational, or within a tolerance), then diffs a baseline metric
record against a candidate one and reports which expectations held. This needs
no project-specific vocabulary — only that both records are dicts of metric
name to number — so it lives in utils/ for any project scoring pairs to reuse.
Deciding what a "metric" means for a given project (what to count, what
"coherent" or "triage" means) is that project's job: see
DOT-Commercial/precision_metrics.py for the chameleon-detection version.

Kept free of Elasticsearch imports on purpose: anything here must be callable
from a test with plain dicts, the same split that keeps
DOT-Commercial/crash_lift.py testable while its measure_crash_lift.py stays
integration-shaped.
"""

from collections.abc import Iterable
from dataclasses import dataclass

# What each expectation permits, because the names alone mislead:
#   must_not_fall        any rise, no fall
#   must_not_rise        any fall, no rise
#   within_10pct         any rise, a fall of at most 10% -- ONE-SIDED. It is
#                        the recall tripwire for metrics a change is meant to
#                        grow, so it never fails on a rise.
#   stable_within_10pct  at most 10% either way; from a zero baseline, only zero
#   informational        anything; printed, never judged
EXPECTATIONS = ("must_not_fall", "must_not_rise", "informational", "within_10pct", "stable_within_10pct")

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
        elif expectation == "stable_within_10pct":
            ok = after == before if not before else abs(pct) <= TOLERANCE
        else:
            ok = pct is None or pct >= -TOLERANCE

        deltas.append(MetricDelta(name, before, after, delta, pct, expectation, ok))
    return deltas


def signal_type_drift(baseline_types: Iterable[str], candidate_types: Iterable[str]) -> frozenset[str]:
    """Signal types that occur in one sweep's `matched_on` and never in the other's.

    Non-empty means the two sweeps do not share a vocabulary, so any metric
    that names a signal type reads zero on the side that used the other
    name. Measured: the vin-overlap -> shared-token rename made a
    must_not_fall guard print "ok" over 0 -> 208, a rename passing as a
    measurement. Kept here, free of Elasticsearch, so the decision is
    testable with plain sets.
    """
    return frozenset(baseline_types) ^ frozenset(candidate_types)
