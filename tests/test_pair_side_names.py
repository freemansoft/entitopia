"""A pair must not name a direction its data cannot support.

A lifecycle sweep asserts that one record followed another, so
`predecessor`/`successor` is a claim it is entitled to make. An all-entities
sweep has no dated events at all — that absence is how a duplicate-detection
project is expressed — so those names would state a succession that does not
exist. A reader pulling one pair out of the index sees only the document, not
the config behind it, and `predecessor` reads as an assertion rather than a
slot name.

The metric predicates are tested alongside, because they read a side BY NAME:
a predicate that knew only `predecessor`/`successor` would return False for
every pair emitted by an all-entities sweep, with no error and a metric
quietly reporting zero.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.documents import EntityDoc
from matching.scorer import ScoredPair
from utils.metric_predicates import evaluate

_PHASE = Path(__file__).parent.parent / "phase_providers" / "phase_entity_match.py"


def _load_phase():
    spec = importlib.util.spec_from_file_location("phase_entity_match", _PHASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase = _load_phase()


def _instance(mode):
    instance = phase.PhaseEntityMatch(
        es=None, project="p", one_step="s", project_config=None
    )
    instance.population_mode = mode
    instance.entity_config = SimpleNamespace(
        key="Facility ID", summary_fields=["Facility Name"]
    )
    return instance


def _document(mode):
    left = EntityDoc(entity_key="1", source={"Facility Name": "EXAMPLE ONE"}, tokens={})
    right = EntityDoc(entity_key="2", source={"Facility Name": "EXAMPLE TWO"}, tokens={})
    pair = ScoredPair(
        predecessor=left, successor=right, total_score=0.8, signals_present=2
    )
    return _instance(mode)._to_action(
        pair,
        "pairs-000001",
        phase.RunProvenance(
            run_id="r", generated_at="2026-08-20T00:00:00+00:00", source_index="src"
        ),
    )["_source"]


def test_lifecycle_mode_keeps_predecessor_and_successor():
    # The direction IS the claim there, and every committed DOT baseline,
    # script and README figure keys off these names.
    assert _instance("lifecycle").pair_side_names() == ("predecessor", "successor")
    document = _document("lifecycle")
    assert "predecessor" in document and "successor" in document
    assert "left" not in document


def test_all_entities_mode_names_the_sides_left_and_right():
    assert _instance("all-entities").pair_side_names() == ("left", "right")
    document = _document("all-entities")
    assert "left" in document and "right" in document
    assert "predecessor" not in document and "successor" not in document


def test_an_unknown_mode_falls_back_to_the_directional_names():
    # Only all-entities earns the neutral naming; anything else is either
    # lifecycle or a config error the validator catches first, and guessing
    # "no direction" would understate a pair that has one.
    assert _instance("something-else").pair_side_names() == ("predecessor", "successor")


def test_the_default_before_config_is_loaded_is_directional():
    bare = phase.PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)
    assert bare.pair_side_names() == ("predecessor", "successor")


@pytest.mark.parametrize(
    "left_key,right_key", [("predecessor", "successor"), ("left", "right")]
)
def test_fields_equal_reads_either_naming(left_key, right_key):
    """The silent-failure case, pinned in both directions.

    A predicate that knew only one naming would not raise on the other; it
    would return False for every pair and report a metric of zero, which is a
    plausible number and therefore the worst possible symptom.
    """
    same = {
        left_key: {"Facility Name": "EXAMPLE HOSPITAL"},
        right_key: {"Facility Name": "EXAMPLE HOSPITAL"},
    }
    differ = {
        left_key: {"Facility Name": "EXAMPLE HOSPITAL"},
        right_key: {"Facility Name": "OTHER HOSPITAL"},
    }
    assert evaluate({"fields_equal": "Facility Name"}, same) is True
    assert evaluate({"fields_equal": "Facility Name"}, differ) is False


def test_fields_equal_still_treats_null_as_not_equal_under_left_right():
    pair = {"left": {"Facility Name": None}, "right": {"Facility Name": None}}
    assert evaluate({"fields_equal": "Facility Name"}, pair) is False


def test_the_cms_config_and_mapping_agree_on_the_naming():
    """Config, mapping and mode must tell the same story.

    A mapping declaring `predecessor` under an all-entities sweep would leave
    the emitted `left` object dynamically mapped -- which works, quietly, until
    someone aggregates on it.
    """
    root = Path(__file__).parent.parent / "CMS-Providers" / "configuration" / "hospital-duplicates"
    entity_match = json.loads((root / "entity-match.json").read_text())
    mappings = json.loads((root / "index-mappings.json").read_text())
    metrics = json.loads((root / "metrics.json").read_text())

    assert entity_match["population"]["mode"] == "all-entities"
    properties = mappings["mappings"]["properties"]
    assert "left" in properties and "right" in properties
    assert "predecessor" not in properties and "successor" not in properties
    distinct = [m["distinct"] for m in metrics["metrics"] if "distinct" in m]
    assert all(path.startswith("left.") or path.startswith("right.") for path in distinct)
