"""Type inference, and the two rules that exist because of a recorded incident.

A date-shaped column maps `keyword`, never `date`: one malformed value in a
date-mapped field throws `document_parsing_exception` and Elasticsearch drops
the ENTIRE document, not just that field. The generator cannot know whether
every value in a column parses, so it picks the shape that cannot lose a
record and leaves the operator a note.

An integer-shaped column with leading zeros maps `keyword`: dynamic inference
reads it as `long` and destroys the padding, the measured case being ZIP codes
where `00602` became `602`.

Profiles here are built by feeding real values to the profiler's own
ColumnProfile rather than to a stub, so these fail if its accounting changes —
which is the point, since the inference reads its counters.
"""

import importlib.util
from pathlib import Path

from utils import scaffold

_SCRIPT = Path(__file__).parent.parent / "scripts" / "profile_dataset.py"


def _load_profiler():
    """Load the profiler by path.

    scripts/ is a directory of standalone tools, not an importable package —
    the same convention tests/test_profile_dataset.py uses and for the same
    reason.
    """
    spec = importlib.util.spec_from_file_location("profile_dataset", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiler = _load_profiler()


def _profile_of(values, name="col"):
    column = profiler.ColumnProfile(name)
    for value in values:
        column.add(value)
    return column


def test_a_date_shaped_column_is_keyword_not_date():
    column = _profile_of(["2021-01-01", "2021-06-30", "2020-12-31"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_legacy_date_shaped_column_is_also_keyword():
    column = _profile_of(["01-JUN-74", "15-MAR-99", "30-SEP-05"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_us_date_shaped_column_is_also_keyword():
    column = _profile_of(["1/6/2021", "12/31/2020", "3/15/99"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_leading_zero_identifier_is_keyword_not_long():
    column = _profile_of(["00602", "00603", "01234"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_plain_integer_column_is_keyword():
    # Identifiers and codes are keyword even when numeric -- the project rule,
    # and the reason is that a numeric type is only right for something you
    # would do arithmetic on, which an identifier never is.
    column = _profile_of(["23680", "99123", "10001"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_high_cardinality_numeric_identifier_is_keyword_not_text():
    """The case the three-value test above passed for the wrong reason.

    Found by generating against the real CMS provider extract: NPI is a
    ten-digit identifier, unique on every row, so a cardinality test alone
    called it varied text and mapped it `text` -- under which
    {"term": {"NPI": "1234567890"}} matches nothing, silently.

    Uniqueness makes an identifier MORE clearly an identifier, not more like
    free text, so the numeric check has to come before cardinality.
    """
    column = _profile_of([str(1_000_000_000 + i) for i in range(5_000)])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_high_cardinality_numeric_column_with_stray_text_is_still_keyword():
    # The mixed-type trap at scale: mostly digits with a few alphanumerics.
    # keyword is right for both reasons at once.
    values = [str(1_000_000_000 + i) for i in range(5_000)] + ["7221120ND"] * 3
    assert scaffold.field_type(_profile_of(values))["type"] == "keyword"


def test_high_cardinality_names_are_still_text():
    # The rule must not swallow the case it exists to serve: genuinely varied
    # free text still wants analysis.
    column = _profile_of(["EXAMPLE NAME {}".format(i) for i in range(5_000)])
    assert scaffold.field_type(column)["type"] == "text"


def test_low_cardinality_text_is_keyword():
    column = _profile_of(["ACTIVE"] * 50 + ["INACTIVE"] * 50)
    assert scaffold.field_type(column)["type"] == "keyword"


def test_high_cardinality_free_text_gets_a_keyword_subfield():
    column = _profile_of(["EXAMPLE NAME {}".format(i) for i in range(300)])
    mapping = scaffold.field_type(column)
    assert mapping["type"] == "text"
    assert mapping["fields"]["keyword"]["type"] == "keyword"


def test_an_all_blank_column_is_keyword_rather_than_guessed():
    # Nothing to infer from. keyword cannot drop a document, so it is the safe
    # floor; guessing a type from no evidence is how dynamic inference goes
    # wrong in the first place.
    column = _profile_of(["", "", "   "])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_a_mostly_numeric_column_with_one_text_value_is_keyword():
    # The mixed-type trap: dynamic inference sees the first numeric value,
    # maps `long`, and then every alphanumeric row fails to index.
    column = _profile_of(["1", "2", "3", "4", "7221120ND"])
    assert scaffold.field_type(column)["type"] == "keyword"


def test_mapping_properties_covers_every_column_in_declaration_order():
    fieldnames = ["Facility ID", "Facility Name", "State"]
    columns = {
        "Facility ID": _profile_of(["010001", "010005"], "Facility ID"),
        "Facility Name": _profile_of(
            ["EXAMPLE HOSPITAL {}".format(i) for i in range(300)], "Facility Name"
        ),
        "State": _profile_of(["AL"] * 40 + ["OR"] * 40, "State"),
    }
    properties = scaffold.mapping_properties(fieldnames, columns)
    assert list(properties) == fieldnames
    assert properties["Facility ID"]["type"] == "keyword"
    assert properties["Facility Name"]["type"] == "text"
    assert properties["State"]["type"] == "keyword"


def test_a_column_name_is_used_verbatim():
    # CMS ships columns named "City/Town" and "ZIP Code". A generator that
    # normalised them would produce a mapping that matches no CSV header, and
    # Elasticsearch treats a mapping for a nonexistent field as inert.
    fieldnames = ["City/Town", "ZIP Code"]
    columns = {name: _profile_of(["X"], name) for name in fieldnames}
    assert list(scaffold.mapping_properties(fieldnames, columns)) == fieldnames


def test_marker_keys_carry_their_instruction_in_the_name():
    marker, message = scaffold.marker("choose_id_field", "Run the profiler.")
    assert marker.startswith("__TODO_")
    assert marker.endswith("__")
    assert "choose_id_field" in marker
    assert message == "Run the profiler."
