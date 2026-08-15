"""Bulk actions built from CSV rows, and how a keyless row is reported.

The counting matters as much as the keying: a blank id_field used to produce a
correct-looking load that grew by a document per reload, and the only way an
operator finds out is if the phase says so at the end of the run.
"""

import logging

import pandas as pd

from phase_providers.phase_index_populate import PhaseIndexingPopulate
from utils import id_utils


def populate_phase():
    return PhaseIndexingPopulate(
        es=None, project="DOT-Commercial", one_step="boc3-agents", project_config=None
    )


def frame(rows):
    return pd.DataFrame(rows)


def actions(rows, id_field, blank_keys=None, pipeline=None):
    return list(
        populate_phase().record_action(frame(rows), pipeline, id_field, None, blank_keys)
    )


def test_rows_are_keyed_by_the_configured_field():
    (doc,) = actions([{"docket_number": "MC1", "agent": "X"}], "docket_number")
    assert doc["_id"] == "MC1"


def test_blank_key_is_counted_for_the_end_of_run_report():
    blank_keys = {"count": 0}
    rows = [
        {"docket_number": "MC1", "agent": "X"},
        {"docket_number": "", "agent": "Y"},
        {"docket_number": "MC2", "agent": "Z"},
    ]
    docs = actions(rows, "docket_number", blank_keys)
    assert blank_keys["count"] == 1
    assert docs[1]["_id"].startswith(id_utils.BLANK_KEY_PREFIX)


def test_absent_column_yields_an_auto_generated_id_and_is_not_counted_as_blank():
    # A missing column is the pre-existing, intentional path to ES-generated
    # ids; conflating it with a blank value would report every unkeyed dataset
    # as defective.
    blank_keys = {"count": 0}
    (doc,) = actions([{"agent": "X"}], "docket_number", blank_keys)
    assert "_id" not in doc
    assert blank_keys["count"] == 0


def test_pipeline_is_carried_on_the_fallback_keyed_document():
    # The fallback rewrites _id only; a row that needs an ingest pipeline still
    # needs it, and losing it would leave that one row untransformed.
    (doc,) = actions([{"docket_number": "", "agent": "Y"}], "docket_number", pipeline="p1")
    assert doc["pipeline"] == "p1"
    assert doc["_id"].startswith(id_utils.BLANK_KEY_PREFIX)


def test_no_counter_supplied_still_keys_the_row():
    # entity-match and any other caller that does not care about the count must
    # keep working unchanged.
    (doc,) = actions([{"docket_number": "", "agent": "Y"}], "docket_number", None)
    assert doc["_id"].startswith(id_utils.BLANK_KEY_PREFIX)


def test_blank_count_of_zero_says_nothing(caplog):
    # The warning has to stay rare enough to be read; a clean load must be
    # silent on this.
    blank_keys = {"count": 0}
    with caplog.at_level(logging.WARNING):
        actions([{"docket_number": "MC1"}], "docket_number", blank_keys)
    assert caplog.text == ""
