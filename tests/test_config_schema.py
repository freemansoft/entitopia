"""Config validation reports every problem it can see, by file and key.

A validator that stops at the first error turns a five-mistake config into five
runs, so these pin that messages accumulate. They also pin the setting the whole
exercise rests on: an unknown key is an error, not an ignored key. A permissive
schema accepts a renamed config key silently and lets the value fall back to its
default with nothing reported, which is this repo's recurring failure shape.
"""

import pytest

from utils import config_schema


def test_a_valid_index_config_reports_nothing():
    raw = {
        "alias": "carriers-000001",
        "index": "carriers-{now/d}-000001",
        "source": "carriers.csv",
        "id_field": "dot_number",
        "num_rows": None,
        "skip_rows": 0,
    }
    assert config_schema.validate_mapping("index-config", raw, "test.json") == []


def test_a_composite_id_field_is_accepted():
    # Several datasets key on a list of columns; both shapes are legitimate.
    raw = {
        "alias": "a-000001",
        "index": "a-000001",
        "source": "a.csv",
        "id_field": ["Ind_enrl_ID", "org_pac_id", "adrs_id"],
    }
    assert config_schema.validate_mapping("index-config", raw, "test.json") == []


def test_an_output_index_needs_no_source():
    # chameleon-candidates and chameleon-validation are WRITTEN by a phase, not
    # loaded from a file. Requiring `source` unconditionally rejected both, and
    # they have been running for months.
    raw = {"index": "chameleon-candidates-{now/d}-000001", "alias": "chameleon-candidates-000001"}
    assert config_schema.validate_mapping("index-config", raw, "test.json") == []


def test_a_loading_key_without_a_source_is_reported():
    # The other half of the same rule: `source` is optional only for an index
    # nothing loads. A dataset config carrying id_field but no source is the
    # real mistake this still has to catch.
    raw = {"index": "a-000001", "alias": "a-000001", "id_field": "dot_number"}
    errors = config_schema.validate_mapping("index-config", raw, "test.json")
    assert any("source" in e for e in errors)


def test_an_unknown_key_is_an_error_not_an_ignored_key():
    raw = {
        "alias": "a-000001",
        "index": "a-000001",
        "source": "a.csv",
        "num_rowz": 100,
    }
    errors = config_schema.validate_mapping("index-config", raw, "test.json")
    assert errors
    assert any("num_rowz" in e for e in errors)


def test_a_missing_required_key_is_reported():
    errors = config_schema.validate_mapping("index-config", {"alias": "a"}, "test.json")
    assert any("index" in e for e in errors)


def test_every_problem_is_reported_not_just_the_first():
    raw = {"alias": 7, "index": 9, "source": 11}
    errors = config_schema.validate_mapping("index-config", raw, "test.json")
    assert len(errors) >= 3


def test_messages_name_the_file_and_the_key_path():
    errors = config_schema.validate_mapping(
        "index-config",
        {"alias": 7, "index": "i", "source": "s"},
        "DOT/index-config.json",
    )
    assert any("DOT/index-config.json" in e and "alias" in e for e in errors)


def test_an_unknown_schema_kind_raises():
    # A typo in a caller's kind string must not silently validate nothing: an
    # absent schema would otherwise accept everything forever.
    with pytest.raises(ValueError, match="no schema"):
        config_schema.validate_mapping("not-a-kind", {}, "test.json")


def test_unreadable_json_is_reported_as_a_finding_not_an_exception():
    # A file that will not parse is a validation failure like any other; making
    # the caller handle it separately would split the report in two.
    errors = config_schema.validate_file("index-config", "does/not/exist.json")
    assert any("not found" in e for e in errors)
