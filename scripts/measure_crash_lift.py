"""Measure whether the chameleon score predicts appearing in the crash file.

Exists because every figure this project commits describes what the matcher
did, not whether it was right — a scorer ranking carriers by ZIP code would
produce equally clean counts. GAO-12-364 measured 18% of applicants with
chameleon attributes in severe crashes against 6% without, and that shape is
reproducible here because the crash data is already loaded and no signal in
entity-match.json reads it. The outcome is genuinely external.

Run after a sweep; quote its output in DOT-Commercial/README.md WITH the
filters, per that README's own standard.
"""

import argparse
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_crash_lift.py`, which puts scripts/
# on sys.path rather than the repo root, so utils.crash_lift is unimportable
# without this. Same fix as measure_address_analyzers.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from utils.crash_lift import (
    SCORE_BANDS,
    band_for,
    crashed_after_registration,
    fleet_size_band,
    months_between,
    rate,
    recency_cohort,
    standardize,
    to_yyyymmdd,
)

PAGE = 1000


def successor_scores(client, pairs_index, limit=None):
    """Highest score per distinct successor, keyed by DOT number as a string.

    Reduced to one entry per carrier because a successor appearing in forty
    pairs would otherwise contribute forty times to a rate, weighting the
    result by how many shut-down carriers happened to resemble it. The max is
    taken in Elasticsearch via a composite aggregation, which pages
    deterministically and never splits a bucket across pages.

    Keys are strings because `successor.dot_number` is `keyword` here but
    `long` on the crashes index; normalizing at every boundary is what stops
    the two sides silently intersecting to nothing.
    """
    scores = {}
    after = None
    while True:
        sources = [{"dot": {"terms": {"field": "successor.dot_number"}}}]
        composite = {"size": PAGE, "sources": sources}
        if after:
            composite["after"] = after
        response = client.search(
            index=pairs_index,
            size=0,
            aggs={"s": {"composite": composite, "aggs": {"best": {"max": {"field": "total_score"}}}}},
            track_total_hits=False,
        )
        agg = response["aggregations"]["s"]
        for bucket in agg["buckets"]:
            scores[str(bucket["key"]["dot"])] = bucket["best"]["value"]
        after = agg.get("after_key")
        if not after or (limit and len(scores) >= limit):
            return scores


def crash_window(client, crashes_index):
    """Earliest and latest report_date actually present, as YYYYMMDD integers.

    Read from the data rather than hardcoded because fetch-config.json pulls
    crashes on a rolling 24-month window: a pinned date would keep printing
    plausible exposure numbers long after the window moved underneath it.
    """
    response = client.search(
        index=crashes_index,
        size=0,
        aggs={"lo": {"min": {"field": "report_date"}}, "hi": {"max": {"field": "report_date"}}},
        track_total_hits=False,
    )
    return (
        int(response["aggregations"]["lo"]["value"]),
        int(response["aggregations"]["hi"]["value"]),
    )


def crash_dates(client, crashes_index, dot_numbers):
    """Report dates per carrier, for carriers that appear in the crash file.

    Only carriers with at least one crash come back, so absence from the
    result is the "no crash" outcome rather than an error. Queried in batches
    by DOT number instead of scanning the whole crash index, because the
    flagged population is a small fraction of it.

    The `dates` sub-aggregation caps at `size: 200` per carrier, and without
    an explicit order `terms` fills that cap by doc_count, not by date value —
    so a carrier with more than 200 distinct report dates can have its
    earliest, doc_count-losing dates truncated out. `crashed_after_registration`
    only needs one surviving date after `add_date` to mark a carrier crashed,
    so this is harmless as long as at least one post-registration date makes
    the cut, which is true for every carrier in the restricted cohort measured
    here (busiest holds 681 distinct dates, comfortably over the cap, but
    still marks crashed). It stops being harmless for a carrier registered
    inside the crash window whose more-than-200 distinct dates are dominated
    by pre-registration dates from the DOT number's prior holder: if every
    post-registration date happens to fall in the truncated tail, that carrier
    silently scores "not crashed" instead of erroring, which is exactly the
    failure mode this project treats as the worse one.
    """
    found = {}
    for start in range(0, len(dot_numbers), PAGE):
        batch = [str(d) for d in dot_numbers[start : start + PAGE]]
        after = None
        while True:
            composite = {
                "size": PAGE,
                "sources": [{"dot": {"terms": {"field": "dot_number"}}}],
            }
            if after:
                composite["after"] = after
            response = client.search(
                index=crashes_index,
                size=0,
                query={"terms": {"dot_number": batch}},
                aggs={
                    "c": {
                        "composite": composite,
                        "aggs": {"dates": {"terms": {"field": "report_date", "size": 200}}},
                    }
                },
                track_total_hits=False,
            )
            agg = response["aggregations"]["c"]
            for bucket in agg["buckets"]:
                key = str(bucket["key"]["dot"])
                found.setdefault(key, []).extend(
                    int(d["key"]) for d in bucket["dates"]["buckets"]
                )
            after = agg.get("after_key")
            if not after:
                break
    return found


def carrier_attributes(client, carriers_index, dot_numbers):
    """Registration date, fleet size and state per carrier, for stratification.

    `phy_state` is read from the source document rather than aggregated,
    because the field is `text` with a `.keyword` subfield and reading
    `_source` sidesteps the trap entirely.
    """
    attributes = {}
    for start in range(0, len(dot_numbers), PAGE):
        batch = [str(d) for d in dot_numbers[start : start + PAGE]]
        response = client.search(
            index=carriers_index,
            size=len(batch),
            query={"terms": {"dot_number": batch}},
            source_includes=["dot_number", "add_date", "nbr_power_unit", "phy_state"],
            track_total_hits=False,
        )
        for hit in response["hits"]["hits"]:
            source = hit["_source"]
            attributes[str(source["dot_number"])] = {
                "add": to_yyyymmdd(source.get("add_date")),
                "fleet": fleet_size_band(source.get("nbr_power_unit")),
                "state": source.get("phy_state"),
            }
    return attributes


def build_rows(scores, crashes, attributes, window_start, window_end):
    """One record per successor: band, crash outcome, exposure, stratum.

    Built once and reused for the restricted view, the exposure-normalized
    view and the placebo, so all three describe exactly the same population.
    Recomputing per view is how three tables that should agree stop agreeing.
    """
    rows = []
    for dot, score in scores.items():
        attribute = attributes.get(dot)
        if not attribute or attribute["add"] is None:
            continue
        add = attribute["add"]
        rows.append(
            {
                "dot": dot,
                "score": score,
                "band": band_for(score),
                "add": add,
                "crashed": crashed_after_registration(add, crashes.get(dot, [])),
                "exposure": max(months_between(max(add, window_start), window_end), 0.0),
                "registered_before_window": add < window_start,
                "stratum": (add // 10000, attribute["fleet"], attribute["state"]),
            }
        )
    return rows


def _band_stats(rows):
    """Carrier count, crash count, raw rate and exposure-normalized rate per score band, over one set of rows.

    Every caller that reports on a single view — the printed restricted/full/
    placebo tables, the printed exposure table, and the persisted band
    documents — computed this same per-band arithmetic independently until
    this was pulled out. Two loops over the same rows producing the same
    numbers by construction still means a future edit to one and not the
    other silently desyncs a printed table from what gets persisted (or from
    each other); recency rows already had this guard via _recency_stats, this
    gives band rows the same one.
    """
    stats = []
    for _, _, label in SCORE_BANDS:
        band_rows = [r for r in rows if r["band"] == label]
        crashed = sum(1 for r in band_rows if r["crashed"])
        exposure = sum(r["exposure"] for r in band_rows)
        stats.append(
            {
                "band": label,
                "carriers": len(band_rows),
                "crashed": crashed,
                "rate": rate(crashed, len(band_rows)),
                "crashes_per_1000_months": (
                    None if exposure <= 0 else 1000 * crashed / exposure
                ),
            }
        )
    return stats


def band_table(rows, title):
    """Crash rate per score band, printed with its denominator.

    The denominator is printed beside every rate because a 100% rate over
    three carriers and a 100% rate over three thousand are different claims,
    and a table of bare percentages invites reading them as the same one.
    """
    print("\n{}".format(title))
    print("  {:<12} {:>10} {:>10} {:>9}".format("band", "carriers", "crashed", "rate"))
    for stat in _band_stats(rows):
        proportion = stat["rate"]
        print(
            "  {:<12} {:>10,} {:>10,} {:>9}".format(
                stat["band"],
                stat["carriers"],
                stat["crashed"],
                "n/a" if proportion is None else "{:.2%}".format(proportion),
            )
        )


def stratum_counts(rows):
    """Crash counts per (cohort, fleet band, state) stratum for standardizing."""
    counts = {}
    for row in rows:
        crashed, total = counts.get(row["stratum"], (0, 0))
        counts[row["stratum"]] = (crashed + (1 if row["crashed"] else 0), total + 1)
    return counts


def _print_exposure_table(rows):
    """Crashes per 1000 carrier-months per band, comparable despite unequal follow-up.

    A per-carrier rate would conflate a higher hazard with simply having
    watched a band's carriers longer (later add_date, less window remaining);
    dividing by observed months is what makes bands comparable regardless of
    when in the window each carrier registered.
    """
    print("\nexposure-normalized, full set")
    print("  {:<12} {:>10} {:>18}".format("band", "carriers", "crashes/1000 months"))
    for stat in _band_stats(rows):
        print(
            "  {:<12} {:>10,} {:>18}".format(
                stat["band"],
                stat["carriers"],
                "n/a" if stat["crashes_per_1000_months"] is None
                else "{:.2f}".format(stat["crashes_per_1000_months"]),
            )
        )


RECENCY_COHORTS = ["under-1y", "1-3y", "3y-plus"]


def _recency_stats(rows, window_end):
    """Crash rate per (band, cohort) cell, shared by the printed dose-response
    table and the persisted recency rows.

    Split out rather than computed twice for the same reason build_rows is
    computed once and reused across the restricted/full/placebo views: two
    call sites recomputing the same cells is how a printed table and a stored
    one quietly stop agreeing.
    """
    stats = []
    for _, _, label in SCORE_BANDS:
        for cohort in RECENCY_COHORTS:
            subset = [
                r for r in rows
                if r["band"] == label and recency_cohort(r["add"], window_end) == cohort
            ]
            exposure = sum(r["exposure"] for r in subset)
            crashed = sum(1 for r in subset if r["crashed"])
            stats.append(
                {
                    "band": label,
                    "cohort": cohort,
                    "carriers": len(subset),
                    "crashed": crashed,
                    "crashes_per_1000_months": (
                        None if exposure <= 0 else 1000 * crashed / exposure
                    ),
                }
            )
    return stats


def recency_table(rows, window_end):
    """Dose-response split by how recently the successor registered.

    Exists because the headline (band_table on `restricted`) structurally
    excludes carriers registered inside the crash window — exactly the
    population an active chameleon would fall into. Run over the FULL row set
    rather than the restricted cohort, since including those fresh
    registrations is the entire point of this table. Exposure-normalized
    rather than a raw proportion because the recent cohorts have had less
    time to crash by construction; comparing raw proportions across them
    would measure exposure, not risk. Every cell prints its carrier count
    because the recent cohorts will be small, and a rate over 40 carriers is
    not the claim a rate over 40,000 is.
    """
    print("\nDOSE-RESPONSE BY REGISTRATION RECENCY (crashes per 1,000 exposure-months, n in parens)")
    print("  {:<12} {:>22} {:>22} {:>22}".format("band", *RECENCY_COHORTS))
    stats = _recency_stats(rows, window_end)
    for _, _, label in SCORE_BANDS:
        cells = []
        for cohort in RECENCY_COHORTS:
            cell = next(s for s in stats if s["band"] == label and s["cohort"] == cohort)
            cells.append(
                "n/a ({})".format(cell["carriers"])
                if cell["crashes_per_1000_months"] is None
                else "{:.2f} ({:,})".format(cell["crashes_per_1000_months"], cell["carriers"])
            )
        print("  {:<12} {:>22} {:>22} {:>22}".format(label, *cells))


def _print_placebo(restricted, seed):
    """Re-band the same carriers under permuted scores, as the falsifiability check.

    Copies each row before touching "band" so the shuffle can never leak into
    the real bands used by the tables above. If bands assigned from permuted
    scores still trend, the trend in the real table is an artifact of the
    banding itself (bin-edge placement, population size per bin) rather than
    the score predicting anything, and no other result in this run can be
    trusted until that is fixed.

    Returns the permuted rows so a caller persisting this run can store them
    as the "placebo" view alongside "restricted" and "full" — the same rows
    the printed table above is built from, not a second, independently
    shuffled draw.
    """
    placebo = [dict(r) for r in restricted]
    shuffled = [r["score"] for r in placebo]
    random.Random(seed).shuffle(shuffled)
    for row, score in zip(placebo, shuffled, strict=True):
        row["band"] = band_for(score)
    band_table(placebo, "PLACEBO (permuted scores; MUST be flat or the banding is wrong)")
    return placebo


def distinct_crashed_dot_numbers(client, crashes_index):
    """Every DOT number appearing at least once in the crash file, unsampled.

    Feeds the whole-population half of the control comparison. The prior
    approach drew a --control-size SAMPLE of unflagged carriers by paging the
    carriers index in dot_number order and stopping at the requested size —
    which silently returned the OLDEST carriers in the file, because DOT
    numbers are assigned chronologically, biasing the control group toward
    established, high-mileage operations that crash more than average. The
    replacement computes exact unflagged counts by subtracting the flagged
    cohort's per-stratum counts from the whole population's, and that
    requires every crashed carrier, not a sample of them.
    """
    dots = []
    after = None
    while True:
        composite = {"size": PAGE, "sources": [{"dot": {"terms": {"field": "dot_number"}}}]}
        if after:
            composite["after"] = after
        response = client.search(
            index=crashes_index,
            size=0,
            aggs={"d": {"composite": composite}},
            track_total_hits=False,
        )
        agg = response["aggregations"]["d"]
        if not agg["buckets"]:
            break
        dots.extend(str(b["key"]["dot"]) for b in agg["buckets"])
        after = agg.get("after_key")
        if not after:
            break
    return dots


def _iso_date(yyyymmdd):
    """Render a YYYYMMDD int as `yyyy-MM-dd`, the form add_date's mapping accepts in a range query."""
    return "{:04d}-{:02d}-{:02d}".format(yyyymmdd // 10000, yyyymmdd // 100 % 100, yyyymmdd % 100)


def population_stratum_counts(client, carriers_index, window_start):
    """Every carrier registered before the crash window, bucketed by build_rows's stratum key.

    The population half of the whole-population control comparison: paired
    with crashed_stratum_counts and subtracted against the flagged cohort in
    _unflagged_stratum_counts, this gives exact unflagged per-stratum totals
    without sampling. `missing_bucket=True` on the state and power-unit
    sources is what makes a carrier missing phy_state or nbr_power_unit land
    in this composite's null-keyed bucket instead of being silently dropped —
    ES's composite aggregation drops docs missing a source field by default,
    and build_rows keeps those carriers (state=None, fleet="unknown"), so
    dropping them here would undercount the population relative to the
    flagged side and bias every stratum's control rate.
    """
    counts = {}
    after = None
    query = {"range": {"add_date": {"lt": _iso_date(window_start)}}}
    while True:
        composite = {
            "size": PAGE,
            "sources": [
                {
                    "cohort": {
                        "date_histogram": {
                            "field": "add_date",
                            "calendar_interval": "year",
                            "format": "yyyy",
                        }
                    }
                },
                {"state": {"terms": {"field": "phy_state.keyword", "missing_bucket": True}}},
                {
                    "power": {
                        "histogram": {"field": "nbr_power_unit", "interval": 1, "missing_bucket": True}
                    }
                },
            ],
        }
        if after:
            composite["after"] = after
        response = client.search(
            index=carriers_index,
            size=0,
            query=query,
            aggs={"pop": {"composite": composite}},
            track_total_hits=False,
        )
        agg = response["aggregations"]["pop"]
        if not agg["buckets"]:
            break
        for bucket in agg["buckets"]:
            key = bucket["key"]
            power = key["power"]
            stratum = (int(key["cohort"]), fleet_size_band(None if power is None else int(power)), key["state"])
            counts[stratum] = counts.get(stratum, 0) + bucket["doc_count"]
        after = agg.get("after_key")
        if not after:
            break
    return counts


def crashed_stratum_counts(client, carriers_index, crashes_index, window_start):
    """Crash counts per stratum for every carrier that ever appears in the crash file.

    The other half of the whole-population control comparison, computed the
    same way build_rows computes an individual row's outcome — reusing
    crashed_after_registration so a crash's stratum credit follows the same
    causal guard as the flagged cohort: a crash predating registration
    belongs to the predecessor, not this carrier.
    """
    crashed_dots = distinct_crashed_dot_numbers(client, crashes_index)
    attributes = carrier_attributes(client, carriers_index, crashed_dots)
    dates = crash_dates(client, crashes_index, crashed_dots)
    counts = {}
    for dot in crashed_dots:
        attribute = attributes.get(dot)
        if not attribute or attribute["add"] is None:
            continue
        add = attribute["add"]
        if add >= window_start or not crashed_after_registration(add, dates.get(dot, [])):
            continue
        stratum = (add // 10000, attribute["fleet"], attribute["state"])
        counts[stratum] = counts.get(stratum, 0) + 1
    return counts


def _unflagged_stratum_counts(population, crashed_population, flagged):
    """Population counts minus flagged counts, giving exact unflagged counts without sampling.

    Subtraction rather than querying the carriers index for "NOT IN flagged",
    because the flagged set is on the order of 200k DOT numbers and a
    composite aggregation has no efficient way to exclude that many terms;
    both sides already have per-stratum totals, so subtracting is exact.

    Returns the unflagged counts and the list of strata where subtraction
    went negative. That should never happen if population and flagged share
    exactly the same stratum-key definition (they call the same
    fleet_size_band and read the same cohort/state fields) — a nonempty list
    means that assumption broke somewhere, and the caller must say so rather
    than silently reporting a rate built from a negative count.
    """
    counts = {}
    negative = []
    for stratum, total in population.items():
        flagged_crashed, flagged_total = flagged.get(stratum, (0, 0))
        crashed = crashed_population.get(stratum, 0) - flagged_crashed
        remaining = total - flagged_total
        if crashed < 0 or remaining < 0:
            negative.append(stratum)
            continue
        counts[stratum] = (crashed, remaining)
    return counts, negative


@dataclass
class ControlResult:
    """The three numbers persistence needs out of the control comparison, bundled.

    _control_comparison already has several locals of its own; unpacking three
    more into main() to carry through to the summary row would push main()
    over ruff's local-variable budget for what is really one piece of state —
    the outcome of this one comparison.
    """

    flagged_rate: float | None
    standardized: float | None
    skipped: list


def _control_comparison(client, args, restricted, window_start):
    """Standardize the whole unflagged population (not a sample) to the flagged cohort's mix, print the lift, and return it.

    Split out of main() so this section's several supporting queries
    (population and crashed-population stratum counts) don't push main()'s
    local-variable count over ruff's PLR0914 budget. Returns a ControlResult
    so the summary row persisted by _persist_results carries the same numbers
    just printed, rather than a second, independently derived copy.
    """
    flagged = stratum_counts(restricted)
    population = population_stratum_counts(client, args.carriers_index, window_start)
    crashed_population = crashed_stratum_counts(
        client, args.carriers_index, args.crashes_index, window_start
    )
    control_counts, negative_strata = _unflagged_stratum_counts(
        population, crashed_population, flagged
    )
    standardized, skipped = standardize(flagged, control_counts)

    # standardize() weights each stratum's control rate by that stratum's
    # FLAGGED total, then divides by the flagged total it actually had a
    # control rate for (skipping strata with flagged carriers but no
    # controls) — so the standardized rate's real denominator is this
    # "represented" figure, not control_total (every unflagged carrier across
    # every stratum, most of which never enter the weighted sum at all).
    # Printing standardized against control_total was the bug: it attached a
    # ~1.6M-carrier denominator to a rate that is actually a weighted average
    # over ~197K flagged successors.
    skipped_set = set(skipped)
    represented_flagged = sum(
        total for stratum, (_, total) in flagged.items() if stratum not in skipped_set
    )

    control_total = sum(total for _, total in control_counts.values())
    control_crashed = sum(crashed for crashed, _ in control_counts.values())
    raw_control_rate = rate(control_crashed, control_total)

    flagged_crashed = sum(1 for r in restricted if r["crashed"])
    flagged_rate = rate(flagged_crashed, len(restricted))
    print("\nCONTROL COMPARISON (restricted cohort; whole unflagged population, not a sample)")
    print("  flagged successors      : {:.2%} of {:,}".format(flagged_rate or 0, len(restricted)))
    print(
        "  unflagged, raw           : {} of {:,}".format(
            "n/a" if raw_control_rate is None else "{:.2%}".format(raw_control_rate), control_total
        )
    )
    print(
        "  unflagged, standardized  : {} — weighted mean over {:,} represented flagged successors "
        "({:,} restricted total, {} strata excluded for no controls); raw unflagged pool is {:,}, "
        "not this rate's denominator".format(
            "n/a" if standardized is None else "{:.2%}".format(standardized),
            represented_flagged,
            len(restricted),
            len(skipped),
            control_total,
        )
    )
    if standardized:
        print(
            "  lift: {:.2f}x  (GAO measured 18% vs 6%, 3.0x)".format(
                (flagged_rate or 0) / standardized
            )
        )
    if skipped:
        print("  strata with no controls, excluded from the weighted total: {}".format(len(skipped)))
    if negative_strata:
        print(
            "  WARNING: {} strata had negative unflagged counts after subtraction; "
            "excluded, the figures above are on the remaining strata only. This means "
            "population and flagged disagreed on a stratum key somewhere and needs "
            "investigating before this run is trusted.".format(len(negative_strata))
        )
    return ControlResult(flagged_rate, standardized, skipped)


def source_fingerprint(client, carriers_index):
    """The analysis fingerprint stamped on the carriers index being measured.

    Carried onto every result document so a stored result can be tied back to
    the token universe that produced it. Without it, matching a result to its
    index means comparing timestamps by hand — which is exactly how this
    project lost track of which figures came from which run.
    """
    mapping = client.indices.get_mapping(index=carriers_index)
    for index_mapping in mapping.body.values():
        return index_mapping.get("mappings", {}).get("_meta", {}).get("analysis_fingerprint")
    return None


def _band_documents(views, run_id, generated_at, source):
    """One row per (view, band) cell — the restricted/full/placebo tables flattened for storage.

    Reads _band_stats rather than recomputing per-band arithmetic, so a
    persisted row can never disagree with what band_table (or, for the full
    view, _print_exposure_table) printed for the same run.

    `crashes_per_1000_months` is forced to None for the placebo view. The
    rows underneath it are real carriers with real exposure months, but their
    band comes from a permuted score — so the figure _band_stats would compute
    is a real number attached to a fake grouping, not a rate of anything. It
    is also never printed (_print_placebo calls band_table, which shows only
    `rate`), so a value here would exist solely to be misread by whoever
    queries this alias next, mistaking it for the same exposure-normalized
    figure the full/restricted views carry.
    """
    documents = []
    for view, view_rows in views:
        for stat in _band_stats(view_rows):
            documents.append(
                {
                    "run_id": run_id,
                    "generated_at": generated_at,
                    "row_type": "band",
                    "view": view,
                    "band": stat["band"],
                    "carriers": stat["carriers"],
                    "crashed": stat["crashed"],
                    "rate": stat["rate"],
                    "crashes_per_1000_months": (
                        None if view == "placebo" else stat["crashes_per_1000_months"]
                    ),
                    "source": source,
                }
            )
    return documents


def _recency_documents(rows, window_end, run_id, generated_at, source):
    """One row per (band, cohort) cell of the dose-response table.

    Task 5B added the recency dimension after this script's other rows were
    already the reviewed baseline for persistence; without this, an active
    chameleon's fresh-registration cohort — the population the restricted
    headline structurally excludes — would exist only in a terminal, the same
    gap this task exists to close for every other row.

    `view` is set to "full" because `rows` here is always the caller's full
    row set (recency_table's whole point is to include the fresh
    registrations the restricted view excludes) — without it, a consumer
    filtering band rows by `view` to avoid double-counting restricted/full/
    placebo would silently lose every recency row, since none of them would
    match any `view` value at all.
    """
    return [
        {
            "run_id": run_id,
            "generated_at": generated_at,
            "row_type": "recency",
            "view": "full",
            "band": stat["band"],
            "recency_cohort": stat["cohort"],
            "carriers": stat["carriers"],
            "crashed": stat["crashed"],
            "crashes_per_1000_months": stat["crashes_per_1000_months"],
            "source": source,
        }
        for stat in _recency_stats(rows, window_end)
    ]


def _summary_document(run_id, generated_at, control, source):
    """The one row a later comparison would query first: lift and its two inputs.

    lift is computed here rather than stored as a bare ratio further upstream
    because it is only meaningful when standardized_control_rate is present;
    folding the None-guard into the one place that builds this document is
    what stops a later reader dividing by a None standardized rate.

    placebo_is_flat is deliberately absent: whether the placebo table came out
    flat is a judgment made by reading it, and code asserting its own placebo
    passed would defeat the point of having one.
    """
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "row_type": "summary",
        "flagged_rate": control.flagged_rate,
        "standardized_control_rate": control.standardized,
        "lift": (
            None
            if not control.standardized
            else (control.flagged_rate or 0) / control.standardized
        ),
        "strata_without_controls": len(control.skipped),
        "source": source,
    }


def _build_result_documents(run_id, generated_at, source, restricted, rows, placebo, window_end, control):
    """Assemble one run's full set of persisted rows: band cells, recency cells, one summary.

    Kept separate from the ES write so the row shapes can be checked against
    index-mappings.json field by field, independent of whether a cluster is
    even reachable — this function needs no client.
    """
    views = (("restricted", restricted), ("full", rows), ("placebo", placebo))
    documents = _band_documents(views, run_id, generated_at, source)
    documents.extend(_recency_documents(rows, window_end, run_id, generated_at, source))
    documents.append(_summary_document(run_id, generated_at, control, source))
    return documents


def write_results(client, index, documents):
    """Index one document per reported row, returning how many were written.

    Refreshed on completion because the immediately following read-back check
    would otherwise see nothing — newly indexed documents are not searchable
    for up to a second, which this repo has been bitten by before.
    """
    actions = [{"_index": index, "_source": document} for document in documents]
    written, _ = bulk(client, actions)
    client.indices.refresh(index=index)
    return written


def _persist_results(client, args, window_start, window_end, dots, restricted, rows, placebo, control):
    """Build this run's documents and write them, unless --no-write suppressed it.

    Split out of main() because assembling the run_id/source/document-list
    state is a lot of local variables for one step of the report; keeping it
    here is what lets main() stay a readable outline of the report rather than
    ending in a block of persistence bookkeeping.
    """
    run_id = uuid.uuid4().hex
    generated_at = datetime.now(UTC).isoformat()
    source = {
        "pairs_index": args.pairs_index,
        "carriers_index": args.carriers_index,
        "crashes_index": args.crashes_index,
        "analysis_fingerprint": source_fingerprint(client, args.carriers_index),
        "crash_window_start": window_start,
        "crash_window_end": window_end,
        "distinct_successors": len(dots),
        "restricted_cohort": len(restricted),
    }
    documents = _build_result_documents(
        run_id, generated_at, source, restricted, rows, placebo, window_end, control
    )
    if not args.write:
        return
    written = write_results(client, args.results_alias, documents)
    print("\nwrote {} result rows to {} as run_id {}".format(written, args.results_alias, run_id))


def _parse_args():
    """Command-line surface: which indices to read and which placebo seed to permute with.

    No control-size flag: the control comparison reads the whole unflagged
    population (see _control_comparison), not a sample, so there is nothing
    left to size.

    Split out of main() so main() reads as the report's outline rather than
    starting with a block of argparse boilerplate.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", default="chameleon-candidates-000001")
    parser.add_argument("--carriers-index", default="carriers-000001")
    parser.add_argument("--crashes-index", default="crashes-000001")
    parser.add_argument("--seed", type=int, default=42, help="placebo permutation only")
    parser.add_argument("--results-alias", default="chameleon-validation-000001")
    parser.add_argument("--no-write", dest="write", action="store_false")
    parser.set_defaults(write=True)
    return parser.parse_args()


def main():
    """Read the live cluster, print the crash-lift report end to end, and persist it.

    The seam between CLI/ES-client setup and the report body, and the report
    body itself is delegated to helpers (band_table, _print_exposure_table,
    _print_placebo, _control_comparison, _persist_results) so this function
    stays short enough to read as an outline rather than tripping ruff's
    statement-count limit. Printing happens whether or not --write is set, so
    a run can always be inspected in the terminal even with persistence off.
    """
    args = _parse_args()
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=300
    )

    window_start, window_end = crash_window(client, args.crashes_index)
    print("crash window: {} to {}".format(window_start, window_end))

    scores = successor_scores(client, args.pairs_index)
    dots = list(scores)
    print("distinct successors: {:,}".format(len(dots)))

    attributes = carrier_attributes(client, args.carriers_index, dots)
    crashes = crash_dates(client, args.crashes_index, dots)
    rows = build_rows(scores, crashes, attributes, window_start, window_end)

    restricted = [r for r in rows if r["registered_before_window"]]
    print(
        "analyzable: {:,} of {:,} successors; restricted cohort {:,} "
        "({:.1%} excluded as registered inside the crash window)".format(
            len(rows),
            len(dots),
            len(restricted),
            1 - (len(restricted) / len(rows)) if rows else 0,
        )
    )

    band_table(restricted, "RESTRICTED COHORT (headline; comparable to GAO's 18% / 6%)")
    band_table(rows, "FULL SET (companion; unequal exposure, do not quote as the headline)")
    _print_exposure_table(rows)
    recency_table(rows, window_end)
    placebo = _print_placebo(restricted, args.seed)
    control = _control_comparison(client, args, restricted, window_start)

    print(
        "\nNOTE: fleet size drives crashes through miles driven and is a matching "
        "stratum above. If the lift appears only in the raw proportion and not in "
        "the standardized comparison, it is confounded by size and must not be "
        "reported as evidence the score ranks risk."
    )

    _persist_results(client, args, window_start, window_end, dots, restricted, rows, placebo, control)
    return 0


if __name__ == "__main__":
    sys.exit(main())
