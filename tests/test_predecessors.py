"""Predecessor selection: which carriers the sweep calls "shut down".

The selector decides the population everything downstream scores, so an error
here does not fail — it quietly changes what the whole sweep is about. These
run without a cluster; build_query() is a pure function of config.
"""

from types import SimpleNamespace

import pytest

from matching.predecessors import PredecessorSelector


def selector(**kwargs):
    config = SimpleNamespace(**kwargs)
    return PredecessorSelector(es=None, source_index="carriers-000001", config=config)


def test_out_of_service_clause_is_nested_on_the_order_path():
    # An object mapping lets status and oos_date match from two different
    # array elements, so a carrier with an ACTIVE 2015 order and an INACTIVE
    # 2022 order is swept even though no single order matches both filters.
    query = selector(selector="out-of-service", oos_status=["ACTIVE"],
                     oos_date_from="2020-01-01").build_query()
    assert "nested" in query
    assert query["nested"]["path"] == "out_of_service_orders"


def test_nested_clause_puts_every_filter_inside_one_order():
    query = selector(selector="out-of-service", oos_status=["ACTIVE"],
                     oos_date_from="2020-01-01").build_query()
    must = query["nested"]["query"]["bool"]["must"]
    assert {"terms": {"out_of_service_orders.status": ["ACTIVE"]}} in must
    assert {"range": {"out_of_service_orders.oos_date": {"gte": "2020-01-01"}}} in must


def test_optional_filters_are_omitted_when_unset():
    # status and date-from are operator knobs for tightening the sweep, not
    # fields every deployment sets; an empty list must not become terms: [].
    query = selector(selector="out-of-service").build_query()
    must = query["nested"]["query"]["bool"]["must"]
    assert must == [{"exists": {"field": "out_of_service_orders.oos_date"}}]


def test_revoked_clause_is_not_nested():
    # auth_history stays an object mapping; only out_of_service_orders changed.
    query = selector(selector="revoked-authority").build_query()
    assert "nested" not in query


def test_both_selector_intersects_the_nested_and_revoked_clauses():
    query = selector(selector="both").build_query()
    clauses = query["bool"]["must"]
    assert any("nested" in c for c in clauses)
    assert len(clauses) == 2


def test_either_selector_unions_them():
    query = selector(selector="either").build_query()
    assert query["bool"]["minimum_should_match"] == 1
    assert len(query["bool"]["should"]) == 2


def test_unknown_selector_is_refused():
    with pytest.raises(ValueError, match="unknown selector"):
        selector(selector="whatever")


def test_oos_path_defaults_to_out_of_service_orders():
    # Existing DOT-Commercial config sets nothing here, so this default is
    # what every current deployment actually runs on.
    query = selector(selector="out-of-service").build_query()
    assert query["nested"]["path"] == "out_of_service_orders"


def test_oos_path_is_configurable():
    # matching/ is framework code shared by every project (see the top-level
    # README's open item 6), so the array field name a future project's
    # "shut down" concept lives under cannot be a literal in this module.
    query = selector(selector="out-of-service", oos_path="shutdown_orders").build_query()
    assert query["nested"]["path"] == "shutdown_orders"
    must = query["nested"]["query"]["bool"]["must"]
    assert {"exists": {"field": "shutdown_orders.oos_date"}} in must
