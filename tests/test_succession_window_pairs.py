"""Guards a partitioned pair of sweeps against drifting apart.

Two sweeps split the time between a predecessor's shutdown and its successor's
creation at one overlap value, so every succession is searched by exactly one
of them. Configs edited apart break that quietly in either direction: windows
that overlap count a pair twice (inflating pairs and recall), and windows with
a gap leave a succession in neither, so recall reads low and nothing reports it.
The sweeps must also agree on the lifecycle, population, declared links, ignored
values, source index and entity key, or their denominators measure different
things and cannot be added together.
"""

from pathlib import Path

import pytest

from utils import file_utils

PAIRS = [
    ("FDIC-Banks", "bank-reappearance", "bank-absorption"),
    ("GLEIF-Entities", "lei-reappearance", "lei-absorption"),
    ("GLEIF-Entities", "lei-reappearance-de", "lei-absorption-de"),
]


def _load(project, step):
    path = Path(project) / "configuration" / step / "entity-match.json"
    return file_utils.load_from_file(str(path))


def _temporal(config):
    return [s for s in config.signals if s.type == "temporal"]


@pytest.mark.parametrize("project,reappearance_step,absorption_step", PAIRS)
class TestWindowPair:
    def test_windows_abut_with_no_overlap_or_gap(
        self, project, reappearance_step, absorption_step
    ):
        reappearance = _load(project, reappearance_step)
        absorption = _load(project, absorption_step)
        assert (
            absorption.scoring.max_gap_days == reappearance.scoring.min_gap_days - 1
        )

    def test_overlap_is_the_measured_starting_value(
        self, project, reappearance_step, absorption_step
    ):
        assert _load(project, reappearance_step).scoring.min_gap_days == -180

    def test_undated_predecessors_are_handled_alike(
        self, project, reappearance_step, absorption_step
    ):
        reappearance = _load(project, reappearance_step).scoring
        absorption = _load(project, absorption_step).scoring
        assert reappearance.undated_predecessors == absorption.undated_predecessors

    def test_absorption_is_unbounded_below(
        self, project, reappearance_step, absorption_step
    ):
        assert not hasattr(_load(project, absorption_step).scoring, "min_gap_days")

    def test_undated_candidates_belong_to_exactly_one_sweep(
        self, project, reappearance_step, absorption_step
    ):
        assert _load(project, reappearance_step).scoring.undated_candidates == "exclude"
        assert _load(project, absorption_step).scoring.undated_candidates == "keep"

    # ignore_values, source_index and entity feed classify and the denominators
    # as directly as the declared rules do: a placeholder ignored in one sweep
    # but not the other, or a different key column or index, would make the
    # two tag and count different link sets while each still looked sound.
    @pytest.mark.parametrize(
        "block",
        [
            "lifecycle",
            "population",
            "declared_links",
            "ignore_values",
            "source_index",
            "entity",
        ],
    )
    def test_shared_blocks_are_identical(
        self, project, reappearance_step, absorption_step, block
    ):
        assert getattr(_load(project, reappearance_step), block) == getattr(
            _load(project, absorption_step), block
        )

    def test_temporal_signal_only_where_dates_follow_shutdown(
        self, project, reappearance_step, absorption_step
    ):
        temporal = _temporal(_load(project, reappearance_step))
        assert len(temporal) == 1
        assert temporal[0].before_shutdown_scale == 1.0
        assert _temporal(_load(project, absorption_step)) == []
