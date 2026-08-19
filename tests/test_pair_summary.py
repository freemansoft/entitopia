"""The emitted pair summary is configuration, not a fixed FMCSA field list.

A pair document is routinely read on its own — pulled by _id, exported into a
review sample, quoted in a README — so it has to be self-describing without the
project config in the reader's hands. That is why the generic entity_key and
the project's own label are both emitted rather than either alone, and why a
configured-but-absent field stays present as null rather than vanishing.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from matching.documents import EntityDoc
from matching.scorer import ScoredPair

_PHASE = Path(__file__).parent.parent / "phase_providers" / "phase_entity_match.py"

_DOT_ENTITY_MATCH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)


def _load_phase():
    spec = importlib.util.spec_from_file_location("phase_entity_match", _PHASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase = _load_phase()


def _doc():
    return EntityDoc(
        entity_key="12345",
        source={"legal_name": "ACME EXAMPLE", "phy_city": "SPRINGFIELD", "unused": "x"},
        tokens={},
    )


def test_summary_emits_the_generic_key():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["entity_key"] == "12345"


def test_summary_emits_the_project_label_as_a_copy():
    config = SimpleNamespace(
        key="dot_number", key_label="dot_number", summary_fields=["legal_name"]
    )
    summary = phase._entity_summary(_doc(), config)
    assert summary["dot_number"] == "12345"
    assert summary["entity_key"] == "12345"


def test_summary_omits_the_label_when_unset():
    # A project with no label gets entity_key only. Emitting a null-valued key
    # would later read as a real absent value rather than as "never labelled".
    config = SimpleNamespace(key="Facility ID", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config)
    assert "dot_number" not in summary
    assert list(summary) == ["entity_key", "legal_name"]


def test_summary_includes_only_configured_fields():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name", "phy_city"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["legal_name"] == "ACME EXAMPLE"
    assert summary["phy_city"] == "SPRINGFIELD"
    assert "unused" not in summary


def test_summary_keeps_a_configured_field_that_is_absent():
    # Absent must stay distinguishable from "not configured": a reviewer
    # reading one pair needs to know the field was asked for and was empty.
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name", "dba_name"])
    summary = phase._entity_summary(_doc(), config)
    assert summary["dba_name"] is None


def test_summary_without_configured_fields_is_just_the_key():
    config = SimpleNamespace(key="dot_number")
    assert phase._entity_summary(_doc(), config) == {"entity_key": "12345"}


def test_extra_fields_are_merged():
    config = SimpleNamespace(key="dot_number", summary_fields=["legal_name"])
    summary = phase._entity_summary(_doc(), config, extra={"shutdown_date": "2021-05-01"})
    assert summary["shutdown_date"] == "2021-05-01"


def test_shipped_dot_config_reproduces_the_previous_field_list():
    """The block DOT ships must emit exactly what the old fixed list emitted.

    A field dropped here is a field missing from every future pair, and the
    compatibility gate compares counts rather than document shape, so nothing
    downstream would catch it.
    """
    entity = json.loads(_DOT_ENTITY_MATCH.read_text())["entity"]
    assert entity["key"] == "dot_number"
    assert entity["key_label"] == "dot_number"
    assert entity["summary_fields"] == [
        "legal_name",
        "dba_name",
        "phy_street",
        "phy_city",
        "phy_state",
    ]


def test_emitted_pair_document_under_the_shipped_dot_config():
    """End-to-end shape of a pair document built with DOT's real entity block.

    The compatibility gate compares metric counts and pair ids, neither of
    which can see a summary field that quietly stopped being emitted. This is
    the only check that the document a reviewer reads still carries what it
    carried before the summary became configuration.
    """
    shipped = json.loads(_DOT_ENTITY_MATCH.read_text())

    def _ns(block):
        return json.loads(
            json.dumps(block), object_hook=lambda d: SimpleNamespace(**d)
        )

    phase_instance = phase.PhaseEntityMatch(
        es=None,
        project="DOT-Commercial",
        one_step="chameleon-detection",
        project_config=None,
    )
    phase_instance.entity_config = _ns(shipped["entity"])
    # The dated fields below (shutdown_date, add_date, gap_days) come from the
    # lifecycle block, so loading only `entity` would test a document the
    # shipped config never produces.
    phase_instance.lifecycle = _ns(shipped["lifecycle"])

    pred = EntityDoc(
        entity_key="1",
        source={
            "legal_name": "ACME EXAMPLE",
            "dba_name": "ACME",
            "phy_street": "1 EXAMPLE WAY",
            "phy_city": "SPRINGFIELD",
            "phy_state": "IL",
            "out_of_service_orders": {"oos_date": "2025-01-01", "oos_reason": "X"},
        },
    )
    succ = EntityDoc(entity_key="2", source={"add_date": "2025-02-01"})
    pair = ScoredPair(
        predecessor=pred, successor=succ, total_score=0.9, signals_present=2
    )

    document = phase_instance._to_action(
        pair,
        "chameleon-candidates-000001",
        phase.RunProvenance(
            run_id="r",
            generated_at="2026-08-16T00:00:00+00:00",
            source_index="carriers-2026.08.13-000001",
            source_alias=None,
            analysis_fingerprint=None,
        ),
    )["_source"]

    # Every key the previous fixed list emitted, still present and populated.
    predecessor = document["predecessor"]
    assert predecessor["dot_number"] == "1"
    assert predecessor["legal_name"] == "ACME EXAMPLE"
    assert predecessor["dba_name"] == "ACME"
    assert predecessor["phy_street"] == "1 EXAMPLE WAY"
    assert predecessor["phy_city"] == "SPRINGFIELD"
    assert predecessor["phy_state"] == "IL"
    assert predecessor["shutdown_date"] == "2025-01-01"
    assert predecessor["shutdown_reason"] == "X"
    assert document["successor"]["add_date"] == "2025-02-01"
    assert document["gap_days"] == 31

    # The generic key is an addition alongside the label, not a replacement.
    assert predecessor["entity_key"] == "1"
