"""The candidate search must return the same candidates on every load of the same data.

Measured 2026-11-06: a full reload of unchanged FMCSA CSVs, swept with
unchanged scoring, lost 2,234 pairs and gained 1,793. 89% of predecessors
matched more than max_candidates (500) seed hits, and the query sorted on BM25
alone, so wherever the cutoff fell inside a block of equal scores (104 to 619
tied documents in the cases probed), Lucene's internal document order decided
which side of it a candidate landed on. That order follows segment layout,
which changes on every load. All 16 lost pairs scoring >= 0.60, the best at
0.751, sat in exactly such a tie at the 500th position.

Two fixes, both tested here by request shape, because the defect is the shape:
a fake that answered with whatever it was asked would pass either way.

- A total sort order: BM25, then registration closest to the predecessor's
  shutdown (what the temporal signal rewards), then the entity key.
- The scorer's gap window applied in the query, so the slots under the cap go
  to candidates the scorer could actually emit.
"""

import datetime
from types import SimpleNamespace

import pytest

from matching.candidates import CandidateFinder
from phase_providers.phase_entity_match import PhaseEntityMatch

_NAME_SIGNAL = SimpleNamespace(
    type="name-phonetic", weight=0.4, fields=["legal_name"], subfield="phonetic"
)
_LIFECYCLE = SimpleNamespace(
    shutdown_date="out_of_service_orders.oos_date", registration_date="add_date"
)
_WINDOW = SimpleNamespace(min_gap_days=-180, max_gap_days=365)
_PRED = {
    "_id": "pred",
    "_source": {"legal_name": "ZQX FREIGHT", "out_of_service_orders": [{"oos_date": "2026-01-10"}]},
}


class _RecordingES:
    """Captures the candidate search and answers with no hits."""

    def __init__(self):
        self.searches = []

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return {"hits": {"hits": []}}

    def mtermvectors(self, **kwargs):
        return {"docs": []}


def _finder(es, lifecycle=_LIFECYCLE, scoring=_WINDOW, key="dot_number"):
    return CandidateFinder(
        es=es,
        source_index="carriers-000001",
        candidates_config=SimpleNamespace(max_candidates=500, seed_signals=["name-phonetic"]),
        signal_configs=[_NAME_SIGNAL],
        entity_config=SimpleNamespace(key=key),
        lifecycle=lifecycle,
        scoring_config=scoring,
    )


def _search(**finder_kwargs):
    es = _RecordingES()
    _finder(es, **finder_kwargs).find(_PRED)
    (call,) = es.searches
    return call


def _window_clause(call):
    filters = call["query"]["bool"].get("filter", [])
    return filters[0]["bool"]["should"] if filters else None


def test_the_sort_ends_on_the_entity_key_so_every_tie_is_broken():
    # The defect itself: with no total order, ties at the cutoff resolve by
    # segment layout. The key is unique per entity, so ending on it makes the
    # order total for any project, lifecycle or not.
    sort = _search()["sort"]
    assert sort[0] == {"_score": {"order": "desc"}}
    assert sort[-1] == {"dot_number": {"order": "asc", "missing": "_last"}}


def test_the_key_tiebreak_holds_for_a_project_without_lifecycle_dates():
    # CMS hospital-duplicates declares no lifecycle; it has ties too.
    call = _search(lifecycle=None, scoring=SimpleNamespace(), key="Facility ID")
    assert call["sort"] == [
        {"_score": {"order": "desc"}},
        {"Facility ID": {"order": "asc", "missing": "_last"}},
    ]
    assert "filter" not in call["query"]["bool"]


def test_ties_prefer_the_registration_closest_to_the_shutdown():
    # Among equally similar names, the candidate registered nearest the
    # shutdown is the one the temporal signal would score highest, so it is
    # the one to keep when the cap forces a choice.
    proximity = _search()["sort"][1]["_script"]
    assert proximity["order"] == "asc"
    assert proximity["script"]["params"]["field"] == "add_date"
    shutdown = datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC)
    assert proximity["script"]["params"]["origin"] == int(shutdown.timestamp() * 1000)


def test_the_latest_shutdown_is_the_one_measured_from():
    # Same rule as the scorer's gap gate: a carrier with several orders is
    # judged on its most recent one.
    pred = {
        "_id": "pred",
        "_source": {
            "legal_name": "ZQX FREIGHT",
            "out_of_service_orders": [{"oos_date": "2023-05-01"}, {"oos_date": "10-JAN-26"}],
        },
    }
    es = _RecordingES()
    _finder(es).find(pred)
    window = _window_clause(es.searches[0])
    assert window[0]["range"]["add_date"]["gte"] == "2025-07-14||/d"


