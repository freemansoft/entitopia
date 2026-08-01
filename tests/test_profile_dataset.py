"""Tests for scripts/profile_dataset.py.

The README tells anyone adding a dataset to run this first and act on every
warning it prints, so a warning that silently stops firing would remove the
guard rail without removing the advice. Each test below pins one hazard the
profiler claims to catch, using a fixture that embeds the real failures this
project has already hit:

    carrier_state_id  mixes numeric and non-numeric values, the shape that
                      dropped 36,788 of 5,647,567 rows from a production load
    dot_number        zero-padded identifiers, which a numeric type destroys
    effective_date    Oracle dd-MMM-yy dates, which dynamic detection misses
    cancel_date       blank on every still-active row
    insurer_name      a handful of values dominating the column
    filing_id         unique, except for one byte-identical duplicate row

Loaded by path rather than import because scripts/ is a directory of
standalone tools, not an importable package.
"""

import importlib.util
from pathlib import Path

import pytest

FIXTURE = str(Path(__file__).parent / "fixtures" / "profile_traps.csv")
_SCRIPT = Path(__file__).parent.parent / "scripts" / "profile_dataset.py"


def _load_profiler():
    spec = importlib.util.spec_from_file_location("profile_dataset", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiler = _load_profiler()


@pytest.fixture(scope="module")
def columns():
    _, cols, _ = profiler.profile(FIXTURE)
    return cols


def _warning_kinds(column):
    """The leading label of each warning, e.g. 'MIXED TYPES'."""
    return {text.split(":")[0] for text in column.warnings()}


def test_profile_reads_every_row_and_column():
    names, cols, rows = profiler.profile(FIXTURE)
    assert rows == 11
    assert len(names) == 10
    assert set(names) == set(cols)


def test_mixed_numeric_and_text_column_is_flagged(columns):
    # The failure that dropped 36,788 documents: dynamic mapping infers a
    # numeric type from whichever value arrives first, then every
    # non-conforming row fails to index and the whole document is rejected.
    kinds = _warning_kinds(columns["carrier_state_id"])
    assert "MIXED TYPES" in kinds


def test_mixed_type_warning_names_the_offending_values(columns):
    # A count alone does not tell you what to look for in the source.
    warning = next(w for w in columns["carrier_state_id"].warnings() if w.startswith("MIXED TYPES"))
    assert "S00000030887" in warning or "NONE" in warning


def test_leading_zero_identifier_is_flagged(columns):
    kinds = _warning_kinds(columns["dot_number"])
    assert "LEADING ZEROS" in kinds


def test_oracle_format_dates_are_flagged(columns):
    # Elasticsearch will not auto-detect dd-MMM-yy, so the field lands as
    # text; mapping it naively resolves 01-JAN-74 to 2074.
    for name in ("effective_date", "cancel_date"):
        assert "NON-ISO DATES" in _warning_kinds(columns[name])


def test_sparse_column_is_flagged(columns):
    # cancel_date is blank on every still-active policy, so a signal reading
    # it must treat blank as "not evaluable" rather than as agreement.
    assert "SPARSE" in _warning_kinds(columns["cancel_date"])


def test_dense_column_is_not_flagged_as_sparse(columns):
    assert "SPARSE" not in _warning_kinds(columns["filing_id"])


def test_dominant_value_column_is_flagged_as_low_cardinality(columns):
    warning = next(w for w in columns["insurer_name"].warnings() if w.startswith("LOW CARDINALITY"))
    # The warning has to carry the collision estimate, since that is the
    # number that decides whether a field can carry a match at all.
    assert "%" in warning


def test_unique_key_is_reported_safe():
    # filing_id repeats once, but only on a byte-identical row.
    result = profiler.check_key(FIXTURE, ["filing_id"])
    assert result["real_collisions"] == 0
    assert result["identical_rows"] == 1


def test_byte_identical_duplicates_are_distinguished_from_real_collisions():
    # These call for opposite responses: a composite key correctly collapses
    # identical rows, but a real collision means the key is simply wrong.
    result = profiler.check_key(FIXTURE, ["filing_id"])
    assert result["duplicate_keys"] == result["identical_rows"] + result["real_collisions"]


def test_non_unique_key_reports_real_collisions():
    # dot_number repeats across separate filings that genuinely differ.
    result = profiler.check_key(FIXTURE, ["dot_number"])
    assert result["real_collisions"] > 0
    assert result["example_collision"] is not None


def test_composite_key_narrows_but_does_not_rescue_a_wrong_key():
    single = profiler.check_key(FIXTURE, ["dot_number"])
    composite = profiler.check_key(FIXTURE, ["dot_number", "policy_number"])
    assert composite["real_collisions"] < single["real_collisions"]


def test_missing_column_is_reported_not_raised():
    result = profiler.check_key(FIXTURE, ["no_such_column"])
    assert "error" in result


def test_high_cardinality_column_is_not_called_low(columns):
    # filing_id identifies a row; calling it a filter would invert the
    # fingerprint-versus-filter split the profiler exists to draw.
    assert "LOW CARDINALITY" not in _warning_kinds(columns["filing_id"])
