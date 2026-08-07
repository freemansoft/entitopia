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

import sys
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_crash_lift.py`, which puts scripts/
# on sys.path rather than the repo root, so utils.crash_lift is unimportable
# without this. Same fix as measure_address_analyzers.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.crash_lift import fleet_size_band, to_yyyymmdd

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
