"""Guards the declared-link measurement against reading as matcher failure.

A recall figure over links the sweep could never reach (a target absent from
the index, or dated outside the gap window) looks like a matcher that misses
real successions, and the number gets quoted that way. These tests pin that
such links leave the denominator and are still counted by name, and that the
output carries no entity key, since a score is an analysis and not a finding.
"""

import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.declared import split_half
from matching.documents import EntityDoc
from matching.window import LifecycleWindow
from utils import file_utils
from utils.declared_measure import LinkOutcome, PairRow, gap_histogram, summarize

REAPPEARANCE = Path("FDIC-Banks/configuration/bank-reappearance/entity-match.json")
ABSORPTION = Path("FDIC-Banks/configuration/bank-absorption/entity-match.json")


def _link(status, score=None, pred="P-1", target="T-1", gap=None):
    return LinkOutcome(pred, target, status, score, gap)


def test_exclusions_leave_denominator():
    links = [
        _link("emitted", 0.9),
        _link("emitted", 0.5),
        _link("not_retrieved"),
        _link("target_missing_from_index"),
        _link("target_outside_window"),
    ]
    result = summarize(links, [], complete_truth=False)
    assert result["denominator"] == 3
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["exclusions"] == {
        "target_missing_from_index": 1,
        "target_outside_window": 1,
        "target_undated": 0,
        "predecessor_undated": 0,
        "predecessor_not_searched": 0,
    }
    assert result["misses"] == {"not_retrieved": 1, "retrieved_not_emitted": 0}


def test_recall_by_band():
    links = [_link("emitted", 0.4), _link("emitted", 0.75)]
    by_band = summarize(links, [], complete_truth=False)["recall_by_band"]
    assert by_band["0.35"] == pytest.approx(1.0)
    assert by_band["0.7"] == pytest.approx(0.5)


def test_band_includes_its_own_score():
    links = [_link("emitted", 0.7)]
    assert summarize(links, [], False)["recall_by_band"]["0.7"] == pytest.approx(1.0)


def test_precision_only_when_complete():
    pairs = [
        PairRow("P-1", "truth", 0.8),
        PairRow("P-2", "none", 0.8),
        PairRow("P-3", "explains", 0.8),
    ]
    assert "precision_by_band" not in summarize([], pairs, complete_truth=False)
    complete = summarize([], pairs, complete_truth=True)
    assert complete["precision_by_band"]["0.7"] == pytest.approx(0.5)
    assert complete["explains_by_band"]["0.7"] == 1


def test_empty_half_still_prints_every_field():
    result = summarize([], [], complete_truth=True)
    assert result["denominator"] == 0
    assert result["recall"] is None
    assert all(v is None for v in result["recall_by_band"].values())
    assert result["precision_by_band"]["0.9"] is None


def test_unevaluable_is_not_zero():
    """Evaluated-and-missed stays 0.0; only an empty denominator is None."""
    missed = summarize([_link("not_retrieved")], [], complete_truth=False)
    assert missed["recall"] == 0.0
    pairs = [PairRow("P", "truth", 0.4)]
    precision = summarize([], pairs, complete_truth=True)["precision_by_band"]
    assert precision["0.35"] == 1.0
    assert precision["0.5"] is None


def test_unknown_status_is_refused():
    with pytest.raises(ValueError):
        summarize([_link("typo")], [], False)


def test_output_has_no_keys():
    links = [
        _link("emitted", 0.9, pred="PRED-KEY-77", target="TARGET-KEY-88"),
        _link("target_undated", pred="PRED-KEY-99", target="TARGET-KEY-11"),
    ]
    pairs = [PairRow("PRED-KEY-77", "truth", 0.9)]
    text = json.dumps(summarize(links, pairs, True)) + json.dumps(gap_histogram(links))
    for key in ("PRED-KEY-77", "TARGET-KEY-88", "PRED-KEY-99", "TARGET-KEY-11"):
        assert key not in text


def test_every_number_is_plain():
    result = summarize([_link("emitted", 0.9)], [PairRow("P", "truth", 0.9)], True)
    assert json.loads(json.dumps(result)) == result


def test_gap_histogram_bins():
    gaps = [-5000, -3650, -400, -365, -181, -180, -1, 0, 365, 366, None]
    links = [_link("emitted", 0.9, gap=g) for g in gaps]
    assert gap_histogram(links) == {
        "<-3650": 1,
        "-3650..-366": 2,
        "-365..-181": 2,
        "-180..-1": 2,
        "0..365": 2,
        ">365": 1,
    }


def test_split_half_covers_both_halves():
    halves = {split_half("K-{}".format(i)) for i in range(50)}
    assert halves == {"tune", "report"}


@pytest.mark.skipif(
    not (REAPPEARANCE.exists() and ABSORPTION.exists()),
    reason="FDIC sweep configs arrive in a later task",
)
def test_denominators_partition():
    lifecycle_windows = []
    for path in (REAPPEARANCE, ABSORPTION):
        config = file_utils.load_from_file(str(path))
        lifecycle_windows.append(
            LifecycleWindow.from_config(config.lifecycle, config.scoring)
        )
    lifecycle = lifecycle_windows[0].lifecycle
    shutdown = datetime.date(2015, 6, 1)
    pred = EntityDoc("P", {lifecycle.shutdown_date: shutdown.isoformat()})
    for gap in (-400, -30, 0, 1, 200, 400, 2000):
        registered = (shutdown + datetime.timedelta(days=gap)).isoformat()
        cand = EntityDoc("C", {lifecycle.registration_date: registered})
        admitting = [w for w in lifecycle_windows if w.admits(pred, cand)]
        assert len(admitting) == 1, "gap {} fell in {} denominators".format(
            gap, len(admitting)
        )


