"""What the config claims about the index, checked against the index.

The highest-value check in the whole validator lives here. This repo shipped
three inert analyzers because the mappings named columns the source had since
renamed: Elasticsearch treats a mapping for a nonexistent field as inert and
dynamic-maps the real field as plain text, so the analyzer never applies, no
error is raised, and the only symptom is scores that are quietly worse. A
signal reading `legal_name.phonetic` from an index that never declared that
subfield gets an empty token set, which the signal reads as "not evaluable"
and drops -- indistinguishable from a record with no name.

These run against a fake client returning a canned mapping, so no cluster is
needed. The mapping shape is copied from a live carriers index: analyzed
subfields under `fields`, nested document paths under `properties`.
"""

import pytest

from utils import config_liveness

_MAPPING = {
    "carriers-2026.08.13-000001": {
        "mappings": {
            "properties": {
                "dot_number": {"type": "keyword"},
                "add_date": {"type": "date"},
                "legal_name": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword"},
                        "clean": {"type": "text"},
                        "phonetic": {"type": "text"},
                    },
                },
                "telephone": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword"}, "clean": {"type": "text"}},
                },
                "out_of_service_orders": {
                    "type": "nested",
                    "properties": {
                        "oos_date": {"type": "keyword"},
                        "status": {"type": "keyword"},
                    },
                },
            }
        }
    }
}


class _FakeIndices:
    def __init__(self, mapping, calls):
        self._mapping = mapping
        self._calls = calls

    def get_mapping(self, index=None):
        self._calls.append(index)
        return self._mapping


class _FakeES:
    """Minimal stand-in recording how many times the mapping was fetched."""

    def __init__(self, mapping=None):
        self.calls = []
        self.indices = _FakeIndices(mapping if mapping is not None else _MAPPING, self.calls)


def _config(**overrides):
    base = {
        "source_index": "carriers-000001",
        "entity": {"key": "dot_number", "summary_fields": ["legal_name"]},
        "lifecycle": {
            "shutdown_date": "out_of_service_orders.oos_date",
            "registration_date": "add_date",
        },
        "population": {"mode": "lifecycle", "sort_field": "dot_number"},
        "signals": [
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["legal_name"],
                "subfield": "phonetic",
            }
        ],
    }
    base.update(overrides)
    return base


def test_a_config_matching_the_mapping_reports_nothing():
    assert config_liveness.check(_FakeES(), _config(), "test.json") == []


def test_a_signal_field_absent_from_the_mapping_is_reported():
    raw = _config(
        signals=[
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["trading_name"],
                "subfield": "phonetic",
            }
        ]
    )
    assert any("trading_name" in e for e in config_liveness.check(_FakeES(), raw, "t"))


def test_a_subfield_the_mapping_never_declared_is_reported():
    # The highest-value check: an undeclared subfield yields an empty token set,
    # which a signal reads as "not evaluable" and drops. Nothing errors, and the
    # signal simply stops contributing.
    raw = _config(
        signals=[
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["legal_name"],
                "subfield": "phonetic_bm",
            }
        ]
    )
    errors = config_liveness.check(_FakeES(), raw, "t")
    assert any("phonetic_bm" in e for e in errors)


def test_a_nested_document_path_is_resolved():
    # out_of_service_orders.oos_date lives under `properties`, not `fields`;
    # conflating the two would report every nested path as missing.
    assert config_liveness.check(_FakeES(), _config(), "test.json") == []


def test_a_missing_nested_path_is_reported():
    raw = _config(
        lifecycle={
            "shutdown_date": "out_of_service_orders.closed_on",
            "registration_date": "add_date",
        }
    )
    assert any("closed_on" in e for e in config_liveness.check(_FakeES(), raw, "t"))


def test_an_entity_key_absent_from_the_mapping_is_reported():
    raw = _config(entity={"key": "carrier_id"})
    assert any("carrier_id" in e for e in config_liveness.check(_FakeES(), raw, "t"))


def test_a_sort_field_absent_from_the_mapping_is_reported():
    # Paging under a point-in-time sorts on this. A missing field fails the
    # search outright -- hours into a sweep, after the setup all succeeded.
    raw = _config(population={"mode": "lifecycle", "sort_field": "nope"})
    assert any("nope" in e for e in config_liveness.check(_FakeES(), raw, "t"))


def test_a_summary_field_absent_from_the_mapping_is_reported():
    raw = _config(entity={"key": "dot_number", "summary_fields": ["legal_name", "nope"]})
    assert any("nope" in e for e in config_liveness.check(_FakeES(), raw, "t"))


def test_the_mapping_is_fetched_once_not_once_per_field():
    # A config with forty field references would otherwise make forty round
    # trips for information one call carries.
    es = _FakeES()
    config_liveness.check(es, _config(), "test.json")
    assert len(es.calls) == 1


def test_an_unreachable_index_is_reported_rather_than_raising():
    class _Exploding:
        class indices:
            @staticmethod
            def get_mapping(index=None):
                raise RuntimeError("index_not_found_exception")

    errors = config_liveness.check(_Exploding(), _config(), "test.json")
    assert errors
    assert any("carriers-000001" in e for e in errors)


def test_every_problem_is_reported_not_just_the_first():
    raw = _config(
        entity={"key": "nope1", "summary_fields": ["nope2"]},
        population={"mode": "lifecycle", "sort_field": "nope3"},
    )
    assert len(config_liveness.check(_FakeES(), raw, "t")) >= 3


@pytest.mark.parametrize("subfield", ["clean", "keyword"])
def test_declared_subfields_are_accepted(subfield):
    raw = _config(
        signals=[
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["telephone"],
                "subfield": subfield,
            }
        ]
    )
    assert config_liveness.check(_FakeES(), raw, "t") == []
