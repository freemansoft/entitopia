"""The GLEIF ingest pipeline must not load quietly wrong entities.

Guards three silent failures, each of which passes loading unnoticed:

- A street without its town matches every same-named street. FDIC learned
  this with 1,481 banks at a bare "Main Street" (FDIC hazard 5,
  tests/test_fdic_street_local.py); GLEIF's `*_LOCAL` fields copy that shape,
  so a record missing its city or region must get no `*_LOCAL` value rather
  than "<street> | " shared with every other town-less namesake.
- A date that fails the strict `date` mapping drops the whole entity, not just
  the value: Elasticsearch rejects the document, and one bad creation date
  would remove a predecessor or its successor from every sweep.
- A `|`-joined column left unsplit is one long keyword, so a parent or
  successor LEI would never equal the other record's LEI and every declared
  link through it would read as missing.

This reads the committed pipeline. It does not run it, which was checked once
with `_simulate`.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "GLEIF-Entities"
_PIPELINE = _ROOT / "configuration" / "entities-ingestion-setup" / "pipelines.json"
_LOCAL = {"HQ_LOCAL": "HQ", "LEGAL_LOCAL": "LEGAL"}
_DATES = ("CREATION_DATE", "SHUTDOWN_DATE")


def _processors():
    """The committed processors, read as plain JSON.

    Plain json rather than file_utils.load_from_file, so a misspelled key reads
    as absent instead of hiding behind a getattr default.
    """
    return json.loads(_PIPELINE.read_text())["processors"]


def _entity_columns():
    """prepare_gleif.EntityColumns, loaded by path because GLEIF-Entities is not a package.

    The list of split columns is read from the class that writes them, so a
    list column added to the prepare step without a matching split fails here.
    """
    spec = importlib.util.spec_from_file_location("prepare_gleif", _ROOT / "prepare_gleif.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EntityColumns


@pytest.mark.parametrize("field,prefix", _LOCAL.items())
def test_local_address_needs_city_and_region(field, prefix):
    """The value carries street then town, and is set only when all three are present."""
    derive = [p["set"] for p in _processors() if p.get("set", {}).get("field") == field]
    assert len(derive) == 1
    value = derive[0]["value"]
    street, city, region = ("{}_{}".format(prefix, part) for part in ("STREET", "CITY", "REGION"))
    assert value.index(street) < value.index("|") < value.index(city) < value.index(region)
    for part in (street, city, region):
        assert "ctx.{} != null".format(part) in derive[0]["if"]
        assert "ctx.{}.toString().trim() != ''".format(part) in derive[0]["if"]


@pytest.mark.parametrize("field", _DATES)
def test_date_failure_removes_field(field):
    """A date that will not parse is removed, so the entity still loads without it."""
    dates = [p["date"] for p in _processors() if p.get("date", {}).get("field") == field]
    assert len(dates) == 1
    assert dates[0]["target_field"] == field
    assert dates[0]["formats"] == ["yyyy-MM-dd"]
    removes = [step["remove"] for step in dates[0]["on_failure"] if "remove" in step]
    assert [r["field"] for r in removes] == [field]


def test_list_columns_are_split():
    """Every column the prepare step joins with `|` is split again on `|`."""
    split = {p["split"]["field"]: p["split"] for p in _processors() if "split" in p}
    columns = _entity_columns().list_valued()
    assert columns, "prepare_gleif names no list-valued columns"
    for column in columns:
        assert column in split, column
        assert split[column]["separator"] == "\\|"
        assert split[column]["ignore_missing"] is True