def test_window_decides_target_dating():
    """Document the admits contract the script's exclusion split relies on."""
    lifecycle = SimpleNamespace(
        shutdown_date="closed", registration_date="opened", end_date=None
    )
    window = LifecycleWindow(lifecycle, undated_candidates="exclude")
    pred = EntityDoc("P", {"closed": "2015-06-01"})
    assert not window.admits(pred, EntityDoc("C", {}))


# --- per-link classification, cluster-free -------------------------------

_SCRIPT = Path(__file__).parent.parent / "scripts" / "measure_declared_links.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("measure_declared_links", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()

_LIFECYCLE = SimpleNamespace(
    shutdown_date="closed", registration_date="opened", end_date=None
)


class _StubEs:
    """Answers the term lookup fetch_target makes from a dict of sources."""

    def __init__(self, sources):
        self.sources = sources

    def search(self, index, size, query, track_total_hits):
        key = query["term"]["key"]
        if key not in self.sources:
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [{"_id": key, "_source": self.sources[key]}]}}


class _StubFinder:
    """Stands in for CandidateFinder.find with a canned answer or failure."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def find(self, pred_hit, ctx):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _env(sources, finder, window, scores=None):
    return SimpleNamespace(
        es=_StubEs(sources),
        source_index="idx",
        key_field="key",
        window=window,
        finder=finder,
        ctx=None,
        scores=scores or {},
        progress={"predecessors": 7},
    )


def _window(**kwargs):
    return LifecycleWindow(
        _LIFECYCLE,
        min_gap_days=-30,
        max_gap_days=400,
        undated_candidates="exclude",
        **kwargs,
    )


def _pred(source=None):
    source = {"key": "P", "closed": "2015-06-01"} if source is None else source
    return {"_id": "P", "_source": source}, EntityDoc("P", source)


def _statuses(links):
    return [link.status for link in links]


def test_undated_target_is_target_undated():
    hit, pred = _pred()
    env = _env({"T": {"key": "T"}}, _StubFinder(), _window())
    links = script.predecessor_links(env, hit, pred, ["T"])
    assert _statuses(links) == ["target_undated"]
    assert env.finder.calls == 0


def test_out_of_window_target_is_target_outside_window():
    hit, pred = _pred()
    env = _env({"T": {"key": "T", "opened": "2020-01-01"}}, _StubFinder(), _window())
    links = script.predecessor_links(env, hit, pred, ["T"])
    assert _statuses(links) == ["target_outside_window"]
    assert links[0].gap_days > 400


def test_skipped_predecessor_yields_predecessor_undated_per_target():
    hit, pred = _pred({"key": "P"})
    window = _window(undated_predecessors="skip")
    env = _env({}, _StubFinder(), window)
    links = script.predecessor_links(env, hit, pred, ["T1", "T2"])
    assert _statuses(links) == ["predecessor_undated"] * 2


def test_missing_target_is_target_missing_from_index():
    hit, pred = _pred()
    env = _env({}, _StubFinder(), _window())
    assert _statuses(script.predecessor_links(env, hit, pred, ["T"])) == [
        "target_missing_from_index"
    ]


def test_unsearched_predecessor_is_not_a_miss():
    hit, pred = _pred()
    sources = {"T": {"key": "T", "opened": "2015-07-01"}}
    env = _env(sources, _StubFinder(result=(None, [], False)), _window())
    links = script.predecessor_links(env, hit, pred, ["T"])
    assert _statuses(links) == ["predecessor_not_searched"]
    result = summarize(links, [], False)
    assert result["denominator"] == 0
    assert result["exclusions"]["predecessor_not_searched"] == 1


def test_emitted_unemitted_and_not_retrieved():
    hit, pred = _pred()
    sources = {
        "T1": {"key": "T1", "opened": "2015-07-01"},
        "T2": {"key": "T2", "opened": "2015-07-02"},
        "T3": {"key": "T3", "opened": "2015-07-03"},
    }
    found = (pred, [EntityDoc("T1", {}), EntityDoc("T2", {})], False)
    pair_id = script.id_utils.compute_id({"p": "P", "s": "T1"}, ["p", "s"])
    env = _env(sources, _StubFinder(result=found), _window(), {pair_id: 0.8})
    links = script.predecessor_links(env, hit, pred, ["T1", "T2", "T3"])
    assert _statuses(links) == ["emitted", "retrieved_not_emitted", "not_retrieved"]
    assert links[0].score == 0.8


def test_failed_search_aborts_and_names_progress():
    hit, pred = _pred()
    sources = {"T": {"key": "T", "opened": "2015-07-01"}}
    env = _env(sources, _StubFinder(error=ValueError("boom")), _window())
    with pytest.raises(RuntimeError, match=r"after 7 predecessors.*incomplete"):
        script.predecessor_links(env, hit, pred, ["T"])
