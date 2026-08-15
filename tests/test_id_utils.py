"""Deterministic _id construction, including the row that has no key at all.

The guarantee under test is the one compute_id's docstring makes: reloading the
same CSV into the same index overwrites rather than appends. A blank key used
to break that silently — the row indexed fine, under a fresh random id every
run — so these tests pin that the fallback id is a property of the data, never
of the run.
"""

import pytest

from utils import id_utils


def test_single_field_key_is_the_field_value():
    assert id_utils.compute_id({"docket_number": "MC1234"}, "docket_number") == "MC1234"


def test_composite_key_joins_the_named_fields():
    record = {"dot_number": "1", "oos_date": "2025-01-01", "status": "A"}
    key = id_utils.compute_id(record, ["dot_number", "oos_date", "status"])
    assert key == "1|2025-01-01|A"


def test_absent_column_still_raises_so_callers_fall_back_to_auto_ids():
    # A missing column is a configuration error, not a data one, and the
    # populate phase's KeyError handler is what keeps a misconfigured id_field
    # from taking down a load. The blank-value fallback must not swallow it.
    with pytest.raises(KeyError):
        id_utils.compute_id({"other": "x"}, "docket_number")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_key_falls_back_to_a_hash_instead_of_an_auto_generated_id(blank):
    got = id_utils.compute_id({"docket_number": blank, "agent": "X"}, "docket_number")
    assert got.startswith(id_utils.BLANK_KEY_PREFIX)


def test_the_fallback_id_is_stable_across_runs():
    # This is the whole fix: the same row must produce the same _id on every
    # reload, or the index grows by one document per run per keyless row.
    record = {"docket_number": None, "agent": "X"}
    assert id_utils.compute_id(record, "docket_number") == id_utils.compute_id(
        dict(record), "docket_number"
    )


def test_different_rows_get_different_fallback_ids():
    a = id_utils.compute_id({"docket_number": None, "agent": "X"}, "docket_number")
    b = id_utils.compute_id({"docket_number": None, "agent": "Y"}, "docket_number")
    assert a != b


def test_column_order_does_not_change_the_fallback_id():
    # Rows arrive as dicts built from the CSV header, so a reordered export of
    # the same data would otherwise re-key every keyless row and duplicate it.
    a = id_utils.compute_id({"docket_number": None, "agent": "X", "city": "Z"}, "docket_number")
    b = id_utils.compute_id({"city": "Z", "agent": "X", "docket_number": None}, "docket_number")
    assert a == b


def test_all_blank_composite_key_falls_back_rather_than_keying_every_row_alike():
    # An all-empty composite renders as one constant string, so without the
    # fallback every such row collapses onto one document — a quieter failure
    # than the random-id case and a lossier one.
    got = id_utils.compute_id({"a": "", "b": "", "c": None, "x": "1"}, ["a", "b", "c"])
    assert got.startswith(id_utils.BLANK_KEY_PREFIX)


def test_partially_blank_composite_key_is_kept_as_is():
    # Still discriminating, so hashing it would throw away a usable key and
    # make the id depend on columns nobody chose to key on.
    got = id_utils.compute_id({"a": "1", "b": "", "c": "3"}, ["a", "b", "c"])
    assert got == "1||3"


def test_a_none_component_still_renders_as_the_literal_None():
    # Not a nicety being preserved for its own sake: 56.1% of the live
    # out-of-service-orders index (221,812 of 395,269 documents) is keyed this
    # way, and rendering None as "" instead would re-key every one of them into
    # a duplicate on the next reload into an existing index.
    got = id_utils.compute_id({"a": "1", "b": None, "c": "3"}, ["a", "b", "c"])
    assert got == "1|None|3"
