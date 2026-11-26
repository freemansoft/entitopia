"""The FDIC sweeps compare streets only within one town.

Guards a silent failure measured on 2026-11-26. FDIC lists a bare "Main
Street", with no number, as the address of 1,481 banks. The address signal
scores an exact street match 1.0 and, by design, keeps that score across state
lines. Compared on ADDRESS alone, every one of those banks matched every other
at 1.0: on the tuning half, 7,301 undeclared absorption pairs at 0.5 or above
were a shared number-less street in a different town, and 1,056 absorption
predecessors hit the candidate ceiling because the street seed retrieved
hundreds of them. Both sweeps logged success. Precision at 0.70 was 0.64 for
reappearance and 0.025 for absorption, and rose to 0.96 and 0.17 once the
street was qualified by its town.

The fix is configuration only: the ingest pipeline derives STREET_LOCAL
("<street> | <city> <state>"), its exact subfield keeps the whole string, its
fuzzy subfield strips the town again, and both sweeps' address signal reads it.
This reads the committed files. It does not run the pipeline or the analyzer,
which were checked once with _simulate and _analyze.
"""

import json
from pathlib import Path

import pytest

_CONFIG = Path(__file__).parent.parent / "FDIC-Banks" / "configuration"
_SWEEPS = ("bank-reappearance", "bank-absorption")


def _load(*parts):
    return json.loads(_CONFIG.joinpath(*parts).read_text())


@pytest.mark.parametrize("step", _SWEEPS)
def test_address_signal_reads_the_town_qualified_street(step):
    """Pointed back at ADDRESS, every bare "Main Street" matches across towns."""
    signals = _load(step, "entity-match.json")["signals"]
    address = [s for s in signals if s["type"] == "address"]
    assert [s["fields"] for s in address] == [["STREET_LOCAL"]]


def test_pipeline_qualifies_the_street_by_city_and_state():
    """The derived value must carry the town, and a blank street none at all.

    Without CITY and STALP in the value, the exact comparison is the old
    ADDRESS one. Without the guard on a blank ADDRESS, every bank with no
    street would share " | <city> <state>" with every other bank in its town.
    """
    processors = _load("institutions-ingestion-setup", "pipelines.json")["processors"]
    derive = [p["set"] for p in processors if p.get("set", {}).get("field") == "STREET_LOCAL"]
    assert len(derive) == 1
    value = derive[0]["value"]
    assert value.index("ADDRESS") < value.index("|") < value.index("CITY") < value.index("STALP")
    assert "ctx.ADDRESS != null" in derive[0]["if"]


def test_fuzzy_subfield_drops_the_town_and_exact_keeps_it():
    """Exact equality needs the town; fuzzy overlap must compare streets only.

    Left in the fuzzy tokens, the town and state would give two different
    streets in one town a partial address match from the city words alone.
    """
    mapping = _load("institutions", "index-mappings.json")["mappings"]["properties"]
    subfields = mapping["STREET_LOCAL"]["fields"]
    analysis = _load("institutions", "index-settings.json")["settings"]["index"]["analysis"]
    exact = analysis["analyzer"][subfields["clean"]["analyzer"]]
    fuzzy = analysis["analyzer"][subfields["tokens"]["analyzer"]]
    assert exact["tokenizer"] == "keyword"
    assert "drop_locality" not in exact.get("char_filter", [])
    assert fuzzy["char_filter"][0] == "drop_locality"
    assert analysis["char_filter"]["drop_locality"]["pattern"].startswith("\\s*\\|")
