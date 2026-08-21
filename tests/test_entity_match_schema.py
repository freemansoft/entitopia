"""The analysis config, where a typo is most expensive.

Each case here corresponds to a specific way this config has gone wrong or
could: a renamed key silently ignored, a signal type that no longer exists, a
seed naming a signal nobody configured, a clause kind outside the closed menu.
The mutations are applied to the config that is actually shipped, so each test
asks "would this change have been caught" rather than testing a toy document
that resembles it.
"""

import copy
import json
from pathlib import Path

from matching.signals import SIGNAL_TYPES
from utils import config_schema

_SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "entity-match.schema.json"

_SHIPPED_PATH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)
_SHIPPED = json.loads(_SHIPPED_PATH.read_text())


def _signal(raw, signal_type):
    return next(s for s in raw["signals"] if s["type"] == signal_type)


def test_the_shipped_config_validates():
    # This file produced the pairs the compatibility gate certified. If it
    # fails, the schema is wrong.
    assert config_schema.validate_mapping("entity-match", _SHIPPED, "shipped") == []


def test_a_renamed_signal_key_is_rejected():
    # max_shared_carriers became max_shared_entities. Under a permissive schema
    # the old name is ignored and the limit falls back to DEFAULT_SHARED_LIMIT
    # with nothing reported -- this is the failure additionalProperties exists
    # to catch, and the reason the whole plan was worth doing.
    raw = copy.deepcopy(_SHIPPED)
    signal = _signal(raw, "shared-token")
    signal["max_shared_carriers"] = signal.pop("max_shared_entities")
    errors = config_schema.validate_mapping("entity-match", raw, "mutated")
    assert any("max_shared_carriers" in e for e in errors)


def test_a_deleted_signal_type_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    _signal(raw, "shared-token")["type"] = "vin-overlap"
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_a_temporal_signal_carrying_its_old_date_keys_is_rejected():
    # Those keys moved to the lifecycle block. Leaving them on the signal would
    # read as configuration that works while the signal ignored them entirely.
    raw = copy.deepcopy(_SHIPPED)
    _signal(raw, "temporal")["predecessor_date"] = "out_of_service_orders.oos_date"
    errors = config_schema.validate_mapping("entity-match", raw, "mutated")
    assert any("predecessor_date" in e for e in errors)


def test_an_address_signal_without_fuzzy_scale_is_rejected():
    """The schema and the code disagreed, and a second project paid for it.

    matching/signals.py reads config.fuzzy_scale with no default, so omitting
    it raises AttributeError on every scored pair. This schema listed the key
    as optional, so a CMS config passed all three validation tiers and then
    produced 532,529 scoring errors and zero pairs on its first sweep.

    Required rather than defaulted in code: fuzzy_scale sets what a fuzzy
    address match is worth against an exact one, so a silent default would
    change what every address score means without anyone choosing it.
    """
    raw = copy.deepcopy(_SHIPPED)
    del _signal(raw, "address")["fuzzy_scale"]
    errors = config_schema.validate_mapping("entity-match", raw, "mutated")
    assert any("fuzzy_scale" in e for e in errors)


def test_every_key_an_address_signal_reads_is_required_by_the_schema():
    """Guards the general shape of the defect above, not just the one key.

    A signal class reading a config key with no default makes that key
    required in fact; if the schema calls it optional, config validates and
    then crashes. This asserts the address variant's required list covers
    every attribute AddressSignal reads directly off its config.
    """
    schema = json.loads(_SCHEMA_PATH.read_text())
    address = next(
        clause
        for clause in schema["properties"]["signals"]["items"]["allOf"]
        if clause["if"]["properties"]["type"].get("const") == "address"
    )
    required = set(address["then"]["required"])
    # Read directly as self.config.X in AddressSignal.score, so each raises
    # AttributeError when absent rather than falling back.
    assert {"fields", "exact_subfield", "fuzzy_subfield", "fuzzy_scale"} <= required


def test_a_name_signal_without_a_subfield_is_rejected():
    # A name signal reads analyzed tokens; with no subfield there is nothing to
    # read and it would score every pair unevaluable.
    raw = copy.deepcopy(_SHIPPED)
    del _signal(raw, "name-token")["subfield"]
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_an_unknown_clause_kind_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["population"]["selectors"]["out-of-service"] = {"wildcard": {"x": "*"}}
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_a_selector_declaring_two_clause_kinds_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["population"]["selectors"]["both"] = {
        "all": ["out-of-service"],
        "any": ["revoked-authority"],
    }
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_an_unknown_population_mode_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["population"]["mode"] = "everything"
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_a_nested_exists_without_a_require_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    del raw["population"]["selectors"]["out-of-service"]["nested-exists"]["require"]
    assert config_schema.validate_mapping("entity-match", raw, "mutated")


def test_an_unknown_top_level_block_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["predecessors"] = {"selector": "out-of-service"}
    errors = config_schema.validate_mapping("entity-match", raw, "mutated")
    assert any("predecessors" in e for e in errors)


def test_a_duplicate_detection_config_without_lifecycle_validates():
    # CMS has no dated events in any of its three datasets. The lifecycle block
    # must be optional, or the all-entities mode this schema is meant to
    # support could not be expressed at all.
    raw = {
        "source_index": "hospitals-000001",
        "entity": {"key": "Facility ID", "summary_fields": ["Facility Name", "Address"]},
        "population": {"mode": "all-entities", "sort_field": "Facility ID"},
        "candidates": {"max_candidates": 100, "seed_signals": ["name-phonetic"]},
        "signals": [
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["Facility Name"],
                "subfield": "phonetic",
            },
            {
                "type": "address",
                "weight": 0.5,
                "fields": ["Address"],
                "exact_subfield": "clean",
                "fuzzy_subfield": "tokens",
                # Omitted when this test was written, because the schema then
                # called it optional. That omission is exactly what broke the
                # real CMS sweep, so this test was encoding the same wrong
                # assumption it was meant to guard against.
                "fuzzy_scale": 0.7,
            },
        ],
        "scoring": {"min_total_score": 0.5, "min_signals": 1},
    }
    assert config_schema.validate_mapping("entity-match", raw, "cms") == []


def test_every_registered_signal_type_is_allowed_by_the_schema():
    """The enum must not drift from matching/signals.py's registry.

    A type registered in code but missing here would be rejected as unknown,
    which is a validator refusing a legitimate config -- the failure that
    teaches operators to distrust it.
    """
    schema = json.loads(_SCHEMA_PATH.read_text())
    allowed = set(schema["properties"]["signals"]["items"]["properties"]["type"]["enum"])
    assert set(SIGNAL_TYPES) == allowed
