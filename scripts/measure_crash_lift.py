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
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_crash_lift.py`, which puts scripts/
# on sys.path rather than the repo root, so utils.crash_lift is unimportable
# without this. Same fix as measure_address_analyzers.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch

from utils.crash_lift import (
    SCORE_BANDS,
    band_for,
    crashed_after_registration,
    fleet_size_band,
    months_between,
    rate,
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
                "crashed": crashed_after_registration(add, crashes.get(dot, [])),
                "exposure": max(months_between(max(add, window_start), window_end), 0.0),
                "registered_before_window": add < window_start,
                "stratum": (add // 10000, attribute["fleet"], attribute["state"]),
            }
        )
    return rows


def band_table(rows, title):
    """Crash rate per score band, printed with its denominator.

    The denominator is printed beside every rate because a 100% rate over
    three carriers and a 100% rate over three thousand are different claims,
    and a table of bare percentages invites reading them as the same one.
    """
    print("\n{}".format(title))
    print("  {:<12} {:>10} {:>10} {:>9}".format("band", "carriers", "crashed", "rate"))
    for _, _, label in SCORE_BANDS:
        band_rows = [r for r in rows if r["band"] == label]
        crashed = sum(1 for r in band_rows if r["crashed"])
        proportion = rate(crashed, len(band_rows))
        print(
            "  {:<12} {:>10,} {:>10,} {:>9}".format(
                label,
                len(band_rows),
                crashed,
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
    for _, _, label in SCORE_BANDS:
        band_rows = [r for r in rows if r["band"] == label]
        exposure = sum(r["exposure"] for r in band_rows)
        crashed = sum(1 for r in band_rows if r["crashed"])
        print(
            "  {:<12} {:>10,} {:>18}".format(
                label,
                len(band_rows),
                "n/a" if exposure <= 0 else "{:.2f}".format(1000 * crashed / exposure),
            )
        )


def _print_placebo(restricted, seed):
    """Re-band the same carriers under permuted scores, as the falsifiability check.

    Copies each row before touching "band" so the shuffle can never leak into
    the real bands used by the tables above. If bands assigned from permuted
    scores still trend, the trend in the real table is an artifact of the
    banding itself (bin-edge placement, population size per bin) rather than
    the score predicting anything, and no other result in this run can be
    trusted until that is fixed.
    """
    placebo = [dict(r) for r in restricted]
    shuffled = [r["score"] for r in placebo]
    random.Random(seed).shuffle(shuffled)
    for row, score in zip(placebo, shuffled, strict=True):
        row["band"] = band_for(score)
    band_table(placebo, "PLACEBO (permuted scores; MUST be flat or the banding is wrong)")


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


def _control_comparison(client, args, restricted, window_start):
    """Standardize the whole unflagged population (not a sample) to the flagged cohort's mix, and print the lift.

    Split out of main() so this section's several supporting queries
    (population and crashed-population stratum counts) don't push main()'s
    local-variable count over ruff's PLR0914 budget.
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
        "  unflagged, standardized  : {} of {:,}".format(
            "n/a" if standardized is None else "{:.2%}".format(standardized), control_total
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
    return parser.parse_args()


def main():
    """Read the live cluster and print the crash-lift report end to end.

    The seam between CLI/ES-client setup and the report body, and the report
    body itself is delegated to helpers (band_table, _print_exposure_table,
    _print_placebo, _control_comparison) so this function stays short enough
    to read as an outline rather than tripping ruff's statement-count limit.
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
    _print_placebo(restricted, args.seed)
    _control_comparison(client, args, restricted, window_start)

    print(
        "\nNOTE: fleet size drives crashes through miles driven and is a matching "
        "stratum above. If the lift appears only in the raw proportion and not in "
        "the standardized comparison, it is confounded by size and must not be "
        "reported as evidence the score ranks risk."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
