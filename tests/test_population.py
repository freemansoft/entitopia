"""The clause menu is closed, and each kind builds one specific query shape.

The population decides what everything downstream scores, so a defect here does
not fail — it quietly changes what the sweep is about. That is why an
unrecognized clause kind raises rather than contributing nothing, and why a
selector cycle is caught rather than recursing until the stack gives out.

nested-exists is a single primitive rather than three composable ones because
flattening is a known defect: under an object mapping, a record with an ACTIVE
2015 order and an INACTIVE 2022 order satisfied status=ACTIVE and
date >= 2020 from two different orders and was swept even though no single
order qualified. Making nesting the only available shape means no project can
reintroduce that by writing config that looks reasonable.
"""

import json
import re
from types import SimpleNamespace

import pytest

from matching.population import PopulationSelector


def _ns(value):
    return json.loads(json.dumps(value), object_hook=lambda d: SimpleNamespace(**d))


def selector(**population):
    return PopulationSelector(es=None, source_index="idx", config=_ns(population))


def test_nested_exists_puts_every_filter_inside_one_element():
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={
            "s": {
                "nested-exists": {
                    "path": "orders",
                    "require": "closed_date",
                    "terms": {"status": ["ACTIVE"]},
                    "range": {"closed_date": {"gte": "2020-01-01"}},
                }
            }
        },
    ).build_query()
    assert query["nested"]["path"] == "orders"
    must = query["nested"]["query"]["bool"]["must"]
    assert {"exists": {"field": "orders.closed_date"}} in must
    assert {"terms": {"orders.status": ["ACTIVE"]}} in must
    assert {"range": {"orders.closed_date": {"gte": "2020-01-01"}}} in must


def test_optional_filters_are_omitted_when_unset():
    # terms and range are operator knobs for tightening a sweep, not fields
    # every deployment sets; an absent one must not become terms: [].
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={"s": {"nested-exists": {"path": "orders", "require": "closed_date"}}},
    ).build_query()
    assert query["nested"]["query"]["bool"]["must"] == [
        {"exists": {"field": "orders.closed_date"}}
    ]


def test_term_clause_is_not_nested():
    query = selector(
        mode="lifecycle",
        selector="s",
        selectors={"s": {"term": {"history.disposition": "REVOKED"}}},
    ).build_query()
    assert query == {"bool": {"must": [{"term": {"history.disposition": "REVOKED"}}]}}


def test_all_intersects_named_selectors():
    query = selector(
        mode="lifecycle",
        selector="both",
        selectors={
            "a": {"term": {"x": "1"}},
            "b": {"term": {"y": "2"}},
            "both": {"all": ["a", "b"]},
        },
    ).build_query()
    assert len(query["bool"]["must"]) == 2


def test_any_unions_named_selectors():
    query = selector(
        mode="lifecycle",
        selector="either",
        selectors={
            "a": {"term": {"x": "1"}},
            "b": {"term": {"y": "2"}},
            "either": {"any": ["a", "b"]},
        },
    ).build_query()
    assert query["bool"]["minimum_should_match"] == 1
    assert len(query["bool"]["should"]) == 2


def test_all_entities_mode_has_no_query():
    # None rather than {"match_all": {}} so a caller can tell "sweep
    # everything" from "a filter that happened to select everything" — the
    # same result, very different intents.
    assert selector(mode="all-entities").build_query() is None


def test_all_entities_ignores_a_configured_selector():
    # A project switching to duplicate detection must not keep silently
    # filtering by a selector left behind in its config.
    assert (
        selector(
            mode="all-entities",
            selector="s",
            selectors={"s": {"term": {"x": "1"}}},
        ).build_query()
        is None
    )


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="unknown population mode"):
        selector(mode="whatever")


def test_lifecycle_mode_without_a_selector_is_refused():
    with pytest.raises(ValueError, match=re.escape("population.selector is required")):
        selector(mode="lifecycle", selectors={"s": {"term": {"x": "1"}}}).build_query()


def test_unknown_selector_name_is_refused():
    with pytest.raises(ValueError, match="unknown selector"):
        selector(
            mode="lifecycle", selector="nope", selectors={"s": {"term": {"x": "1"}}}
        ).build_query()


def test_unknown_clause_kind_is_refused():
    # The menu is closed on purpose: an unrecognized kind must fail loudly
    # rather than contribute nothing and silently change the population.
    with pytest.raises(ValueError, match="unknown clause kind"):
        selector(
            mode="lifecycle", selector="s", selectors={"s": {"wildcard": {"x": "*"}}}
        ).build_query()


def test_a_selector_declaring_two_kinds_is_refused():
    # Ambiguous rather than wrong, and picking one silently would change the
    # population without anything reporting it.
    with pytest.raises(ValueError, match="exactly one clause kind"):
        selector(
            mode="lifecycle",
            selector="s",
            selectors={"s": {"term": {"x": "1"}, "all": ["s"]}},
        ).build_query()


def test_a_selector_cycle_is_refused():
    with pytest.raises(ValueError, match="cycle"):
        selector(
            mode="lifecycle",
            selector="a",
            selectors={"a": {"all": ["b"]}, "b": {"all": ["a"]}},
        ).build_query()


def test_sort_field_is_read_from_config():
    # Paging under a point-in-time needs a stable total order, and which field
    # provides it is per-project — it was a dot_number literal before.
    assert selector(mode="all-entities", sort_field="Facility ID").sort_field == (
        "Facility ID"
    )
