"""Arithmetic for validating the chameleon score against crash outcomes.

Exists so the parts of the validation that decide what a number MEANS can be
tested without a cluster. The script that reads Elasticsearch is unavoidably
integration-shaped; this is not, and a banding or reweighting error would
otherwise only ever surface as a table that looks reasonable and is wrong.

Kept free of Elasticsearch imports on purpose: anything here must be callable
from a test with plain dicts.
"""

from datetime import date

# Fixed by docs/superpowers/specs/2026-08-06-crash-outcome-validation-design.md
# BEFORE the first run, and deliberately reusing thresholds the project already
# committed to — 0.35 is the emit floor, 0.70 the triage threshold, and the
# README already calls everything under 0.50 noise. Edges chosen after seeing
# the outcome are the standard way this analysis fools its author, so changing
# them makes a run a new measurement rather than a refined one.
SCORE_BANDS = [
    (0.35, 0.50, "0.35-0.50"),
    (0.50, 0.60, "0.50-0.60"),
    (0.60, 0.70, "0.60-0.70"),
    (0.70, 0.80, "0.70-0.80"),
    (0.80, 0.90, "0.80-0.90"),
    (0.90, 1.00, "0.90-1.00"),
]

# Fleet-size strata fixed by design. Lower bound is inclusive; upper is too
# (unlike SCORE_BANDS). See fleet_size_band() for the `unknown` stratum logic.
FLEET_SIZE_BANDS = [
    (1, 1, "1"),
    (2, 5, "2-5"),
    (6, 20, "6-20"),
    (21, 100, "21-100"),
    (101, float("inf"), "100+"),
]


def band_for(score):
    """Which score band a successor falls in, or None if it is out of range.

    Half-open intervals except the last, so a score never lands in two bands
    and 1.0 still has a home. None rather than a catch-all bucket because a
    score below the emit floor means the caller passed something that is not a
    pair score, and silently bucketing it would hide that.
    """
    if score is None:
        return None
    for lower, upper, label in SCORE_BANDS:
        if lower <= score < upper:
            return label
    if score == SCORE_BANDS[-1][1]:
        return SCORE_BANDS[-1][2]
    return None


def fleet_size_band(power_units):
    """Coarse fleet-size stratum, because crash exposure scales with trucks.

    Banded rather than used raw so control strata have enough carriers in them
    to produce a stable rate. `unknown` is separate from `1` deliberately: a
    carrier that never filed a power-unit count is not a carrier with one
    truck, and merging them would shift real carriers between strata and bias
    the standardized rate.
    """
    if power_units is None:
        return "unknown"
    count = int(power_units)
    for lower, upper, label in FLEET_SIZE_BANDS:
        if lower <= count <= upper:
            return label
    return None


def to_yyyymmdd(add_date):
    """Render a carrier `add_date` into the integer space `report_date` uses.

    Comparison happens in YYYYMMDD integer space rather than by parsing
    `report_date` into a date, because `report_date` is mapped `long` and
    parsing 333k of them per run to compare against one registration date
    would be work done in the wrong direction. None when absent so the caller
    can exclude the carrier rather than guess a registration date.
    """
    if not add_date:
        return None
    return int(str(add_date)[:10].replace("-", ""))


def months_between(start_yyyymmdd, end_yyyymmdd):
    """Fractional months of observation between two YYYYMMDD integers.

    Fractional rather than whole months because the exposure-normalized view
    exists precisely for carriers registered partway through the crash window;
    rounding their exposure to whole months would erase the distinction it was
    added to preserve.
    """
    start = date(start_yyyymmdd // 10000, start_yyyymmdd // 100 % 100, start_yyyymmdd % 100)
    end = date(end_yyyymmdd // 10000, end_yyyymmdd // 100 % 100, end_yyyymmdd % 100)
    return (end - start).days / 30.4375


def crashed_after_registration(add_yyyymmdd, report_dates):
    """Whether any crash postdates the carrier's registration.

    The entire causal claim of this measurement rests here: a crash that
    predates registration belongs to whoever held that DOT number before, and
    counting it would let the predecessor's history leak into the successor's
    outcome — manufacturing exactly the correlation being tested for.

    Strictly after, so a same-day crash does not count. That biases against
    finding an effect, which is the safe direction for a validation.
    """
    if add_yyyymmdd is None:
        return False
    return any(report_date > add_yyyymmdd for report_date in report_dates)


def rate(numerator, denominator):
    """Proportion, or None when the denominator is empty.

    None and 0.0 mean different things and conflating them is the reporting
    equivalent of this repo's recurring silent-wrong-output bug: None is "no
    carriers fell in this band", 0.0 is "carriers fell here and none crashed".
    Printing 0.0% for an empty band invents a measurement that was never made.
    """
    if not denominator:
        return None
    return numerator / denominator


def standardize(flagged_counts, control_counts):
    """Control crash rate reweighted to the flagged population's stratum mix.

    Answers "what rate would the control group show if it had the flagged
    group's distribution of registration cohort, fleet size and state?" —
    which is the only version of the comparison that is not dominated by those
    confounders. Fleet size in particular drives crashes through miles driven,
    so an unadjusted control rate would mostly measure how big the carriers
    are.

    Direct standardization rather than drawing a matched sample: it is
    deterministic, so the published number is reproducible without recording a
    random seed, and it uses every control carrier rather than discarding most
    of them. Sampling would add run-to-run noise to a figure whose entire
    purpose is to be quoted and re-derived.

    Returns the standardized rate and the list of strata that had flagged
    carriers but no controls. Those are returned rather than dropped because
    silently ignoring them would redefine the comparison population without
    saying so.
    """
    total_flagged = sum(total for _, total in flagged_counts.values())
    if not total_flagged:
        return None, []

    weighted = 0.0
    represented = 0
    skipped = []
    for stratum, (_, flagged_total) in sorted(flagged_counts.items()):
        control = control_counts.get(stratum)
        control_rate = rate(*control) if control else None
        if control_rate is None:
            skipped.append(stratum)
            continue
        weighted += flagged_total * control_rate
        represented += flagged_total

    if not represented:
        return None, skipped
    return weighted / represented, skipped
