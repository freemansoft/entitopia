# Crash Outcome Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether `total_score` predicts an outcome the matcher never sees — appearing in the FMCSA crash file — and record the result so it is re-derivable.

**Architecture:** Pure arithmetic (banding, exposure, standardization) lives in `utils/crash_lift.py` and is unit-tested with no Elasticsearch. All cluster I/O and printing lives in `scripts/measure_crash_lift.py`, following the `measure_address_analyzers.py` precedent of a committed measurement script whose output is quoted in a README.

**Tech Stack:** Python 3.11 from `.venv`, elasticsearch-py 9.4.1, pytest, ruff.

## Global Constraints

- Everything runs from `.venv`. Never `python3` or `pip3`. Tests: `.venv/bin/python -m pytest`.
- `ruff check .` must print `All checks passed!` before any task is complete.
- Elasticsearch calls pass explicit keyword arguments, never `body=`.
- Every function, class and module gets a comment saying **why it exists**, never narrating its steps.
- **Never name a real flagged entity** — no company names, DOT numbers, addresses, phones or emails belonging to matched records, in code, comments, output examples, commit messages or PR text. Aggregate counts are fine.
- Bin edges, the outcome definition and the matching strata are **fixed by the spec before the first run**. Do not adjust them after seeing results.
- Source of truth for design decisions: `docs/superpowers/specs/2026-08-06-crash-outcome-validation-design.md`.

## Verified Facts (do not re-derive; measured 2026-08-06)

