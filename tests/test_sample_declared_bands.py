"""Guards the hand-review sample behind GLEIF's per-band findings.

Two failures read as success here. A sample drawn from the pool as a whole
lands almost entirely in the largest band, the low-score noise, so the bands
that decide where a reviewer's threshold sits get two or three rows each and
their "how many look real" figure is a guess. And a sample written outside a
`data/` directory is a file git will offer to commit, holding the real names
and addresses of entities the matcher paired, which the repo must never carry.
These tests pin a per-interval cap, a separate undated stratum, a seed-stable
draw, and the refusal of any other output path.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from utils.declared_measure import DEFAULT_BANDS, sample_bands

_SCRIPT = Path(__file__).parent.parent / "scripts" / "sample_declared_bands.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("sample_declared_bands", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _pair(pair_id, score, declared="none", undated=False):
    return {
        "_id": pair_id,
        "total_score": score,
        "declared": declared,
        "gap_days": None if undated else 3,
        "candidate_undated": undated,
        "predecessor": {"entity_key": "P-" + pair_id, "NAME": "INVENTED PRED"},
        "successor": {"entity_key": "S-" + pair_id, "NAME": "INVENTED SUCC"},
    }


def _pool():
    pairs = [_pair("low-{}".format(i), 0.4) for i in range(200)]
    pairs += [_pair("mid-{}".format(i), 0.75) for i in range(5)]
    pairs += [_pair("top-{}".format(i), 0.95) for i in range(20)]
    return pairs


def _bands(rows):
    counts = {}
    for row in rows:
        counts[row["band"]] = counts.get(row["band"], 0) + 1
    return counts


def test_per_band_cap_and_seed_stable():
    pool = _pool()
    rows = sample_bands(pool, DEFAULT_BANDS, per_band=15, seed=42)
    assert _bands(rows) == {"0.35-0.5": 15, "0.7-0.8": 5, "0.9+": 15}
    # Scan order is not stable across runs, so a reversed pool must give the
    # same rows, or a second reviewer cannot be handed the same sample.
    again = sample_bands(list(reversed(pool)), DEFAULT_BANDS, per_band=15, seed=42)
    assert again == rows
    other = sample_bands(pool, DEFAULT_BANDS, per_band=15, seed=7)
    assert {r["_id"] for r in other} != {r["_id"] for r in rows}
    row = rows[0]
    assert row["verdict"] is None
    assert set(row) == {
        "_id",
        "band",
        "declared",
        "total_score",
        "gap_days",
        "predecessor",
        "successor",
        "verdict",
    }


def test_undated_stratum_separate():
    """An undated pair is drawn into "undated" only, never into its score interval.

    Kept apart because the findings table reports the undated band as its own
    row: drawn into an interval as well, the same pair would be reviewed
    twice and an interval's verdicts would mix in pairs with no timing.
    """
    pool = _pool() + [_pair("u-{}".format(i), 0.95, undated=True) for i in range(30)]
    rows = sample_bands(pool, DEFAULT_BANDS, per_band=15, seed=42)
    assert _bands(rows)["undated"] == 15
    top = [r for r in rows if r["band"] == "0.9+"]
    assert all(r["_id"].startswith("top-") for r in top)
    undated = [r for r in rows if r["band"] == "undated"]
    assert all(r["_id"].startswith("u-") for r in undated)
    below_floor = [_pair("u-low", 0.2, undated=True)]
    assert sample_bands(below_floor, DEFAULT_BANDS, 15, 42) == []


def test_only_requested_declared_tag():
    pool = [_pair("t", 0.95, "truth"), _pair("e", 0.95, "explains"), _pair("n", 0.95)]
    assert [r["_id"] for r in sample_bands(pool, DEFAULT_BANDS, 15, 42)] == ["n"]
    truth = sample_bands(pool, DEFAULT_BANDS, 15, 42, declared="truth")
    assert [(r["_id"], r["declared"]) for r in truth] == [("t", "truth")]


def test_out_outside_data_refused(tmp_path):
    with pytest.raises(SystemExit, match="data/"):
        script.check_out_path(tmp_path / "review" / "sample.jsonl")
    with pytest.raises(SystemExit, match="data/"):
        script.check_out_path(Path("GLEIF-Entities") / "sample.jsonl")
    script.check_out_path(tmp_path / "data" / "review" / "sample.jsonl")


def test_count_verdicts_by_band(tmp_path):
    rows = [
        {"band": "0.9+", "verdict": "real"},
        {"band": "0.9+", "verdict": "real"},
        {"band": "0.9+", "verdict": "look-alike"},
        {"band": "undated", "verdict": "explained-missed"},
        {"band": "undated", "verdict": None},
    ]
    path = tmp_path / "data" / "sample.jsonl"
    path.parent.mkdir()
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert script.count_verdicts(path) == {
        "0.9+": {"real": 2, "explained-missed": 0, "look-alike": 1, "unreviewed": 0},
        "undated": {"real": 0, "explained-missed": 1, "look-alike": 0, "unreviewed": 1},
    }


def test_unknown_verdict_refused(tmp_path):
    """A misspelt verdict dropped from every column would shrink the sample silently."""
    path = tmp_path / "sample.jsonl"
    path.write_text(json.dumps({"band": "0.9+", "verdict": "Real"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Real"):
        script.count_verdicts(path)


def test_redraw_over_reviewed_file_refused(tmp_path):
    """Redrawing over a reviewed file would replace hand verdicts with nulls."""
    path = tmp_path / "data" / "sample.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({"band": "0.9+", "verdict": "real"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="1 verdict"):
        script.refuse_overwriting_verdicts(path)
    script.refuse_overwriting_verdicts(tmp_path / "data" / "absent.jsonl")
