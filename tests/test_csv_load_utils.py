"""What the CSV loader is allowed to change about a source value: nothing.

Exists because the loader is the one place that can corrupt data before any
mapping, pipeline or signal gets a chance to be correct. `pd.read_csv` without
`dtype` types each column by inspecting it, and a column that happens to be
uniformly numeric becomes `int64` — silently discarding the leading zeros of
zero-padded identifiers. No `keyword` mapping can undo that, because the loss
happens before Elasticsearch is involved.

These tests pin the loader's contract: values arrive as the source wrote them,
and missing stays missing.
"""

import io
import json

import numpy as np
import pandas as pd

from utils.csv_load_utils import CsvLoadUtils


def write_csv(tmp_path, text, step="widgets", filename="widgets.csv"):
    """Build the project/data_dir/step/filename layout load_csv expects."""
    directory = tmp_path / "data" / step
    directory.mkdir(parents=True)
    (directory / filename).write_text(text)
    return CsvLoadUtils(str(tmp_path), "data", step, filename, None, 0)


def test_zero_padded_identifier_keeps_its_padding(tmp_path):
    """The defect this module exists to prevent.

    A uniformly numeric column is inferred as int64, so `010001` loads as
    `10001`. When that column is also the configured `id_field`, every affected
    document gets a wrong `_id` and any join against a padded source fails.
    """
    loader = write_csv(tmp_path, "facility_id\n010001\n010005\n")
    assert list(loader.load_csv()["facility_id"]) == ["010001", "010005"]


def test_all_columns_load_as_strings_so_mappings_do_the_typing(tmp_path):
    """Elasticsearch coerces strings into numeric fields, so nothing is lost.

    Letting the mapping decide the type is what makes the loader incapable of
    a type-driven corruption in the first place; inferring in pandas puts that
    decision somewhere no configuration can see it.
    """
    loader = write_csv(tmp_path, "count,ratio\n42,1.50\n7,0.25\n")
    frame = loader.load_csv()
    assert list(frame["count"]) == ["42", "7"]
    assert list(frame["ratio"]) == ["1.50", "0.25"]


def test_blank_cells_stay_missing_rather_than_becoming_a_literal_string(tmp_path):
    """Blank must survive as NaN for phase_index_populate to turn it into None.

    That phase replaces NaN with None so a blank becomes JSON null and the
    field is simply absent. If the loader turned blanks into `''` or `'nan'`,
    every signal's "not evaluable" test would see a present value instead, and
    two records with nothing in a field would appear to agree — the exact
    failure the scoring model is built to avoid.
    """
    loader = write_csv(tmp_path, "name,phone\nACME,\n,5551234\n")
    frame = loader.load_csv()
    assert frame["phone"].isna()[0]
    assert frame["name"].isna()[1]
    assert frame.replace({np.nan: None})["phone"][0] is None


def test_a_mixed_column_is_unchanged_by_the_fix(tmp_path):
    """Guards against a regression that would only show on already-safe columns.

    A column mixing numeric and non-numeric values was already loaded as
    strings, so the fix must be a no-op here. If this starts failing, the
    loader has begun transforming values rather than merely declining to
    retype them.
    """
    loader = write_csv(tmp_path, "state_id\n12345\nNONE\nPA\n")
    assert list(loader.load_csv()["state_id"]) == ["12345", "NONE", "PA"]


def test_row_and_skip_limits_still_apply(tmp_path):
    """The fix must not disturb the truncation the loader already supports.

    num_rows drives the documented partial-load path, and skip_rows exists for
    files carrying a banner above the header.
    """
    loader = CsvLoadUtils(str(tmp_path), "data", "widgets", "widgets.csv", 2, 0)
    directory = tmp_path / "data" / "widgets"
    directory.mkdir(parents=True)
    (directory / "widgets.csv").write_text("id\n001\n002\n003\n004\n")
    assert list(loader.load_csv()["id"]) == ["001", "002"]


def test_loaded_frame_is_json_serializable_after_the_nan_replacement(tmp_path):
    """End-to-end guard on the shape phase_index_populate actually ships.

    pandas 3 carries several missing-value sentinels, and one that survived
    `replace` would reach json serialization as `<NA>` — either raising deep
    inside parallel_bulk or, worse, indexing the literal text.
    """
    loader = write_csv(tmp_path, "a,b\n010001,\n020002,x\n")
    frame = loader.load_csv().replace({np.nan: None})
    records = frame.to_dict("records")
    assert json.loads(json.dumps(records)) == [
        {"a": "010001", "b": None},
        {"a": "020002", "b": "x"},
    ]


def test_pandas_would_have_corrupted_this_column_without_the_fix(tmp_path):
    """Pins the defect's mechanism, so the fix cannot be quietly reverted.

    Asserting on pandas' own default behavior rather than on the loader: if a
    future pandas stops inferring int64 here, this fails and tells the reader
    the hazard changed shape, rather than leaving a fix in place for a reason
    that no longer holds.
    """
    inferred = pd.read_csv(io.StringIO("facility_id\n010001\n010005\n"))["facility_id"]
    assert inferred.dtype == "int64"
    assert list(inferred) == [10001, 10005]
