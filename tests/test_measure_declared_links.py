"""Guards the declared-link measurement against reading as matcher failure.

A recall figure over links the sweep could never reach (a target absent from
the index, or dated outside the gap window) looks like a matcher that misses
real successions, and the number gets quoted that way. These tests pin that
such links leave the denominator and are still counted by name, and that the
output carries no entity key, since a score is an analysis and not a finding.
"""

import datetime
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
    assert result["recall"] == 0.0
    assert result["precision_by_band"]["0.9"] == 0.0


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
