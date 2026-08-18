"""The scan's _source list is derived from config, not hardcoded.

A hardcoded field list is how a scan silently stops feeding a newly added
predicate: the field is absent from every fetched document, the predicate reads
it as missing, and the metric reports a smaller number than the data supports
with nothing raising. Restricting the scan is still necessary at this scale, so
the fix is to derive the list rather than to stop restricting.
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_metrics.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_metrics", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_metrics = _load()


def test_the_always_read_fields_are_always_requested():
    fields = run_metrics.source_fields([{"name": "pairs"}])
    for name in ("total_score", "gap_days", "matched_on", "signals"):
        assert name in fields


def test_fields_equal_pulls_both_sides_of_the_pair():
    fields = run_metrics.source_fields(
        [{"name": "m", "filter": {"fields_equal": "legal_name"}}]
    )
    assert "predecessor.legal_name" in fields
    assert "successor.legal_name" in fields


def test_a_nested_fields_equal_is_found():
    # Most real filters wrap their clauses in an `all`; a walker that only
    # looked at the top level would miss nearly every one.
    fields = run_metrics.source_fields(
        [
            {
                "name": "m",
                "filter": {
                    "all": [{"score_gte": 0.7}, {"fields_equal": "legal_name"}]
                },
            }
        ]
    )
    assert "predecessor.legal_name" in fields


def test_a_fields_equal_under_not_is_found():
    fields = run_metrics.source_fields(
        [{"name": "m", "filter": {"not": {"fields_equal": "dba_name"}}}]
    )
    assert "predecessor.dba_name" in fields


def test_a_distinct_path_is_requested():
    fields = run_metrics.source_fields(
        [{"name": "m", "distinct": "predecessor.entity_key"}]
    )
    assert "predecessor.entity_key" in fields


def test_the_shipped_dot_config_pulls_everything_its_metrics_read():
    """The real config, checked field by field rather than in aggregate."""
    path = (
        Path(__file__).parent.parent
        / "DOT-Commercial"
        / "configuration"
        / "chameleon-detection"
        / "metrics.json"
    )
    fields = run_metrics.source_fields(json.loads(path.read_text())["metrics"])
    assert "predecessor.legal_name" in fields
    assert "successor.legal_name" in fields
    assert "predecessor.entity_key" in fields
    assert "total_score" in fields