| Fact                                          | Value                                                            |
| --------------------------------------------- | ---------------------------------------------------------------- |
| Pairs in `chameleon-candidates-000001`        | 421,846                                                          |
| Distinct successors                           | ~249,549                                                         |
| Crash records                                 | 333,122                                                          |
| Records with fatality/injury/tow-away         | 333,120 (99.9994%) — so presence in file **is** the outcome      |
| Distinct carriers with any crash              | ~122,483 of 2,085,534 = **5.87%** (GAO's non-chameleon rate: 6%) |
| `crashes.dot_number`                          | `long`                                                           |
| `carriers.dot_number`, `successor.dot_number` | `keyword`                                                        |
| `crashes.report_date`                         | `long`, `YYYYMMDD` form (e.g. `20240812`)                        |
| `carriers.add_date`                           | `date`, `strict_date_optional_time                               |     | yyyy-MM-dd` |
| `crashes.tow_away`                            | `text` + `.keyword`; `{"term": {"tow_away": "Y"}}` matches **0** |
| `carriers.phy_state`                          | `text` + `.keyword`                                              |
| `carriers.nbr_power_unit`                     | `float`                                                          |

## File Structure

- **Create `utils/crash_lift.py`** — pure functions: score banding, fleet-size banding, date coercion, exposure months, rate computation, direct standardization. No Elasticsearch import. This is the only file with unit tests.
- **Create `tests/test_crash_lift.py`** — unit tests for the above, plus one integration test that skips when the cluster is unreachable.
- **Create `scripts/measure_crash_lift.py`** — CLI, Elasticsearch reads, output tables. Thin: it composes `utils/crash_lift.py`.
- **Create `DOT-Commercial/configuration/chameleon-validation/`** — `index-config.json` and `index-mappings.json` for the results index, so each run is retained and comparable (Task 6).
- **Modify `DOT-Commercial/configuration.json`** — register the `chameleon-validation` step (Task 6).
- **Modify `DOT-Commercial/README.md`** — record measured output with exact filters (Task 7).
- **Modify `DOT-Commercial/configuration/crashes/index-mappings.json`** — pin `tow_away` (Task 8, independent).

---

### Task 1: Pure banding and coercion helpers

**Files:**

- Create: `utils/crash_lift.py`
- Test: `tests/test_crash_lift.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `SCORE_BANDS: list[tuple[float, float, str]]`, `band_for(score: float) -> str | None`, `fleet_size_band(power_units: float | None) -> str`, `to_yyyymmdd(add_date: str | None) -> int | None`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the crash-outcome validation arithmetic.

These cover the parts that decide what a number MEANS — which band a score
falls in, whether a crash postdates registration, how strata are reweighted.
A defect in any of them produces a plausible-looking table rather than an
error, which is the failure mode this repo keeps hitting.
"""

from utils.crash_lift import SCORE_BANDS, band_for, fleet_size_band, to_yyyymmdd


def test_band_edges_are_half_open_so_no_score_lands_in_two_bands():
    assert band_for(0.70) == "0.70-0.80"
    assert band_for(0.6999) == "0.60-0.70"


def test_perfect_score_lands_in_the_top_band_rather_than_falling_off_the_end():
    # The top band is the only closed interval; 1.0 is attainable and must not
    # silently drop out of the denominator.
    assert band_for(1.0) == "0.90-1.00"


def test_scores_below_the_emit_floor_have_no_band():
    # The sweep cannot emit below 0.35, so a lower value means the caller is
    # passing something that is not a pair score. Returning None makes that
    # visible instead of inventing a bucket for it.
    assert band_for(0.30) is None


def test_bands_are_contiguous_and_ordered():
    for (_, upper, _), (lower, _, _) in zip(SCORE_BANDS, SCORE_BANDS[1:]):
        assert upper == lower


def test_fleet_size_bands_group_the_long_tail():
    assert fleet_size_band(1) == "1"
    assert fleet_size_band(5) == "2-5"
    assert fleet_size_band(20) == "6-20"
    assert fleet_size_band(101) == "100+"


def test_missing_fleet_size_is_its_own_band_not_zero():
    # A carrier that never filed a power-unit count is not a carrier with zero
    # trucks. Folding it into "1" would move real carriers between strata.
    assert fleet_size_band(None) == "unknown"


def test_add_date_becomes_an_integer_comparable_to_report_date():
    # report_date is a long in YYYYMMDD form, so comparison happens in that
    # space rather than by parsing report_date into a date.
    assert to_yyyymmdd("2014-05-29") == 20140529


def test_add_date_with_a_time_component_still_coerces():
    assert to_yyyymmdd("2014-05-29T00:00:00Z") == 20140529


def test_missing_add_date_is_none_so_the_carrier_can_be_excluded():
    assert to_yyyymmdd(None) is None
    assert to_yyyymmdd("") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.crash_lift'`

- [ ] **Step 3: Write the implementation**

```python
"""Arithmetic for validating the chameleon score against crash outcomes.

Exists so the parts of the validation that decide what a number MEANS can be
tested without a cluster. The script that reads Elasticsearch is unavoidably
integration-shaped; this is not, and a banding or reweighting error would
otherwise only ever surface as a table that looks reasonable and is wrong.

Kept free of Elasticsearch imports on purpose: anything here must be callable
from a test with plain dicts.
"""

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
    if count <= 1:
        return "1"
    if count <= 5:
        return "2-5"
    if count <= 20:
        return "6-20"
    if count <= 100:
        return "21-100"
    return "100+"


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add utils/crash_lift.py tests/test_crash_lift.py
git commit -m "Add score and fleet-size banding for crash-outcome validation"
```

---

### Task 2: Exposure and rate arithmetic

**Files:**

- Modify: `utils/crash_lift.py`
- Test: `tests/test_crash_lift.py`

**Interfaces:**

- Consumes: `to_yyyymmdd` from Task 1.
- Produces: `months_between(start_yyyymmdd: int, end_yyyymmdd: int) -> float`, `crashed_after_registration(add_yyyymmdd: int | None, report_dates: list[int]) -> bool`, `rate(numerator: int, denominator: int) -> float | None`.

- [ ] **Step 1: Write the failing tests**

```python
from utils.crash_lift import crashed_after_registration, months_between, rate


def test_a_crash_before_registration_does_not_count():
    # The whole causal claim rests on this. A crash the predecessor had before
    # the successor existed says nothing about the successor.
    assert crashed_after_registration(20250101, [20241201]) is False


def test_a_crash_after_registration_counts():
    assert crashed_after_registration(20250101, [20250102]) is True


def test_a_crash_on_the_registration_date_does_not_count():
    # Strictly after. Same-day is ambiguous and rare; excluding it is the
    # conservative direction, biasing against finding an effect.
    assert crashed_after_registration(20250101, [20250101]) is False


def test_any_qualifying_crash_is_enough():
    assert crashed_after_registration(20250101, [20240101, 20250601]) is True


def test_a_carrier_with_no_registration_date_never_counts():
    # Cannot establish the crash postdates registration, so it is excluded
    # rather than assumed.
    assert crashed_after_registration(None, [20250601]) is False


def test_months_between_is_fractional_so_short_exposure_is_not_rounded_away():
    # CORRECTED after implementation: this originally asserted `== 6.0`, which
    # is arithmetically impossible under the implementation below — 181 days /
    # 30.4375 is 5.947. A tolerance states the intent and still fails if the
    # function returns days, weeks, or divides by 365.
    assert 5.8 < months_between(20250101, 20250701) < 6.1
    assert round(months_between(20250101, 20250116), 1) == 0.5


def test_rate_of_an_empty_band_is_none_not_zero():
    # None means "no carriers in this band"; 0.0 means "carriers, none crashed".
    # Printing 0.0% for an empty band invents a measurement.
    assert rate(0, 0) is None
    assert rate(0, 10) == 0.0


def test_rate_is_a_proportion():
    assert rate(3, 12) == 0.25
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: FAIL — `ImportError: cannot import name 'crashed_after_registration'`

- [ ] **Step 3: Write the implementation**

Append to `utils/crash_lift.py`:

```python
from datetime import date


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add utils/crash_lift.py tests/test_crash_lift.py
git commit -m "Add exposure and rate arithmetic for crash-outcome validation"
```

---

### Task 3: Direct standardization for the control comparison

**Files:**

- Modify: `utils/crash_lift.py`
- Test: `tests/test_crash_lift.py`

**Interfaces:**

- Consumes: `rate` from Task 2.
- Produces: `standardize(flagged_counts: dict, control_counts: dict) -> tuple[float | None, list]` where each dict maps a stratum key to `(carriers_with_crash, total_carriers)`.

- [ ] **Step 1: Write the failing tests**

```python
from utils.crash_lift import standardize


def test_standardized_rate_reweights_controls_to_the_flagged_mix():
    # Controls are 50/50 across strata; flagged are 90/10. Standardizing must
    # answer "what would the control rate be if controls had the flagged
    # population's mix", which is 0.9*0.10 + 0.1*0.50 = 0.14 — NOT the crude
    # control rate of 0.30. Getting this backwards is the whole reason the
    # comparison exists.
    flagged = {"a": (0, 90), "b": (0, 10)}
    control = {"a": (10, 100), "b": (50, 100)}
    standardized, skipped = standardize(flagged, control)
    assert round(standardized, 4) == 0.14
    assert skipped == []


def test_strata_with_no_controls_are_reported_not_silently_dropped():
    # Dropping them quietly redefines the comparison population, which would
    # make the lift describe a different set of carriers than the headline.
    flagged = {"a": (0, 50), "orphan": (0, 50)}
    control = {"a": (10, 100)}
    standardized, skipped = standardize(flagged, control)
    assert standardized == 0.10
    assert skipped == ["orphan"]


def test_no_overlapping_strata_gives_none_rather_than_zero():
    standardized, skipped = standardize({"a": (0, 10)}, {"b": (5, 10)})
    assert standardized is None
    assert skipped == ["a"]


def test_empty_flagged_population_gives_none():
    assert standardize({}, {"a": (5, 10)}) == (None, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: FAIL — `ImportError: cannot import name 'standardize'`

- [ ] **Step 3: Write the implementation**

Append to `utils/crash_lift.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add utils/crash_lift.py tests/test_crash_lift.py
git commit -m "Add direct standardization for the crash-lift control group"
```

---

### Task 4: Elasticsearch readers

**Files:**

- Create: `scripts/measure_crash_lift.py`
- Test: `tests/test_crash_lift.py` (integration test, skipped when cluster unreachable)

**Interfaces:**

- Consumes: everything from `utils.crash_lift`.
- Produces: `successor_scores(client, pairs_index) -> dict[str, float]`, `crash_dates(client, crashes_index, dot_numbers) -> dict[str, list[int]]`, `carrier_attributes(client, carriers_index, dot_numbers) -> dict[str, dict]`, `crash_window(client, crashes_index) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing integration test**

```python
import pytest
from elasticsearch import Elasticsearch


@pytest.fixture
def live_client():
    """Real cluster, skipped when unreachable.

    The pure functions above assert what we compute; only this asserts the
    queries retrieve what we think. The dot_number type mismatch between
    indexes (long on crashes, keyword elsewhere) is invisible to a unit test.
    """
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=120
    )
    try:
        reachable = client.ping()
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("Elasticsearch is not reachable on localhost:9200")
    return client


def test_crash_dates_join_across_the_dot_number_type_mismatch(live_client):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from measure_crash_lift import crash_dates, successor_scores

    scores = successor_scores(live_client, "chameleon-candidates-000001", limit=500)
    assert scores, "no successors read from the candidates index"

    found = crash_dates(live_client, "crashes-000001", list(scores))
    # Not every successor crashed, but across 500 the intersection must not be
    # empty — an empty result here is the signature of the keyword/long
    # mismatch silently intersecting to nothing.
    assert found, "crash join returned nothing; check dot_number str/int coercion"
    for dates in found.values():
        assert all(isinstance(d, int) for d in dates)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -k join -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'measure_crash_lift'`

- [ ] **Step 3: Write the implementation**

```python
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
import sys
from pathlib import Path

# Runs as `.venv/bin/python scripts/measure_crash_lift.py`, which puts scripts/
# on sys.path rather than the repo root, so utils.crash_lift is unimportable
# without this. Same fix as measure_address_analyzers.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch  # noqa: E402

from utils.crash_lift import (  # noqa: E402
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -k join -v`
Expected: PASS (or SKIP if no cluster — start it with `docker compose -f docker/compose.yml up -d`)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add scripts/measure_crash_lift.py tests/test_crash_lift.py
git commit -m "Add Elasticsearch readers for crash-outcome validation"
```

---

### Task 5: Report assembly, placebo, and CLI

**Files:**

- Modify: `scripts/measure_crash_lift.py`

**Interfaces:**

- Consumes: all readers from Task 4 and all arithmetic from Tasks 1-3.
- Produces: `main() -> int`, invoked via `if __name__ == "__main__"`.

- [ ] **Step 1: Write the report assembly**

Append to `scripts/measure_crash_lift.py`:

```python
import random  # noqa: E402


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", default="chameleon-candidates-000001")
    parser.add_argument("--carriers-index", default="carriers-000001")
    parser.add_argument("--crashes-index", default="crashes-000001")
    parser.add_argument("--seed", type=int, default=42, help="placebo permutation only")
    args = parser.parse_args()

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
            len(rows), len(dots), len(restricted),
            1 - (len(restricted) / len(rows)) if rows else 0,
        )
    )

    band_table(restricted, "RESTRICTED COHORT (headline; comparable to GAO's 18% / 6%)")
    band_table(rows, "FULL SET (companion; unequal exposure, do not quote as the headline)")

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

    # Permuted scores over the SAME carriers. Must come out flat: without this
    # a monotonic trend cannot be told apart from an artifact of where the bin
    # edges fell, and bin edges are the easiest thing in this analysis to fool
    # yourself with.
    placebo = [dict(r) for r in restricted]
    shuffled = [r["score"] for r in placebo]
    random.Random(args.seed).shuffle(shuffled)
    for row, score in zip(placebo, shuffled):
        row["band"] = band_for(score)
    band_table(placebo, "PLACEBO (permuted scores; MUST be flat or the banding is wrong)")

    flagged = stratum_counts(restricted)
    control = control_stratum_counts(client, args, flagged, window_start, window_end)
    standardized, skipped = standardize(flagged, control)

    flagged_crashed = sum(1 for r in restricted if r["crashed"])
    flagged_rate = rate(flagged_crashed, len(restricted))
    print("\nCONTROL COMPARISON (restricted cohort, directly standardized)")
    print("  flagged successors : {:.2%} of {:,}".format(flagged_rate or 0, len(restricted)))
    print(
        "  standardized control: {} of {:,} unflagged carriers".format(
            "n/a" if standardized is None else "{:.2%}".format(standardized), len(control_rows)
        )
    )
    if standardized:
        print("  lift: {:.2f}x  (GAO measured 18% vs 6%, 3.0x)".format((flagged_rate or 0) / standardized))
    if skipped:
        print("  strata with no controls, excluded from the weighted total: {}".format(len(skipped)))

    print(
        "\nNOTE: fleet size drives crashes through miles driven and is a matching "
        "stratum above. If the lift appears only in the raw proportion and not in "
        "the standardized comparison, it is confounded by size and must not be "
        "reported as evidence the score ranks risk."
    )
    return 0


def control_stratum_counts(client, args, flagged, window_start, window_end):
    """Crash counts per stratum for every carrier NOT in the pair set.

    Covers the whole unflagged population rather than a sample, because the
    spec's reason for choosing direct standardization was to use every control
    carrier. It also removes an entire class of wrong answer: selecting
    controls by paging an ordered field returns the OLDEST carriers, since
    FMCSA assigns DOT numbers chronologically, and old carriers crash more.
    That mistake was made once here and produced a confident 0.70x lift — the
    score appearing to anti-predict crashes, when what was being measured was
    company age.

    Both sides come from subtraction, so nothing has to be sampled:

        control_total   = all_carriers_total   - flagged_total
        control_crashed = all_crashed_total    - flagged_crashed

    The crashed side is cheap despite covering every carrier: only carriers
    appearing in the crash file can contribute, and there are ~122,483 of
    those against 2,085,534 carriers.
    """


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Add the missing import**

`SCORE_BANDS` is referenced by `band_table`. Add it to the `utils.crash_lift` import list at the top of the file.

- [ ] **Step 3: Verify the script runs end to end**

Run: `.venv/bin/python scripts/measure_crash_lift.py`
Expected: prints the crash window, counts, four band tables and the control comparison. No traceback.

- [ ] **Step 4: Verify the placebo is flat**

Read the PLACEBO table. Rates across bands must not trend. **If the placebo trends, stop — the banding is wrong and no result from this run is publishable.** Fix before continuing.

- [ ] **Step 5: Lint, test, commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -q
git add scripts/measure_crash_lift.py
git commit -m "Add band tables, placebo and standardized control to crash-lift script"
```

---

### Task 5B: Split the dose–response by registration recency

Added after Task 5 returned a null result (lift 1.10x, dose–response flat).
The headline restricted cohort excludes every successor registered inside the
crash window — which is the population most likely to contain an _active_
chameleon. A carrier that re-registered in 2015 and has run quietly since is
not what this project hunts. If the score carries signal only for fresh
registrations, the Task 5 headline is structurally unable to see it.

Exposure normalization is what makes this askable: crashes per 1,000 observed
months puts a carrier with four months of history on the same scale as one
with twenty-four.

**Files:**

- Modify: `scripts/measure_crash_lift.py`
- Test: `tests/test_crash_lift.py`

**Interfaces:**

- Consumes: `build_rows`, `months_between`, `SCORE_BANDS`, `band_for` from Tasks 1-5.
- Produces: `recency_cohort(add_yyyymmdd, window_end) -> str` in `utils/crash_lift.py`, and `recency_table(rows, window_end)` in the script.

- [ ] **Step 1: Write the failing test**

Cohort edges are FIXED HERE, before the run, for the same reason the score
bands were — edges chosen after seeing the outcome are how this analysis fools
its author.

```python
from utils.crash_lift import recency_cohort


def test_recency_cohorts_are_measured_back_from_the_crash_window_end():
    # Boundaries are months before the newest crash in the data, not before
    # today, so the cohorts stay stable as the fetch window rolls forward.
    assert recency_cohort(20260101, 20260301) == "under-1y"
    assert recency_cohort(20240101, 20260301) == "1-3y"
    assert recency_cohort(20200101, 20260301) == "3y-plus"


def test_recency_cohort_boundaries_are_half_open():
    # Exactly 12 months back belongs to the older cohort, so no carrier lands
    # in two columns and the columns sum to the population.
    assert recency_cohort(20250301, 20260301) == "1-3y"
    assert recency_cohort(20230301, 20260301) == "3y-plus"


def test_recency_cohort_is_none_without_a_registration_date():
    # Same rule as everywhere else here: absent input is excluded, never
    # guessed into a bucket.
    assert recency_cohort(None, 20260301) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -k recency -v`
Expected: FAIL — `ImportError: cannot import name 'recency_cohort'`

- [ ] **Step 3: Implement `recency_cohort` in `utils/crash_lift.py`**

```python
RECENCY_COHORTS = [(12, "under-1y"), (36, "1-3y")]


def recency_cohort(add_yyyymmdd, window_end_yyyymmdd):
    """How recently a carrier registered, measured back from the newest crash.

    Exists because the crash-lift headline restricts to carriers registered
    before the crash window, which structurally excludes the freshest
    registrations — the ones an active chameleon would be. Splitting the score
    bands by this lets a signal confined to recent registrations show up
    instead of being averaged away against a decade of quiet carriers.

    Measured back from the window end rather than from today so the cohorts do
    not shift under a run simply because the fetch window rolled forward.
    Boundaries are half-open, so the columns partition the population and a
    carrier cannot appear in two.
    """
    if add_yyyymmdd is None:
        return None
    age = months_between(add_yyyymmdd, window_end_yyyymmdd)
    for limit, label in RECENCY_COHORTS:
        if age < limit:
            return label
    return "3y-plus"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crash_lift.py -k recency -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the table to the script**

Print crashes per 1,000 exposure-months per (band, cohort) over the FULL row
set — not the restricted cohort, since including recent registrations is the
entire point. **Every cell prints its carrier count**, because a rate over 40
carriers and a rate over 40,000 are different claims and the recent cohorts
will be the small ones.

```python
def recency_table(rows, window_end):
    """Dose-response split by how recently the successor registered.

    Exposure-normalized rather than a raw proportion because the recent
    cohorts have had less time to crash by construction; comparing raw
    proportions across them would measure exposure, not risk.
    """
    cohorts = ["under-1y", "1-3y", "3y-plus"]
    print("\nDOSE-RESPONSE BY REGISTRATION RECENCY (crashes per 1,000 exposure-months, n in parens)")
    print("  {:<12} {:>22} {:>22} {:>22}".format("band", *cohorts))
    for _, _, label in SCORE_BANDS:
        cells = []
        for cohort in cohorts:
            subset = [
                r for r in rows
                if r["band"] == label and recency_cohort(r["add"], window_end) == cohort
            ]
            exposure = sum(r["exposure"] for r in subset)
            crashed = sum(1 for r in subset if r["crashed"])
            cells.append(
                "n/a ({})".format(len(subset)) if exposure <= 0
                else "{:.2f} ({:,})".format(1000 * crashed / exposure, len(subset))
            )
        print("  {:<12} {:>22} {:>22} {:>22}".format(label, *cells))
```

`build_rows` must carry `add` on each row for this to work; add it there if absent.

- [ ] **Step 6: Call it from `main` and run for real**

Call `recency_table(rows, window_end)` after the existing exposure-normalized
table. Then run `.venv/bin/python scripts/measure_crash_lift.py` and report the
table.

**Report what it shows either way.** If the recent cohort trends with score
while the overall result does not, that is the finding. If it is flat too, the
null result stands and is stronger for having been checked where the signal
was most likely to hide.

- [ ] **Step 7: Lint, test, commit**

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/ -q
git add utils/crash_lift.py scripts/measure_crash_lift.py tests/test_crash_lift.py
git commit -m "Split the crash-lift dose-response by registration recency"
```

---

### Task 6: Persist each run's result to Elasticsearch

Printing to stdout alone would repeat the exact failure this session spent hours
undoing. The "roughly 195 pairs" figure became unreproducible because the run
behind it was gone; the controlled before/after comparison was only possible
because three previous sweeps happened to still exist as indexes. A validation
result that lives only in a terminal is a figure with no run behind it.

Indexing also makes each run self-describing: it carries the fingerprint of the
carriers index it measured, so a result can be tied to the token universe that
produced it rather than matched up by timestamp afterwards.

**Files:**

- Create: `DOT-Commercial/configuration/chameleon-validation/index-config.json`
- Create: `DOT-Commercial/configuration/chameleon-validation/index-mappings.json`
- Modify: `DOT-Commercial/configuration.json` (register the step)
- Modify: `scripts/measure_crash_lift.py`

**Interfaces:**

- Consumes: the row structures from Task 5.
- Produces: `write_results(client, index, documents) -> int`, and a `--write/--no-write` CLI flag defaulting to write.

- [ ] **Step 1: Create the index config**

`DOT-Commercial/configuration/chameleon-validation/index-config.json`:

```json
{
  "index": "chameleon-validation-{now/d}-000001",
  "alias": "chameleon-validation-000001"
}
```

Date-stamped behind an alias like every other step, so each run is retained for
comparison while the alias always names the latest. The alias swap is atomic as
of the fix merged earlier today, so a re-run will not leave two result sets
stacked behind one name.

- [ ] **Step 2: Pin the mappings**

Every field is pinned rather than left to dynamic inference. `tow_away` in Task 7
is what dynamic inference costs, and a results index whose `rate` field guessed
`long` from a first value of `0` would silently truncate every subsequent rate to
an integer.

`DOT-Commercial/configuration/chameleon-validation/index-mappings.json`:

```json
{
  "mappings": {
    "properties": {
      "run_id": { "type": "keyword" },
      "generated_at": { "type": "date" },
      "row_type": { "type": "keyword" },
      "view": { "type": "keyword" },
      "band": { "type": "keyword" },
      "recency_cohort": { "type": "keyword" },
      "carriers": { "type": "long" },
      "crashed": { "type": "long" },
      "rate": { "type": "double" },
      "crashes_per_1000_months": { "type": "double" },
      "flagged_rate": { "type": "double" },
      "standardized_control_rate": { "type": "double" },
      "lift": { "type": "double" },
      "strata_without_controls": { "type": "long" },
      "placebo_is_flat": { "type": "boolean" },
      "source": {
        "properties": {
          "pairs_index": { "type": "keyword" },
          "carriers_index": { "type": "keyword" },
          "crashes_index": { "type": "keyword" },
          "analysis_fingerprint": { "type": "keyword" },
          "crash_window_start": { "type": "long" },
          "crash_window_end": { "type": "long" },
          "distinct_successors": { "type": "long" },
          "restricted_cohort": { "type": "long" }
        }
      }
    }
  }
}
```

- [ ] **Step 3: Register the step**

Add to the `steps` array in `DOT-Commercial/configuration.json`, after
`chameleon-detection`:

```json
{
  "name": "chameleon-validation",
  "phases": ["index-create", "index-map"]
}
```

Only the two index phases — the script writes the documents, not a populate
phase, because the input is a computation over three indexes rather than a CSV.

- [ ] **Step 4: Write the persistence function**

Append to `scripts/measure_crash_lift.py`:

```python
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from elasticsearch.helpers import bulk  # noqa: E402


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
```

- [ ] **Step 5: Emit the documents from `main`**

In `main`, after the control comparison is computed, build and write the
documents. Give every row the same `run_id` and `source` block so one run is
retrievable as a unit:

```python
    run_id = uuid.uuid4().hex
    generated_at = datetime.now(timezone.utc).isoformat()
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

    documents = []
    for view, view_rows in (("restricted", restricted), ("full", rows), ("placebo", placebo)):
        for _, _, label in SCORE_BANDS:
            band_rows = [r for r in view_rows if r["band"] == label]
            crashed = sum(1 for r in band_rows if r["crashed"])
            exposure = sum(r["exposure"] for r in band_rows)
            documents.append(
                {
                    "run_id": run_id,
                    "generated_at": generated_at,
                    "row_type": "band",
                    "view": view,
                    "band": label,
                    "carriers": len(band_rows),
                    "crashed": crashed,
                    "rate": rate(crashed, len(band_rows)),
                    "crashes_per_1000_months": (
                        None if exposure <= 0 else 1000 * crashed / exposure
                    ),
                    "source": source,
                }
            )
    documents.append(
        {
            "run_id": run_id,
            "generated_at": generated_at,
            "row_type": "summary",
            "flagged_rate": flagged_rate,
            "standardized_control_rate": standardized,
            "lift": None if not standardized else (flagged_rate or 0) / standardized,
            "strata_without_controls": len(skipped),
            "source": source,
        }
    )

    if args.write:
        written = write_results(client, args.results_alias, documents)
        print("\nwrote {} result rows to {} as run_id {}".format(written, args.results_alias, run_id))
```

Add the flags alongside the existing ones:

```python
    parser.add_argument("--results-alias", default="chameleon-validation-000001")
    parser.add_argument("--no-write", dest="write", action="store_false")
    parser.set_defaults(write=True)
```

`placebo_is_flat` is left unset by the script deliberately — it is a judgment
made by reading the placebo table, and having code assert its own placebo passed
would defeat the purpose of having one.

- [ ] **Step 6: Create the index and run**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-validation
.venv/bin/python scripts/measure_crash_lift.py
```

- [ ] **Step 7: Verify the run is retrievable as a unit**

```bash
curl -s "localhost:9200/chameleon-validation-000001/_search?size=3" \
  -H 'Content-Type: application/json' \
  -d '{"query":{"term":{"row_type":"summary"}},"sort":[{"generated_at":"desc"}]}'
```

Expected: one summary document carrying `lift`, and a `source.analysis_fingerprint`
equal to the fingerprint stamped on the carriers index it measured.

- [ ] **Step 8: Commit**

```bash
.venv/bin/python -m ruff check .
git add DOT-Commercial/configuration/chameleon-validation/ DOT-Commercial/configuration.json scripts/measure_crash_lift.py
git commit -m "Persist crash-lift results to a date-stamped validation index"
```

---

### Task 7: Run it and record the result

**Files:**

- Modify: `DOT-Commercial/README.md` (the `entity-match` calibration open item)

- [ ] **Step 1: Run the full measurement and capture output**

```bash
.venv/bin/python scripts/measure_crash_lift.py | tee /tmp/crash-lift.txt
```

- [ ] **Step 2: Write the result into the open item**

Add a paragraph to the `entity-match` calibration open item. Include, verbatim from the run: the crash window, the restricted-cohort denominator, the per-band rates, the standardized control rate and lift, and the placebo verdict. State the command that produced it.

**Record the result whichever way it comes out.** A flat curve means the shipped weighting does not rank real risk — more actionable than any count currently committed, and the reason the next open item (name triple-weighting) matters. Do not re-cut bands to find a trend.

Anonymize per the repo rule: no carrier names, DOT numbers, addresses, phones or emails. Aggregate counts and rates only.

- [ ] **Step 3: Verify no flagged entity is named**

```bash
grep -nE '\b[0-9]{6,8}\b' DOT-Commercial/README.md | grep -v "2,085,534\|421,846\|333,122"
```

Expected: no DOT-number-shaped values introduced by this change.

- [ ] **Step 4: Commit**

```bash
git add DOT-Commercial/README.md
git commit -m "Record measured crash-outcome lift for the chameleon score"
```

---

### Task 8 (independent): Pin `tow_away` as keyword

Not required by the measurement — nothing above reads `tow_away`. Included because the field is a live instance of this repo's recurring text/keyword defect, and it is a one-line fix. Drop this task freely.

Note that Task 6 avoids this class of defect on the results index by pinning every field rather than letting a first value decide the type.

**Files:**

- Modify: `DOT-Commercial/configuration/crashes/index-mappings.json`

- [ ] **Step 1: Pin the field**

`crashes/index-mappings.json` currently pins only `dot_number`. Add `tow_away` alongside it:

```json
"tow_away": { "type": "keyword" }
```

`keyword` rather than `boolean`: the source has three states (`Y` at 309,628, `N` at 23,492, absent at 2), and a boolean would force a Y/N transform in the ingest pipeline, changing data on the way in rather than preserving the source verbatim. `keyword` removes the trap outright — `{"term": {"tow_away": "Y"}}` then matches — and mirrors the fix the `insp_carrier_state_id` open item prescribes.

- [ ] **Step 2: Reload crashes so the mapping takes effect**

Mappings are immutable on a live index, so the pin only applies to a newly created one:

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=crashes
```

- [ ] **Step 3: Verify the trap is gone**

```bash
curl -s "localhost:9200/crashes-000001/_count" -H 'Content-Type: application/json' \
  -d '{"query":{"term":{"tow_away":"Y"}}}'
```

Expected: ~309,628, not 0.

- [ ] **Step 4: Commit**

```bash
git add DOT-Commercial/configuration/crashes/index-mappings.json
git commit -m "Pin crashes.tow_away as keyword so an exact-value query works"
```

---

## Self-Review

**Spec coverage.** Problem/external reference → Task 6's write-up. Unit of analysis (max per successor) → Task 4 `successor_scores`. Outcome variable (presence in file) → Task 4 `crash_dates` + Task 2 `crashed_after_registration`. Field-type traps → Task 4 docstrings and the Task 4 integration test; `tow_away` → Task 7. Exposure (both views) → Task 2 `months_between`, Task 5 restricted/full/normalized tables. Dose–response and fixed bin edges → Task 1 `SCORE_BANDS`. Matched control by direct standardization → Task 3. Placebo → Task 5 Step 4. Fleet-size confounder → Task 1 `fleet_size_band`, Task 3, and the closing note in Task 5. Testing → Tasks 1-4. Persistence and run traceability → Task 6. Documentation → Task 7. Risks (null result recorded, edges frozen) → Task 7 Step 2.

**Added beyond the spec.** Task 6 (persisting results to a date-stamped index behind an alias) is not in the design document. It was added because a result that exists only in a terminal reproduces the failure the spec's own Documentation section warns about, and because carrying the source index's `analysis_fingerprint` on each result row is the traceability the spec's Problem section identifies as missing. Fold it back into the spec if the spec is revised.

**Known gap, deliberate.** The spec's testing section lists "a successor appearing in several pairs must count once at its highest score" as a unit test. It is covered by the composite aggregation's `max` sub-aggregation and asserted in the Task 4 integration test rather than as a pure unit test, because the reduction happens in Elasticsearch. A pure `reduce_max_scores` helper would have to be dead code to be unit-testable, which is worse.

**Type consistency.** `to_yyyymmdd` returns `int | None`; `crashed_after_registration` and `months_between` both consume that integer form. `band_for` returns the label strings in `SCORE_BANDS`, which `band_table` iterates. `standardize` consumes the `{stratum: (crashed, total)}` shape that `stratum_counts` produces. DOT numbers are `str` at every boundary.
