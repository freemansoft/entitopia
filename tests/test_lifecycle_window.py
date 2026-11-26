"""Retrieval and the scorer gate must read one window, and agree on it.

Candidate retrieval and the scorer gate once each read min_gap_days and
max_gap_days on their own. When a rule exists twice it drifts, and the drift is
silent: a candidate retrieval keeps but the scorer drops wastes a capped slot,
and one the scorer keeps but retrieval drops is lost recall with no error.

The undated rules sharpen it. An undated candidate that both retrieval and the
scorer keep is emitted by the sweep, and if a measurement also counts it as a
hit, it is counted twice in recall. These tests pin the filter and the gate to
each other so the two cannot be changed apart.
"""

import datetime
from types import SimpleNamespace

import pytest

from matching.documents import EntityDoc
from matching.window import DEFAULT_BACKWARD_WINDOW_DAYS, LifecycleWindow
from phase_providers.phase_entity_match import PhaseEntityMatch
from utils import file_utils

_SHIPPED = (
    "DOT-Commercial/configuration/chameleon-detection/entity-match.json"
)
_SHUTDOWN = datetime.date(2021, 1, 1)
_LIFECYCLE = SimpleNamespace(shutdown_date="shut", registration_date="reg")


def _window(lifecycle=_LIFECYCLE, **scoring):
    return LifecycleWindow.from_config(lifecycle, SimpleNamespace(**scoring))


def _pred(shut="2021-01-01"):
    return EntityDoc(entity_key="p", source={"shut": shut} if shut else {}, tokens={})


def _cand(**source):
    return EntityDoc(entity_key="c", source=source, tokens={})


def test_dot_config_filter_unchanged():
    config = file_utils.load_from_file(_SHIPPED)
    window = LifecycleWindow.from_config(config.lifecycle, config.scoring)
    assert window.query_filter(_SHUTDOWN) == [
        {
            "bool": {
                "should": [
                    {
                        "range": {
                            "add_date": {
                                "gte": "2020-07-05||/d",
                                "lte": "2022-01-01||/d",
                            }
                        }
                    },
                    {"bool": {"must_not": {"exists": {"field": "add_date"}}}},
                ],
                "minimum_should_match": 1,
            }
        }
    ]


def test_exclude_drops_missing_arm():
    window = _window(min_gap_days=-180, max_gap_days=365, undated_candidates="exclude")
    assert "must_not" not in str(window.query_filter(_SHUTDOWN))
    assert window.admits(_pred(), _cand()) is False


def test_keep_admits_undated():
    window = _window(min_gap_days=-180, max_gap_days=365)
    assert window.admits(_pred(), _cand()) is True


def test_end_date_inclusive():
    lifecycle = SimpleNamespace(shutdown_date="shut", registration_date="reg", end_date="ended")
    window = _window(lifecycle, min_gap_days=-180, max_gap_days=365)
    assert window.admits(_pred(), _cand(ended="2021-01-01")) is True
    assert window.admits(_pred(), _cand(ended="2020-12-31")) is False
    assert window.admits(_pred(), _cand()) is True
    assert {
        "bool": {
            "should": [
                {"range": {"ended": {"gte": "2021-01-01||/d"}}},
                {"bool": {"must_not": {"exists": {"field": "ended"}}}},
            ],
            "minimum_should_match": 1,
        }
    } in window.query_filter(_SHUTDOWN)


def _clause_passes(clause, source):
    """Evaluate the small clause vocabulary query_filter emits against a source."""
    if "range" in clause:
        ((field, bounds),) = clause["range"].items()
        value = source.get(field)
        if value is None:
            return False
        day = datetime.date.fromisoformat(value)
        if "gte" in bounds and day < datetime.date.fromisoformat(bounds["gte"].split("||")[0]):
            return False
        return not (
            "lte" in bounds and day > datetime.date.fromisoformat(bounds["lte"].split("||")[0])
        )
    if "exists" in clause:
        return source.get(clause["exists"]["field"]) is not None
    inner = clause["bool"]
    if "must_not" in inner:
        return not _clause_passes(inner["must_not"], source)
    return any(_clause_passes(c, source) for c in inner["should"])


@pytest.mark.parametrize("undated", ["keep", "exclude"])
def test_filter_and_gate_agree(undated):
    window = _window(min_gap_days=-180, max_gap_days=365, undated_candidates=undated)
    clauses = window.query_filter(_SHUTDOWN)
    gaps = [-400, -181, -180, -1, 0, 1, 365, 366, None]
    for gap in gaps:
        source = {}
        if gap is not None:
            source["reg"] = (_SHUTDOWN + datetime.timedelta(days=gap)).isoformat()
        in_filter = all(_clause_passes(c, source) for c in clauses)
        assert window.admits(_pred(), _cand(**source)) == in_filter, (undated, gap)


def test_skips_undated_predecessor():
    assert _window(undated_predecessors="skip").skips_predecessor(None) is True
    assert _window(undated_predecessors="skip").skips_predecessor(_SHUTDOWN) is False
    assert _window().skips_predecessor(None) is False


def test_backward_window_from_min_gap():
    assert _window(min_gap_days=-90).backward_window_days() == 90
    assert _window().backward_window_days() == DEFAULT_BACKWARD_WINDOW_DAYS == 180


def test_no_lifecycle_means_no_window():
    assert LifecycleWindow.from_config(None, SimpleNamespace()) is None


def test_bad_setting_is_refused_not_defaulted():
    with pytest.raises(ValueError, match="undated_candidates"):
        _window(undated_candidates="drop")


class _Selector:
    def __init__(self, hits):
        self._hits = hits

    def iterate(self):
        return iter(self._hits)


class _Finder:
    """Records find() calls so a skipped predecessor can be shown to cost none."""

    def __init__(self, window):
        self.window = window
        self.calls = []

    def find(self, pred_hit, ctx=None):
        self.calls.append(pred_hit["_id"])
        return None, [], False


def test_phase_counts_skipped_predecessor():
    finder = _Finder(_window(undated_predecessors="skip"))
    stats = {
        "predecessors": 0,
        "candidates": 0,
        "pairs": 0,
        "truncated": 0,
        "undated_predecessors": 0,
        "lookup_errors": 0,
        "scoring_errors": 0,
        "index_errors": 0,
    }
    phase = PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)
    hits = [
        {"_id": "undated", "_source": {}},
        {"_id": "dated", "_source": {"shut": "2021-01-01"}},
    ]
    list(
        phase._generate_actions(
            _Selector(hits), finder, None, None, "t", 10, {}, stats
        )
    )
    assert finder.calls == ["dated"]
    assert stats["undated_predecessors"] == 1
    assert stats["predecessors"] == 2