def test_the_gap_window_matches_the_scorers_inclusive_bounds():
    # The scorer rejects a pair whose gap is outside [min_gap_days,
    # max_gap_days]; the query must keep everything it would accept, so both
    # ends are inclusive and rounded to whole days.
    window = _window_clause(_search())
    shutdown = datetime.date(2026, 1, 10)
    assert window[0] == {
        "range": {
            "add_date": {
                "gte": (shutdown - datetime.timedelta(days=180)).isoformat() + "||/d",
                "lte": (shutdown + datetime.timedelta(days=365)).isoformat() + "||/d",
            }
        }
    }


def test_a_candidate_with_no_registration_date_is_still_retrieved():
    # The scorer reads a missing date as "not evaluable" and keeps the pair,
    # so the query must not drop it; dropping it would be a recall loss the
    # scorer never asked for.
    window = _window_clause(_search())
    assert window[1] == {"bool": {"must_not": {"exists": {"field": "add_date"}}}}


@pytest.mark.parametrize(
    "scoring",
    [SimpleNamespace(), SimpleNamespace(min_gap_days=None, max_gap_days=None)],
)
def test_no_window_is_applied_when_the_scorer_has_none(scoring):
    # A deployment that has not opted into the gap gate keeps its population.
    call = _search(scoring=scoring)
    assert "filter" not in call["query"]["bool"]


def test_one_sided_windows_bound_only_that_side():
    window = _window_clause(_search(scoring=SimpleNamespace(max_gap_days=365)))
    assert set(window[0]["range"]["add_date"]) == {"lte"}


def test_an_unparseable_shutdown_date_applies_no_window_and_no_proximity():
    # With no date to measure from, the scorer cannot judge the gap either,
    # so nothing is filtered; the key still makes the order total.
    pred = {"_id": "pred", "_source": {"legal_name": "ZQX FREIGHT", "out_of_service_orders": [{"oos_date": "garbage"}]}}
    es = _RecordingES()
    _finder(es).find(pred)
    (call,) = es.searches
    assert "filter" not in call["query"]["bool"]
    assert [next(iter(s)) for s in call["sort"]] == ["_score", "dot_number"]


def test_the_predecessor_is_still_excluded():
    call = _search()
    assert call["query"]["bool"]["must_not"] == [{"ids": {"values": ["pred"]}}]


def test_sort_fields_names_what_the_preflight_must_type_check():
    # The window is a range query and the proximity sort reads a date: on a
    # keyword field the range compares strings and silently drops candidates.
    assert _finder(_RecordingES()).sort_fields() == {"dot_number": "sortable", "add_date": "date"}
    assert _finder(_RecordingES(), lifecycle=None, scoring=SimpleNamespace()).sort_fields() == {
        "dot_number": "sortable"
    }


class _FieldCapsES:
    """Answers field_caps from a fixed {field: [types]} table."""

    def __init__(self, types):
        self.types = types

    def field_caps(self, index, fields):
        return {"fields": {f: {t: {"type": t} for t in self.types[f]} for f in fields if f in self.types}}


def _phase(types):
    return PhaseEntityMatch(es=_FieldCapsES(types), project="DOT-Commercial", one_step="x", project_config=None)


def test_preflight_passes_a_date_registration_and_keyword_key():
    phase = _phase({"dot_number": ["keyword"], "add_date": ["date"]})
    assert phase._check_sort_field_types("carriers-000001", {"dot_number": "sortable", "add_date": "date"})


def test_preflight_refuses_a_keyword_registration_date(caplog):
    # The silent case: a range over keyword compares strings and the sweep
    # would retrieve only candidates with no date at all.
    phase = _phase({"dot_number": ["keyword"], "add_date": ["keyword"]})
    assert not phase._check_sort_field_types("carriers-000001", {"dot_number": "sortable", "add_date": "date"})
    assert "add_date is mapped as keyword, needs date" in caplog.text


def test_preflight_refuses_a_text_key_and_an_unmapped_field(caplog):
    phase = _phase({"Facility ID": ["text"]})
    assert not phase._check_sort_field_types("h", {"Facility ID": "sortable", "add_date": "date"})
    assert "Facility ID is mapped as text" in caplog.text
    assert "add_date is not mapped" in caplog.text
