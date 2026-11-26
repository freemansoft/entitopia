"""A sweep whose truth targets are never retrieved yields meaningless recall.

When the declared truth links exist but the candidate search cannot reach their
targets, every pair reads as undeclared and recall reads as zero. That used to
look like a clean run. These tests pin the three counts (declared, retrieved,
emitted), that they survive in the index _meta without erasing provenance, and
that retrieving nothing fails the run.
"""

import logging
from types import SimpleNamespace

import pytest

from matching.declared import DeclaredLinks
from matching.documents import EntityDoc, ScoringContext
from matching.scorer import ScoredPair
from matching.window import LifecycleWindow
from phase_providers.phase_entity_match import PhaseEntityMatch, RunProvenance

# _to_action reads provenance attributes, so a bare dict cannot stand in.
_PROVENANCE = RunProvenance(
    run_id="r",
    generated_at="2026-01-01T00:00:00+00:00",
    source_index="src",
    source_alias=None,
    analysis_fingerprint=None,
)


class _Selector:
    def iterate(self):
        return iter([{"_id": "A", "_source": {}}])


class _Finder:
    window = None

    def __init__(self, cands):
        self.cands = cands

    def find(self, pred_hit, ctx=None):
        return EntityDoc("A", {"NEWCERT": "B"}), self.cands, False


class _Scorer:
    def __init__(self, scores):
        self.scores = scores

    def score_pair(self, pred, cand, ctx):
        return ScoredPair(
            predecessor=pred,
            successor=cand,
            total_score=self.scores[cand.entity_key],
            signals_present=1,
        )


def _stats():
    keys = [
        "predecessors", "candidates", "pairs", "truncated",
        "undated_predecessors", "truth_links", "truth_retrieved",
        "truth_emitted", "lookup_errors", "scoring_errors", "index_errors",
    ]
    return dict.fromkeys(keys, 0)


def _phase(declared=True):
    phase = PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)
    if declared:
        rule = SimpleNamespace(name="r", role="truth", kind="key", field="NEWCERT")
        phase.declared_links = DeclaredLinks.from_config(
            SimpleNamespace(declared_links=SimpleNamespace(rules=[rule]))
        )
    return phase


def _sweep(phase, scores, max_pairs):
    cands = [EntityDoc(k, {}) for k in scores]
    stats = _stats()
    list(
        phase._generate_actions(
            _Selector(), _Finder(cands), _Scorer(scores), ScoringContext(),
            "t", max_pairs, _PROVENANCE, stats,
        )
    )
    return stats


def test_counts_retrieved_and_emitted():
    stats = _sweep(_phase(), {"B": 0.9, "C": 0.5}, max_pairs=10)
    assert (stats["truth_links"], stats["truth_retrieved"], stats["truth_emitted"]) == (1, 1, 1)


def test_cut_by_max_pairs_is_retrieved_not_emitted():
    stats = _sweep(_phase(), {"B": 0.5, "C": 0.9}, max_pairs=1)
    assert stats["truth_retrieved"] == 1
    assert stats["truth_emitted"] == 0


def test_no_truth_retrieved_raises():
    stats = _stats()
    stats["truth_links"] = 3
    with pytest.raises(RuntimeError, match="declared_links"):
        _phase()._raise_if_no_truth_retrieved(stats)


def test_no_declared_links_no_accounting():
    stats = _sweep(_phase(declared=False), {"B": 0.9}, max_pairs=10)
    assert stats["truth_links"] == stats["truth_retrieved"] == stats["truth_emitted"] == 0
    _phase(declared=False)._raise_if_no_truth_retrieved(stats)


class _Indices:
    def __init__(self, meta):
        self.meta = meta
        self.written = None

    def get_mapping(self, index):
        return {index: {"mappings": {"_meta": self.meta}}}

    def put_mapping(self, **kwargs):
        self.written = kwargs["meta"]


def test_accounting_write_keeps_existing_provenance():
    """put_mapping replaces _meta wholesale; provenance must not be lost."""
    indices = _Indices({"source_index": "src-1", "source_analysis_fingerprint": "abc"})
    phase = _phase()
    phase.es = SimpleNamespace(indices=indices)
    stats = _stats()
    stats.update(truth_links=2, truth_retrieved=1, truth_emitted=1)
    phase._record_declared_accounting("pairs", stats)
    assert indices.written["source_index"] == "src-1"
    assert indices.written["source_analysis_fingerprint"] == "abc"
    assert indices.written["declared_accounting"] == {
        "truth_links": 2, "truth_retrieved": 1, "truth_emitted": 1,
    }


def test_unreadable_meta_skips_the_write_and_logs_error(caplog):
    """An unrecognised response must not fall back to an empty _meta.

    put_mapping replaces _meta, so writing from an empty fallback would erase
    the provenance stamp silently.
    """
    indices = _Indices({})
    indices.get_mapping = lambda index: {index: {"unexpected": True}}
    phase = _phase()
    phase.es = SimpleNamespace(indices=indices)
    with caplog.at_level(logging.ERROR):
        phase._record_declared_accounting("pairs", _stats())
    assert indices.written is None
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_undated_skipped_predecessor_adds_no_truth_links():
    """A skipped predecessor was never swept, so its target cannot count."""
    window = LifecycleWindow.from_config(
        SimpleNamespace(shutdown_date="shut", registration_date="reg"),
        SimpleNamespace(undated_predecessors="skip"),
    )
    finder = _Finder([EntityDoc("B", {})])
    finder.window = window
    stats = _stats()
    list(
        _phase()._generate_actions(
            _Selector(), finder, _Scorer({"B": 0.9}), ScoringContext(),
            "t", 10, _PROVENANCE, stats,
        )
    )
    assert stats["undated_predecessors"] == 1
    assert stats["truth_links"] == 0
