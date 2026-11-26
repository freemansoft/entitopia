"""Declared successions tag pairs truth/explains/none.

A declared path that never fires makes every pair read as undeclared and recall
read as zero, which looks like a bad matcher rather than bad configuration.
These tests pin that each rule kind fires, that ignored placeholders never do,
and that the tuning/reporting split is stable across runs. They also pin three
refusals that keep a tag from being quietly wrong: a truth rule recall cannot
count (shared or reverse) does not load, a missing key is not hashed into a
half, and the pair writer will not tag without the scoring context that
carries ignore_values.
"""

from types import SimpleNamespace

import pytest

from matching.declared import DeclaredLinks, split_half
from matching.documents import EntityDoc, ScoringContext
from matching.scorer import ScoredPair
from phase_providers.phase_entity_match import PhaseEntityMatch, RunProvenance
from utils import config_liveness


def ns(**kw):
    return SimpleNamespace(**kw)


def links(*rules, complete_truth=False):
    return DeclaredLinks.from_config(
        ns(declared_links=ns(complete_truth=complete_truth, rules=[ns(**r) for r in rules]))
    )


SUCCESSOR = {"name": "fdic-successor", "role": "truth", "kind": "key", "field": "NEWCERT"}
CHAIN = {"name": "fdic-chain-end", "role": "explains", "kind": "key", "field": "ULTCERT"}
CTX = ScoringContext()

# Invented CERTs in a range no FDIC institution uses (checked against the loaded
# index), so a fixture can never name a real bank. Labelled CERTs are what the
# anonymization scanner looks up, and these must come back NOT_FOUND.


def test_key_rule_truth():
    pred = EntityDoc("990000", {"NEWCERT": "990002"})
    cand = EntityDoc("990002", {})
    assert links(SUCCESSOR, CHAIN).classify(pred, cand, CTX) == ("truth", "fdic-successor")


def test_truth_beats_explains():
    pred = EntityDoc("990000", {"NEWCERT": "990002", "ULTCERT": "990002"})
    cand = EntityDoc("990002", {})
    assert links(CHAIN, SUCCESSOR).classify(pred, cand, CTX)[0] == "truth"


def test_explains_chain():
    pred = EntityDoc("990000", {"NEWCERT": "990001", "ULTCERT": "990002"})
    cand = EntityDoc("990002", {})
    assert links(SUCCESSOR, CHAIN).classify(pred, cand, CTX) == ("explains", "fdic-chain-end")


def test_ignored_value_never_fires():
    ctx = ScoringContext(ignored_values={"NEWCERT": {"0"}})
    pred = EntityDoc("1", {"NEWCERT": "0"})
    declared = links(SUCCESSOR)
    assert declared.classify(pred, EntityDoc("0", {}), ctx) == ("none", None)
    assert declared.truth_targets(pred, ctx) == set()


def test_reverse_key_rule():
    rule = {"name": "child", "role": "explains", "kind": "key", "field": "parent", "reverse": True}
    pred = EntityDoc("P1", {})
    assert links(rule).classify(pred, EntityDoc("C1", {"parent": "P1"}), CTX)[0] == "explains"
    assert links(rule).classify(pred, EntityDoc("C1", {"parent": "other"}), CTX)[0] == "none"


@pytest.mark.parametrize(
    "override",
    [{"kind": "shared"}, {"reverse": True}],
    ids=["shared", "reverse"],
)
def test_truth_rule_recall_cannot_see_is_refused(override):
    """A truth rule that names no target from the predecessor must not load.

    classify would tag its pairs truth, but truth_targets cannot list them, so
    the accounting, the recall denominator and the no-truth guards would never
    see the links: recall would be counted over a different set of links than
    the one tagged.
    """
    with pytest.raises(ValueError, match="forward key"):
        links({**SUCCESSOR, **override})


def test_reverse_on_shared_rule_is_rejected():
    rule = {"name": "hc", "role": "explains", "kind": "shared", "field": "hc", "reverse": True}
    with pytest.raises(ValueError, match="reverse"):
        links(rule)


