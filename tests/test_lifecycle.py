"""One source of truth for the dates a pair's gap is measured between.

The two paths used to live on the temporal signal AND as literals in the phase
that computes the reported gap_days, with nothing checking they agreed. A pair
could therefore be scored on one pair of dates and reported with a gap measured
between another, and no test or log would have said so.

These pin that both now read the same config, and that a project configuring a
temporal signal without a lifecycle block fails loudly rather than getting a
signal that silently never fires.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.documents import EntityDoc, ScoringContext
from matching.scorer import PairScorer
from matching.signals import build_signal

_DOT_ENTITY_MATCH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)


def _lifecycle():
    return SimpleNamespace(shutdown_date="orders.closed", registration_date="opened")


def _signal(max_gap_days=365):
    config = SimpleNamespace(type="temporal", weight=0.05, max_gap_days=max_gap_days)
    return build_signal(config, lifecycle=_lifecycle())


def _pred(closed="2021-01-01"):
    return EntityDoc(entity_key="1", source={"orders": {"closed": closed}}, tokens={})


def _cand(opened="2021-01-31"):
    return EntityDoc(entity_key="2", source={"opened": opened}, tokens={})


def test_temporal_reads_paths_from_lifecycle():
    score = _signal().score(_pred(), _cand(), ctx=ScoringContext())
    assert score is not None
    assert 0.0 < score < 1.0


def test_temporal_scores_a_shorter_gap_higher():
    # The signal's whole premise: reopening days after a shutdown is stronger
    # evidence than reopening most of a year later.
    soon = _signal().score(_pred(), _cand("2021-01-08"), ctx=ScoringContext())
    later = _signal().score(_pred(), _cand("2021-11-01"), ctx=ScoringContext())
    assert soon > later


def test_temporal_is_unevaluable_without_both_dates():
    # None means "not evaluable", which the scorer drops and renormalizes
    # around. Returning 0.0 would penalize a record for a gap in its data.
    missing = EntityDoc(entity_key="1", source={}, tokens={})
    assert _signal().score(missing, _cand(), ctx=ScoringContext()) is None


def test_temporal_without_lifecycle_is_refused():
    # Silently scoring nothing is the failure this guards: a project that
    # configures temporal but no lifecycle would otherwise get a signal that
    # never fires and no indication why.
    config = SimpleNamespace(type="temporal", weight=0.05, max_gap_days=365)
    with pytest.raises(ValueError, match="temporal signal requires a lifecycle block"):
        build_signal(config, lifecycle=None)


def test_scorer_gap_window_reads_the_same_lifecycle():
    """The emitted-gap guard and the signal must not be able to disagree.

    Previously the guard looked up the temporal signal's own config to find
    its date paths. Reading both from one block is what makes drift
    impossible rather than merely unlikely.
    """
    scoring = SimpleNamespace(
        min_total_score=0.0,
        min_signals=1,
        require_identity_signal=False,
        min_gap_days=-180,
        max_gap_days=365,
    )
    signal_configs = [SimpleNamespace(type="temporal", weight=0.05, max_gap_days=365)]
    scorer = PairScorer(signal_configs, scoring, lifecycle=_lifecycle())

    # Two years after the shutdown is outside the window and must be dropped.
    assert scorer.score_pair(_pred(), _cand("2023-01-31"), ScoringContext()) is None
    # Inside the window survives the gate.
    assert scorer.score_pair(_pred(), _cand(), ScoringContext()) is not None


def test_unparseable_date_is_not_treated_as_outside_the_window():
    # "Not evaluable" is not "incoherent". Dropping these would discard every
    # record carrying a malformed legacy date -- a recall loss wearing a
    # precision gain's clothes.
    scoring = SimpleNamespace(
        min_total_score=0.0,
        min_signals=1,
        require_identity_signal=False,
        min_gap_days=-180,
        max_gap_days=365,
    )
    scorer = PairScorer(
        [SimpleNamespace(type="temporal", weight=0.05, max_gap_days=365)],
        scoring,
        lifecycle=_lifecycle(),
    )
    assert scorer._gap_outside_window(_pred("not-a-date"), _cand()) is False


def test_shipped_dot_lifecycle_matches_the_paths_that_were_hardcoded():
    """DOT's block must name exactly the paths the phase used as literals.

    A different path here changes which event a pair's gap describes, and the
    compatibility gate compares counts, which would not necessarily move.
    """
    config = json.loads(_DOT_ENTITY_MATCH.read_text())
    lifecycle = config["lifecycle"]
    assert lifecycle["shutdown_date"] == "out_of_service_orders.oos_date"
    assert lifecycle["registration_date"] == "add_date"
    assert lifecycle["shutdown_reason"] == "out_of_service_orders.oos_reason"

    # The temporal signal no longer carries its own copy of those paths.
    temporal = next(s for s in config["signals"] if s["type"] == "temporal")
    assert "predecessor_date" not in temporal
    assert "successor_date" not in temporal


def test_signal_type_is_still_temporal_after_the_change():
    # Guards against an accidental rename: seed_signals and matched_on key off
    # this string, and IDENTITY_SIGNAL_TYPES does not contain it.
    assert _signal().signal_type == "temporal"
    assert re.fullmatch(r"temporal", _signal().signal_type)


def test_temporal_still_contributes_exactly_one_distinct_evidence_key():
    """min_signals counts distinct evidence keys, so this could move pairs.

    Moving the date paths into the lifecycle block means the temporal signal
    names no source fields, so its evidence_key falls through from
    {"out_of_service_orders.oos_date", "add_date"} to {"temporal"}. That is
    still one key and still distinct from every other signal's, so the count
    min_signals sees is unchanged -- but it is a silent change to a guard that
    decides which pairs survive, which is worth an explicit test rather than
    an argument.
    """
    temporal = _signal()
    name = build_signal(
        SimpleNamespace(
            type="name-phonetic",
            weight=0.15,
            fields=["legal_name"],
            subfield="phonetic",
        )
    )
    assert len(temporal.evidence_key) == 1
    assert temporal.evidence_key != name.evidence_key
    assert len({temporal.evidence_key, name.evidence_key}) == 2
