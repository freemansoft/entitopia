"""The metrics config, checked structurally and against the predicate menu.

A ratio naming a metric that does not exist is NOT checked here — it is a
relationship between two entries, which a schema cannot see, and it belongs to
the coherence tier. Adding it here as well would mean two places to fix when
the rule changes.
"""

import copy
import json
from pathlib import Path

from utils import config_schema
from utils.metric_predicates import PREDICATES

_SHIPPED_PATH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "metrics.json"
)
_SHIPPED = json.loads(_SHIPPED_PATH.read_text())
_SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "metrics.schema.json"


def test_the_shipped_metrics_config_validates():
    assert config_schema.validate_mapping("metrics", _SHIPPED, "shipped") == []


def test_an_unknown_predicate_name_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["metrics"][1]["filter"] = {"score_greater": 0.7}
    errors = config_schema.validate_mapping("metrics", raw, "mutated")
    assert errors


def test_an_unknown_predicate_nested_inside_all_is_rejected():
    # The recursive $ref must apply the closed menu at every depth, not only at
    # the top level -- most real filters are nested inside an `all`.
    raw = copy.deepcopy(_SHIPPED)
    raw["metrics"][2]["filter"] = {"all": [{"score_gte": 0.7}, {"gap_bewteen": [0, 1]}]}
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_a_metric_without_a_name_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    del raw["metrics"][0]["name"]
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_a_metric_that_is_both_distinct_and_ratio_is_rejected():
    # A metric counts pairs, counts distinct values, or divides two others.
    # Two at once has no defined meaning and the runner would silently pick.
    raw = copy.deepcopy(_SHIPPED)
    raw["metrics"].append(
        {
            "name": "confused",
            "distinct": "predecessor.entity_key",
            "ratio": {"numerator": "pairs", "denominator": "pairs"},
        }
    )
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_a_gap_between_with_one_bound_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["metrics"][2]["filter"] = {"gap_between": [0]}
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_a_ratio_missing_its_denominator_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    del raw["metrics"][3]["ratio"]["denominator"]
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_an_unknown_top_level_key_is_rejected():
    raw = copy.deepcopy(_SHIPPED)
    raw["metrix"] = []
    assert config_schema.validate_mapping("metrics", raw, "mutated")


def test_the_schema_predicate_enum_matches_the_implementation():
    """The enum must not drift from utils.metric_predicates.

    A predicate implemented in code but missing from the schema would be
    rejected as unknown -- a validator refusing a legitimate config, which is
    the failure that teaches operators to distrust it. One missing from code
    but present here fails at run time instead of at validation.
    """
    schema = json.loads(_SCHEMA_PATH.read_text())
    declared = set(schema["$defs"]["predicate"]["properties"])
    assert declared == set(PREDICATES)


def test_the_shipped_metric_names_match_the_committed_baseline():
    """Every metric this project declares must be a key of its own baseline.

    A name that drifts from the baseline is not caught by any schema: the
    comparison would simply raise on a missing key, hours after a sweep.
    """
    baseline_path = (
        Path(__file__).parent.parent / "DOT-Commercial" / _SHIPPED["baseline"]
    )
    baseline = json.loads(baseline_path.read_text())
    declared = {metric["name"] for metric in _SHIPPED["metrics"]}
    assert declared == set(baseline)
