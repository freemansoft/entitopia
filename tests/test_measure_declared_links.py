"""Guards the declared-link measurement against reading as matcher failure.

A recall figure over links the sweep could never reach (a target absent from
the index, or dated outside the gap window) looks like a matcher that misses
real successions, and the number gets quoted that way. These tests pin that
such links leave the denominator and are still counted by exclusion reason,
and that the output carries no entity key, since a score is an analysis and not
a finding. A pair index from another step, from a superseded source index, or
swept without declared links is refused: the re-run search would otherwise be
joined to pairs from a sweep that never ran.
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
from utils.declared_measure import (
    FINE_GAP_EDGES,
    LinkOutcome,
    PairRow,
    gap_histogram,
    summarize,
)

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


def test_denominators_partition():
    """Each succession lands in exactly one sweep's denominator, up to max_gap_days.

    A pair counted by both sweeps inflates recall, and one counted by neither
    is a succession no sweep can find, so the gaps either side of the seam
    (-181 and -180) and the edges (0, +365) are probed. A gap above the
    reappearance max_gap_days (+365) is in neither window by design: too late
    to be a reappearance and not before the shutdown, so not an absorption. The
    claim therefore covers successor creation dates up to max_gap_days only.
    An undated candidate is probed too: it must land in absorption alone.
    """
    lifecycle_windows = []
    for path in (REAPPEARANCE, ABSORPTION):
        config = file_utils.load_from_file(str(path))
        lifecycle_windows.append(
            LifecycleWindow.from_config(config.lifecycle, config.scoring)
        )
    max_gap = file_utils.load_from_file(str(REAPPEARANCE)).scoring.max_gap_days
    lifecycle = lifecycle_windows[0].lifecycle
    shutdown = datetime.date(2015, 6, 1)
    pred = EntityDoc("P", {lifecycle.shutdown_date: shutdown.isoformat()})

    def admitting(gap):
        registered = (shutdown + datetime.timedelta(days=gap)).isoformat()
        cand = EntityDoc("C", {lifecycle.registration_date: registered})
        return [w for w in lifecycle_windows if w.admits(pred, cand)]

    for gap in (-3000, -400, -181, -180, -30, 0, 1, 200, max_gap):
        assert len(admitting(gap)) == 1, "gap {} fell in {} denominators".format(
            gap, len(admitting(gap))
        )
    for gap in (max_gap + 1, 2000):
        assert admitting(gap) == [], "gap {} should be in neither window".format(gap)

    # An undated candidate has no gap to place it, so only undated_candidates
    # decides; both sweeps keeping it would emit it twice, neither would lose it.
    undated = EntityDoc("C", {})
    admitted = [w for w in lifecycle_windows if w.admits(pred, undated)]
    assert admitted == [lifecycle_windows[1]], "undated candidate must be absorption's"


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


# --- the pair index must be the sweep being reproduced --------------------

_ACCOUNTING = {"truth_links": 4, "truth_retrieved": 2, "truth_emitted": 1}
_TEMPLATE = "bank-reappearance-pairs-{now/d}-000001"
_PAIRS = "bank-reappearance-pairs-2026.11.26-000011"


class _MetaIndices:
    """Answers get_mapping and get_alias the way the measurement reads them."""

    def __init__(self, pair_meta, pair_index=_PAIRS, aliases=None):
        self.pair_meta = pair_meta
        self.pair_index = pair_index
        self.aliases = aliases if aliases is not None else {"src-alias": ["src-2"]}

    def get_mapping(self, index):
        return {self.pair_index: {"mappings": {"_meta": self.pair_meta}}}

    def get_alias(self, name):
        return {index: {} for index in self.aliases.get(name, [])}


def _check(meta, **kwargs):
    es = SimpleNamespace(indices=_MetaIndices(meta, **kwargs))
    return script.check_pair_index(
        es, "bank-reappearance-pairs-000001", "src-alias", _TEMPLATE
    )


def test_matching_pair_index_is_accepted():
    meta = {"source_index": "src-2", "declared_accounting": _ACCOUNTING}
    assert _check(meta) == _PAIRS


def test_pair_index_from_a_superseded_source_is_refused():
    """Recall over a source the sweep never read describes no real run.

    The candidate search re-runs against whatever the alias names today, and
    its result is joined to the pair index by _id, so after a reload the miss
    split compares a search over new documents with pairs from old ones.
    """
    meta = {"source_index": "src-1", "declared_accounting": _ACCOUNTING}
    with pytest.raises(SystemExit, match=r"src-1.*src-2"):
        _check(meta)


def test_pair_index_without_declared_accounting_is_refused():
    """A sweep run without declared links tags nothing, so precision reads 0.0."""
    with pytest.raises(SystemExit, match="declared_accounting"):
        _check({"source_index": "src-2"})


def test_another_steps_pair_index_is_refused():
    meta = {"source_index": "src-2", "declared_accounting": _ACCOUNTING}
    with pytest.raises(SystemExit, match="bank-absorption-pairs"):
        _check(meta, pair_index="bank-absorption-pairs-2026.11.26-000009")


def test_concrete_source_index_is_compared_as_named():
    # A project may configure a concrete index rather than an alias.
    meta = {"source_index": "src-alias", "declared_accounting": _ACCOUNTING}
    assert _check(meta, aliases={}) == _PAIRS


# --- a pair with no predecessor key --------------------------------------


def _hits(*sources):
    return [{"_id": str(i), "_source": s} for i, s in enumerate(sources)]


def test_pair_with_no_predecessor_key_is_refused(monkeypatch):
    """Hashing str(None) would file every such pair under one fixed half."""
    hits = _hits({"total_score": 0.9, "declared": "none", "predecessor": {}})
    monkeypatch.setattr(script, "scan", lambda *a, **k: iter(hits))
    with pytest.raises(ValueError, match=r"predecessor\.entity_key"):
        script.read_pairs(None, "idx", "predecessor", "successor", False)


def test_all_entities_pairs_are_read_by_their_own_side_name(monkeypatch):
    # An all-entities sweep writes left/right, so reading `predecessor` would
    # find no key on any pair.
    hits = _hits({"total_score": 0.9, "declared": "truth", "left": {"entity_key": "L1"}})
    monkeypatch.setattr(script, "scan", lambda *a, **k: iter(hits))
    scores, rows = script.read_pairs(None, "idx", "left", "right", False)
    assert rows == [PairRow("L1", "truth", 0.9)]
    assert scores == {"0": 0.9}


# --- band and undated counts, added for GLEIF ------------------------------


def test_existing_keys_unchanged():
    """FDIC's quoted report-half figures must not move when keys are added.

    The literals were captured from summarize before the band and undated
    counts existed. Every key part 1 defined is compared whole, so a change
    that quietly re-cut a band or re-defined a denominator fails here even
    though the new keys beside it are correct.
    """
    links = [
        LinkOutcome("P1", "T1", "emitted", 0.95, 0),
        LinkOutcome("P2", "T2", "emitted", 0.62, -40),
        LinkOutcome("P3", "T3", "emitted", 0.41, 3),
        LinkOutcome("P4", "T4", "not_retrieved", None, -200),
        LinkOutcome("P5", "T5", "retrieved_not_emitted", None, 10),
        LinkOutcome("P6", "T6", "target_missing_from_index"),
        LinkOutcome("P7", "T7", "target_outside_window", None, -4000),
        LinkOutcome("P8", "T8", "target_undated", target_undated=True),
        LinkOutcome("P9", "T9", "predecessor_undated"),
        LinkOutcome("P10", "T10", "predecessor_not_searched", None, 5),
    ]
    pairs = [
        PairRow("P1", "truth", 0.95),
        PairRow("P2", "truth", 0.62),
        PairRow("P3", "none", 0.85, candidate_undated=True),
        PairRow("P4", "none", 0.55),
        PairRow("P5", "explains", 0.72),
        PairRow("P6", "explains", 0.36),
        PairRow("P7", "none", 0.2),
        PairRow("P8", "truth", 0.41),
    ]
    result = summarize(links, pairs, complete_truth=True)
    expected = {
        "denominator": 5,
        "exclusions": {
            "target_missing_from_index": 1,
            "target_outside_window": 1,
            "target_undated": 1,
            "predecessor_undated": 1,
            "predecessor_not_searched": 1,
        },
        "recall": 0.6,
        "recall_by_band": {
            "0.35": 0.6,
            "0.5": 0.4,
            "0.6": 0.4,
            "0.7": 0.2,
            "0.8": 0.2,
            "0.9": 0.2,
        },
        "misses": {"not_retrieved": 1, "retrieved_not_emitted": 1},
        "explains_by_band": {
            "0.35": 2,
            "0.5": 1,
            "0.6": 1,
            "0.7": 1,
            "0.8": 0,
            "0.9": 0,
        },
        "precision_by_band": {
            "0.35": 0.6,
            "0.5": 0.5,
            "0.6": 0.6666666666666666,
            "0.7": 0.5,
            "0.8": 0.5,
            "0.9": 1.0,
        },
    }
    for key, value in expected.items():
        assert result[key] == value, key
    assert set(result) - set(expected) == {"undated", "pairs_in_band"}
    assert gap_histogram(links) == {
        "<-3650": 1,
        "-3650..-366": 0,
        "-365..-181": 1,
        "-180..-1": 1,
        "0..365": 4,
        ">365": 0,
    }


def test_undated_targets_counted_both_ways():
    """An undated successor counts in a denominator, and its exclusions show too.

    Absorption keeps undated candidates, so their truth links are measured
    there and excluded from reappearance as target_undated. Reporting only one
    side would hide how much of a recall figure rests on links with no date.
    """
    links = [
        LinkOutcome("P", "T1", "emitted", 0.8, target_undated=True),
        LinkOutcome("P", "T2", "not_retrieved", target_undated=True),
        LinkOutcome("P", "T3", "target_undated", target_undated=True),
        _link("emitted", 0.9),
    ]
    undated = summarize(links, [], complete_truth=False)["undated"]
    assert undated["target_links_measured"] == 2
    assert undated["target_links_excluded"] == 1


def test_pairs_in_band_are_intervals():
    pairs = [
        PairRow("P1", "truth", 0.55),
        PairRow("P2", "none", 0.95),
        PairRow("P3", "none", 0.2),
    ]
    in_band = summarize([], pairs, complete_truth=False)["pairs_in_band"]
    assert list(in_band) == [
        "0.35-0.5",
        "0.5-0.6",
        "0.6-0.7",
        "0.7-0.8",
        "0.8-0.9",
        "0.9+",
    ]
    empty = {"truth": 0, "explains": 0, "none": 0, "none_undated": 0}
    assert in_band["0.5-0.6"] == {**empty, "truth": 1}
    assert in_band["0.9+"] == {**empty, "none": 1}
    others = [label for label in in_band if label not in ("0.5-0.6", "0.9+")]
    assert all(in_band[label] == empty for label in others)


def test_interval_lower_edge_is_inclusive():
    """A score on an edge belongs to the interval above it, as in recall_by_band."""
    in_band = summarize([], [PairRow("P", "none", 0.6)], False)["pairs_in_band"]
    assert in_band["0.6-0.7"]["none"] == 1
    assert in_band["0.5-0.6"]["none"] == 0


def test_undated_pairs_by_band():
    """Undated pairs are cumulative like recall_by_band; none_undated is per interval.

    none_undated is a subset of none, so the truth share a reader computes
    per interval covers every pair, and the dated findings are the difference.
    """
    pairs = [
        PairRow("P1", "none", 0.4, candidate_undated=True),
        PairRow("P2", "none", 0.75, candidate_undated=True),
        PairRow("P3", "truth", 0.95, candidate_undated=True),
        PairRow("P4", "none", 0.75),
        PairRow("P5", "none", 0.1, candidate_undated=True),
    ]
    result = summarize([], pairs, complete_truth=False)
    assert result["undated"]["pairs_by_band"] == {
        "0.35": 3,
        "0.5": 2,
        "0.6": 2,
        "0.7": 2,
        "0.8": 1,
        "0.9": 1,
    }
    assert result["pairs_in_band"]["0.7-0.8"]["none"] == 2
    assert result["pairs_in_band"]["0.7-0.8"]["none_undated"] == 1
    assert result["pairs_in_band"]["0.9+"]["none_undated"] == 0


def test_fine_gap_bins_match_fdic_table():
    """The bins of FDIC-Banks/README.md's "Choosing overlap_days" table, in order.

    That table was computed by a one-off because the coarse bins could not show
    the valley. Pinning one gap per bin keeps the script able to reproduce it.
    """
    gaps = [-3651, -1096, -366, -181, -91, -61, -31, -8, -1, 0, 1, 7, 366]
    histogram = gap_histogram([_link("emitted", 0.9, gap=g) for g in gaps], FINE_GAP_EDGES)
    assert list(histogram) == [
        "<-3650",
        "-3650..-1096",
        "-1095..-366",
        "-365..-181",
        "-180..-91",
        "-90..-61",
        "-60..-31",
        "-30..-8",
        "-7..-1",
        "0",
        "1..6",
        "7..365",
        ">365",
    ]
    assert list(histogram.values()) == [1] * len(gaps)


def test_fetched_undated_target_is_flagged_whatever_its_status():
    hit, pred = _pred()
    sources = {"T1": {"key": "T1"}, "T2": {"key": "T2", "opened": "2015-07-01"}}
    keep = LifecycleWindow(
        _LIFECYCLE, min_gap_days=-30, max_gap_days=400, undated_candidates="keep"
    )
    found = (pred, [EntityDoc("T1", {})], False)
    env = _env(sources, _StubFinder(result=found), keep)
    links = script.predecessor_links(env, hit, pred, ["T1", "T2", "T3"])
    flags = {link.target_key: (link.status, link.target_undated) for link in links}
    assert flags == {
        "T3": ("target_missing_from_index", False),
        "T1": ("retrieved_not_emitted", True),
        "T2": ("not_retrieved", False),
    }
    excluded = script.predecessor_links(_env(sources, _StubFinder(), _window()), hit, pred, ["T1"])
    assert [(link.status, link.target_undated) for link in excluded] == [
        ("target_undated", True)
    ]


def test_candidate_undated_only_when_the_step_is_dated(monkeypatch):
    """A step with no registration_date has no add_date to miss.

    Flagging its every pair undated would report the whole sweep as the
    undated band.
    """
    hits = _hits(
        {"total_score": 0.9, "predecessor": {"entity_key": "P1"}, "successor": {}},
        {
            "total_score": 0.8,
            "predecessor": {"entity_key": "P2"},
            "successor": {"add_date": "2015-07-01"},
        },
    )
    monkeypatch.setattr(script, "scan", lambda *a, **k: iter(hits))
    _, dated = script.read_pairs(None, "idx", "predecessor", "successor", True)
    assert [row.candidate_undated for row in dated] == [True, False]
    _, undated = script.read_pairs(None, "idx", "predecessor", "successor", False)
    assert [row.candidate_undated for row in undated] == [False, False]