def test_shared_rule():
    rule = {"name": "holding", "role": "explains", "kind": "shared", "field": "hc"}
    declared = links(rule)
    assert declared.classify(EntityDoc("1", {"hc": "77"}), EntityDoc("2", {"hc": "77"}), CTX) == (
        "explains",
        "holding",
    )
    assert declared.classify(EntityDoc("1", {"hc": ""}), EntityDoc("2", {"hc": ""}), CTX)[0] == "none"
    assert declared.classify(EntityDoc("1", {}), EntityDoc("2", {}), CTX)[0] == "none"


def test_no_block_means_no_links():
    assert DeclaredLinks.from_config(ns()) is None


def test_split_half_stable():
    # Literals computed once with hashlib in a scratch shell, not with this
    # function's own expression, so a change to the hash cannot pass silently.
    assert split_half("990005") == "tune"
    assert split_half("990002") == "report"
    assert split_half("ZZZZ00INVENTEDLEI000") == "tune"
    tune = sum(split_half(str(i)) == "tune" for i in range(10000))
    assert 4800 < tune < 5200


@pytest.mark.parametrize("key", [None, "", "  "], ids=["none", "empty", "blank"])
def test_split_half_refuses_a_missing_key(key):
    """A missing key must not be hashed into a half.

    str(None) is "None", which hashes to one fixed half, so every pair whose
    predecessor key went missing would pile into it and be measured as if it
    were one real predecessor.
    """
    with pytest.raises(ValueError, match="entity key"):
        split_half(key)


def _pair():
    pred = EntityDoc("990000", {"NEWCERT": "990002"})
    return ScoredPair(
        predecessor=pred, successor=EntityDoc("990002", {}), total_score=0.9, signals_present=1
    )


def _action(declared, ctx=CTX):
    phase = PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)
    phase.declared_links = declared
    phase.scoring_context = ctx
    prov = RunProvenance(
        run_id="r",
        generated_at="t",
        source_index="i",
        source_alias=None,
        analysis_fingerprint=None,
    )
    return phase._to_action(_pair(), "idx", prov)["_source"]


def test_pair_document_carries_tag():
    doc = _action(links(SUCCESSOR))
    assert doc["declared"] == "truth"
    assert doc["declared_by"] == "fdic-successor"


def test_pair_document_without_declared_links_has_neither_key():
    doc = _action(None)
    assert "declared" not in doc
    assert "declared_by" not in doc


def test_untagged_pair_has_declared_none_and_no_rule():
    doc = _action(links(CHAIN))
    assert doc["declared"] == "none"
    assert "declared_by" not in doc


def test_tagging_without_a_scoring_context_fails_loudly():
    """Tagging with no context would let ignored placeholders fire.

    An empty fallback context carries no ignore_values, so a NEWCERT of "0"
    would link every closed bank to whatever is keyed "0" and the pair would be
    written as truth. Only a change in call order could get here, and it must
    surface as an error rather than as quietly wrong tags.
    """
    with pytest.raises(RuntimeError, match="scoring context"):
        _action(links(SUCCESSOR), ctx=None)


def test_no_declared_links_needs_no_scoring_context():
    # DOT-Commercial and CMS-Providers declare no links; their pair writer must
    # not start demanding a context it never used.
    assert "declared" not in _action(None, ctx=None)


class _Indices:
    def get_mapping(self, index=None):
        return {"i": {"mappings": {"properties": {"dot_number": {"type": "keyword"}}}}}


class _Es:
    indices = _Indices()


def test_liveness_reports_missing_rule_field():
    raw = {
        "source_index": "i",
        "entity": {"key": "dot_number"},
        "declared_links": {"rules": [{**SUCCESSOR, "field": "NEWCERT_GONE"}]},
    }
    assert any("NEWCERT_GONE" in e for e in config_liveness.check(_Es(), raw, "t"))


@pytest.mark.parametrize(
    "override", [{"role": "truths"}, {"kind": "chain"}], ids=["role", "kind"]
)
def test_unknown_role_or_kind_is_refused(override):
    # A typo must fail at load, not leave a rule that never fires.
    with pytest.raises(ValueError, match="role must be one of"):
        links({**SUCCESSOR, **override})
