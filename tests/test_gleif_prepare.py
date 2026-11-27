"""Tests for GLEIF-Entities/prepare_gleif.py.

The prepare step turns the golden copy into the one CSV the GLEIF sweeps read,
and two of its quiet mistakes would each pass loading unnoticed:

    shifted dates      GLEIF writes dates with an offset
                       (1996-01-01T00:00:00+01:00). Converting to UTC moves
                       that to the previous day, and a one-day shift is enough
                       to move a same-day succession from one sweep's window
                       to the other's.
    self-successors    an inactive record naming its own LEI as successor is a
                       truth link the matcher could never retrieve, because the
                       candidate search excludes the predecessor itself, so it
                       would read as a recall miss forever.

The rest pin the shapes the declared-link rules rely on: successor chains
followed to their end (so A-C in a chain A->B->C is explained, not reported),
parents copied onto each child from the same download, list values that
survive the `|` join, and a zip or header that is not what the column map
expects refused rather than read as blanks.

Every LEI here is invented and fails the LEI checksum.
Loaded by path because GLEIF-Entities is a project directory, not a package.
"""

import importlib.util
import io
import re
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "GLEIF-Entities" / "prepare_gleif.py"


def _load():
    # By path: GLEIF-Entities is a project directory, not an importable package.
    spec = importlib.util.spec_from_file_location("prepare_gleif", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare_gleif = _load()
COLUMNS = prepare_gleif.EntityColumns()
RELATIONSHIPS = prepare_gleif.RelationshipColumns()

A = "TESTA0000000000000" + "00"
B = "TESTB0000000000000" + "00"
C = "TESTC0000000000000" + "00"
D = "TESTD0000000000000" + "00"


def _entity(lei, **values):
    """A Level 1 row with every configured column blank except those given.

    Keyword names are output columns; list-valued ones take a list, filled into
    the source columns in order.
    """
    row = dict.fromkeys(COLUMNS.required(), "")
    row[COLUMNS.LEI] = lei
    for name, value in values.items():
        source = getattr(COLUMNS, name)
        if isinstance(source, tuple):
            for column, item in zip(source, value, strict=False):
                row[column] = item
        else:
            row[source] = value
    return row


def _prepared(rows, parents=None, chain_ends=None):
    # Runs the writing pass to completion and hands back its counters as well.
    stats = prepare_gleif.PrepStats()
    out = list(
        prepare_gleif.prepare_entities(
            rows, parents or {}, COLUMNS, chain_ends=chain_ends or {}, stats=stats
        )
    )
    return out, stats


def test_offset_date_keeps_calendar_day():
    assert prepare_gleif.iso_day("1996-01-01T00:00:00+01:00") == "1996-01-01"
    assert prepare_gleif.iso_day("2012-06-06T15:53:00Z") == "2012-06-06"
    assert prepare_gleif.iso_day("") is None
    assert prepare_gleif.iso_day(None) is None
    assert prepare_gleif.iso_day("garbage") is None
    assert prepare_gleif.iso_day("2021-02-29") is None


def test_self_successor_dropped_and_counted():
    out, stats = _prepared([_entity(A, SUCCESSOR_LEI=[A, B])])
    assert out[0]["SUCCESSOR_LEI"] == B
    assert stats["self_successor"] == 1
    assert prepare_gleif.collect_successors([_entity(A, SUCCESSOR_LEI=[A])], COLUMNS) == {}


def test_malformed_successor_dropped_and_counted():
    out, stats = _prepared([_entity(A, SUCCESSOR_LEI=["not-an-lei"])])
    assert out[0]["SUCCESSOR_LEI"] == ""
    assert stats["successor_bad_format"] == 1


def test_chain_end_reaches_the_last_entity():
    ends, stats = prepare_gleif.successor_chain_ends({A: [B], B: [C]})
    assert ends == {A: [C], B: [C], C: [C]}
    assert stats["chain_members"] == 3
    assert stats["chain_length_2"] == 1
    assert stats["chain_length_1"] == 1
    assert stats["chain_length_0"] == 1


def test_chain_end_of_a_split():
    ends, stats = prepare_gleif.successor_chain_ends({A: [B, D]})
    assert ends[A] == [B, D]
    assert ends[B] == [B]
    assert ends[D] == [D]
    assert stats["split_successor"] == 1


def test_chain_end_survives_a_cycle():
    ends, stats = prepare_gleif.successor_chain_ends({A: [B], B: [A]})
    assert ends == {A: [A], B: [A]}
    assert stats["successor_cycle"] == 1


def test_unchained_entity_has_no_chain_end():
    ends, _ = prepare_gleif.successor_chain_ends({A: [B]})
    assert C not in ends
    out, _ = _prepared([_entity(C)], chain_ends=ends)
    assert out[0]["SUCCESSOR_CHAIN_END"] == ""


def test_successor_absent_from_file_is_an_end():
    # B names no successor because it has no record at all in this file.
    successors = prepare_gleif.collect_successors([_entity(A, SUCCESSOR_LEI=[B])], COLUMNS)
    ends, _ = prepare_gleif.successor_chain_ends(successors)
    assert ends[A] == [B]
    out, _ = _prepared([_entity(A, SUCCESSOR_LEI=[B])], chain_ends=ends)
    assert out[0]["SUCCESSOR_CHAIN_END"] == B


def test_successor_without_lei_counted():
    row = _entity(A)
    row[COLUMNS.successor_names[0]] = "SOME SUCCESSOR NAME LLC"
    out, stats = _prepared([row])
    assert out[0]["SUCCESSOR_LEI"] == ""
    assert stats["successor_without_lei"] == 1


def _relationship(child, parent, kind, status):
    # A Level 2 row holding only the four columns the pivot reads.
    return {
        RELATIONSHIPS.child: child,
        RELATIONSHIPS.parent: parent,
        RELATIONSHIPS.relationship_type: kind,
        RELATIONSHIPS.status: status,
    }


# Only the two types the pivot tests feed; the full map would refuse them as
# a vocabulary with types that match no row.
TWO_TYPES = prepare_gleif.RelationshipColumns(
    parent_columns={
        "IS_DIRECTLY_CONSOLIDATED_BY": "DIRECT_PARENT_LEI",
        "IS_ULTIMATELY_CONSOLIDATED_BY": "ULTIMATE_PARENT_LEI",
    }
)


def test_relationships_pivot_keeps_every_status():
    rows = [
        _relationship(A, B, "IS_DIRECTLY_CONSOLIDATED_BY", "ACTIVE"),
        _relationship(A, D, "IS_DIRECTLY_CONSOLIDATED_BY", "INACTIVE"),
        _relationship(A, C, "IS_ULTIMATELY_CONSOLIDATED_BY", "ACTIVE"),
    ]
    parents, counts = prepare_gleif.pivot_relationships(rows, TWO_TYPES)
    assert parents == {A: {"DIRECT_PARENT_LEI": [B, D], "ULTIMATE_PARENT_LEI": [C]}}
    assert counts == {
        ("IS_DIRECTLY_CONSOLIDATED_BY", "ACTIVE"): 1,
        ("IS_DIRECTLY_CONSOLIDATED_BY", "INACTIVE"): 1,
        ("IS_ULTIMATELY_CONSOLIDATED_BY", "ACTIVE"): 1,
    }


def test_parents_joined_onto_entity():
    parents = {A: {"DIRECT_PARENT_LEI": [B, D], "ULTIMATE_PARENT_LEI": [C]}}
    out, _ = _prepared([_entity(A), _entity(B)], parents=parents)
    assert out[0]["DIRECT_PARENT_LEI"] == "{}|{}".format(B, D)
    assert out[0]["ULTIMATE_PARENT_LEI"] == C
    assert out[1]["DIRECT_PARENT_LEI"] == ""
    assert list(out[0]) == COLUMNS.output_columns()


def test_separator_in_value_replaced_and_counted():
    out, stats = _prepared([_entity(A, OTHER_NAMES=["ALPHA | BETA", "GAMMA"])])
    assert out[0]["OTHER_NAMES"] == "ALPHA   BETA|GAMMA"
    assert stats["separator_replaced"] == 1


def test_list_columns_are_discoverable():
    assert {
        "OTHER_NAMES",
        "SUCCESSOR_LEI",
        "SUCCESSOR_CHAIN_END",
        "DIRECT_PARENT_LEI",
        "ULTIMATE_PARENT_LEI",
    } <= set(prepare_gleif.EntityColumns.list_valued())
    assert "LEGAL_NAME" not in prepare_gleif.EntityColumns.list_valued()


def _zip(tmp_path, members, name="file.zip"):
    # A zip built in the test, so member names and counts are under its control.
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    return path


def test_missing_column_refused(tmp_path):
    path = _zip(tmp_path, {"x.csv": "LEI,Entity.LegalName\n{},NAME\n".format(A)})
    with pytest.raises(RuntimeError, match=re.escape("Entity.EntityStatus")):
        prepare_gleif.read_zip_csv(path, ["LEI", "Entity.EntityStatus"])


def test_zip_must_hold_one_csv(tmp_path):
    two = _zip(tmp_path, {"a.csv": "LEI\n", "b.csv": "LEI\n"})
    with pytest.raises(RuntimeError, match="exactly one"):
        prepare_gleif.read_zip_csv(two, ["LEI"])
    (tmp_path / "file.zip").unlink()
    other = _zip(tmp_path, {"a.txt": "LEI\n"})
    with pytest.raises(RuntimeError, match="exactly one"):
        prepare_gleif.read_zip_csv(other, ["LEI"])


def test_zip_rows_stream_as_dicts(tmp_path):
    body = io.StringIO()
    body.write('LEI,Entity.LegalName\n{},"NAME, WITH COMMA"\n'.format(A))
    path = _zip(tmp_path, {"x.csv": body.getvalue()})
    assert list(prepare_gleif.read_zip_csv(path, ["LEI"])) == [
        {"LEI": A, "Entity.LegalName": "NAME, WITH COMMA"}
    ]


def _with_events(row, *events):
    """Fill event slots in order with (type, status, effective date)."""
    for slot, (kind, status, day) in zip(COLUMNS.events, events, strict=False):
        row[slot.event_type] = kind
        row[slot.status] = status
        row[slot.effective_date] = day
    return row


def test_shutdown_prefers_own_end_date_then_latest_terminal_event():
    own = _with_events(
        _entity(A, ENTITY_STATUS="INACTIVE", SHUTDOWN_DATE="2019-05-01T00:00:00+02:00"),
        ("DISSOLUTION", "COMPLETED", "2020-01-01T00:00:00Z"),
    )
    events = _with_events(
        _entity(B, ENTITY_STATUS="INACTIVE"),
        ("LIQUIDATION", "COMPLETED", "2018-03-01T00:00:00Z"),
        ("MERGERS_AND_ACQUISITIONS", "COMPLETED", "2021-07-15T00:00:00+01:00"),
        # Later, but not terminal, not completed, or not a date: none may win.
        ("CHANGE_LEGAL_NAME", "COMPLETED", "2023-01-01T00:00:00Z"),
        ("DISSOLUTION", "WITHDRAWN_CANCELLED", "2024-01-01T00:00:00Z"),
        ("ABSORPTION", "IN_PROGRESS", "2025-01-01T00:00:00Z"),
    )
    undatable = _with_events(
        _entity(C, ENTITY_STATUS="INACTIVE"),
        ("BANKRUPTCY", "COMPLETED", "2017-01-01T00:00:00Z"),
    )
    # The surviving side of a merger carries the same completed event.
    survivor = _with_events(
        _entity(D, ENTITY_STATUS="ACTIVE"),
        ("MERGERS_AND_ACQUISITIONS", "COMPLETED", "2021-07-15T00:00:00Z"),
    )
    out, stats = _prepared([own, events, undatable, survivor])
    assert [row["SHUTDOWN_DATE"] for row in out] == ["2019-05-01", "2021-07-15", "", ""]
    assert stats["no_shutdown_date"] == 1


def test_chain_end_of_a_cycle_with_an_exit():
    # Only a closed cycle falls back to its smallest LEI; one with an exit
    # leads somewhere, and its members share where it leads.
    ends, stats = prepare_gleif.successor_chain_ends({A: [B], B: [A, C]})
    assert ends[A] == [C]
    assert ends[B] == [C]
    assert ends[C] == [C]
    assert stats["successor_cycle"] == 1


def test_relationship_type_with_no_rows_refused():
    rows = [_relationship(A, B, "IS_DIRECTLY_CONSOLIDATED_BY", "ACTIVE")]
    with pytest.raises(RuntimeError, match="IS_FEEDER_TO"):
        prepare_gleif.pivot_relationships(rows, RELATIONSHIPS)


def test_no_dated_inactive_record_refused():
    rows = [
        _with_events(
            _entity(A, ENTITY_STATUS="INACTIVE"),
            ("RENAMED_TERMINAL_TYPE", "COMPLETED", "2020-01-01T00:00:00Z"),
        ),
        _entity(B, ENTITY_STATUS="INACTIVE"),
    ]
    with pytest.raises(RuntimeError, match="SHUTDOWN_DATE"):
        _prepared(rows)


def test_mixed_publish_refused(tmp_path):
    lei2 = _zip(tmp_path, {"20261126-1600-gleif-goldencopy-lei2-golden-copy.csv": "LEI\n"}, "l.zip")
    old_rr = _zip(tmp_path, {"20261126-0800-gleif-goldencopy-rr-golden-copy.csv": "x\n"}, "r.zip")
    with pytest.raises(RuntimeError, match="different publishes"):
        prepare_gleif.require_same_publish(lei2, old_rr)
    same_rr = _zip(tmp_path, {"20261126-1600-gleif-goldencopy-rr-golden-copy.csv": "x\n"}, "s.zip")
    assert prepare_gleif.require_same_publish(lei2, same_rr) == "20261126-1600"
    unstamped = _zip(tmp_path, {"rr.csv": "x\n"}, "u.zip")
    with pytest.raises(RuntimeError, match="publish stamp"):
        prepare_gleif.require_same_publish(lei2, unstamped)


def test_care_of_street_read_from_next_line():
    additional = dict(COLUMNS.care_of_streets)
    agent = _entity(A, LEGAL_STREET="C/O SOME FILING AGENT INC", HQ_STREET="care of a manager")
    agent[additional["LEGAL_STREET"]] = "100 EXAMPLE ROAD"
    plain = _entity(B, LEGAL_STREET="200 SAMPLE AVENUE", HQ_STREET="CARE OF ANOTHER AGENT")
    plain[additional["HQ_STREET"]] = "SUITE 5, 300 PLACEHOLDER LANE"
    out, stats = _prepared([agent, plain])
    assert [row["LEGAL_STREET"] for row in out] == ["100 EXAMPLE ROAD", "200 SAMPLE AVENUE"]
    assert [row["HQ_STREET"] for row in out] == ["", "SUITE 5, 300 PLACEHOLDER LANE"]
    assert stats["care_of_street_replaced"] == 2
    assert stats["care_of_no_street"] == 1
