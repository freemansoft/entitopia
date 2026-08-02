from types import SimpleNamespace

import pytest

from matching.documents import CarrierDoc, ScoringContext
from matching.scorer import PairScorer
from matching.signals import build_signal


def cfg(**kwargs):
    return SimpleNamespace(**kwargs)


def doc(dot_number="1", source=None, tokens=None):
    return CarrierDoc(dot_number=dot_number, source=source or {}, tokens=tokens or {})


def scoring(**overrides):
    base = {
        "min_total_score": 0.35,
        "min_signals": 2,
        "require_identity_signal": True,
        "max_pairs_per_predecessor": 10,
    }
    base.update(overrides)
    return cfg(**base)


NAME_SIGNAL = cfg(
    type="name-phonetic", weight=0.5, fields=["legal_name"], subfield="phonetic"
)
VIN_SIGNAL = cfg(
    type="vin-overlap", weight=0.5, fields=["crashes.vehicle_identification_number"]
)
AGENT_SIGNAL = cfg(type="agent", weight=0.5, name_field="boc3_agents.co_name")


def strong_pair():
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0", "TRKN"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0", "TRKN"}},
    )
    return pred, cand


def test_scores_a_strong_pair():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, cand = strong_pair()
    result = scorer.score_pair(pred, cand, ScoringContext())
    assert result is not None
    assert result.total_score == pytest.approx(1.0)
    assert result.signals_present == 2
    assert set(result.matched_on) == {"name-phonetic", "vin-overlap"}


def test_renormalizes_over_available_signals():
    # VIN is unevaluable; the name signal alone should carry the total,
    # not be diluted by the absent VIN weight.
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1))
    pred = doc("1", tokens={"legal_name.phonetic": {"SM0"}})
    cand = doc("2", tokens={"legal_name.phonetic": {"SM0"}})
    result = scorer.score_pair(pred, cand, ScoringContext())
    assert result.total_score == pytest.approx(1.0)
    assert result.signals_present == 1


def test_min_signals_guard_rejects_thin_evidence():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=2))
    pred = doc("1", tokens={"legal_name.phonetic": {"SM0"}})
    cand = doc("2", tokens={"legal_name.phonetic": {"SM0"}})
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_min_total_score_guard_rejects_weak_pairs():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_total_score=0.9))
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"SM0"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "2XYZ"}]},
        tokens={"legal_name.phonetic": {"SM0"}},
    )
    # name 1.0 * 0.5 + vin 0.0 * 0.5 = 0.5, below the 0.9 floor
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_require_identity_signal_rejects_vin_only_match():
    temporal = cfg(
        type="temporal",
        weight=0.5,
        predecessor_date="out_of_service_orders.oos_date",
        successor_date="add_date",
        max_gap_days=365,
    )
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL, temporal], scoring(min_signals=1))
    pred = doc(
        "1",
        source={
            "crashes": [{"vehicle_identification_number": "9ZZZ"}],
            "out_of_service_orders": [{"oos_date": "2022-01-01"}],
        },
    )
    cand = doc(
        "2",
        source={
            "crashes": [{"vehicle_identification_number": "8YYY"}],
            "add_date": "2022-01-01",
        },
    )
    # temporal fires perfectly and VIN is evaluable but zero. No identity
    # signal fired, so this must be rejected: 340K carriers are shut down and
    # temporal proximity alone is meaningless.
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_identity_signal_must_actually_fire_not_merely_be_evaluable():
    # Pairs the name signal with a purely corroborating one (agent), so the only
    # identity signal present is evaluable but scores 0.0. Previously this used
    # vin-overlap as the corroborating signal; a shared VIN now counts as
    # identity evidence in its own right, which would make this pair legitimately
    # pass and stop testing the guard.
    scorer = PairScorer([NAME_SIGNAL, AGENT_SIGNAL], scoring(min_signals=1, min_total_score=0.0))
    pred = doc(
        "1",
        source={"boc3_agents": {"co_name": "ACME"}},
        tokens={"legal_name.phonetic": {"AAA"}},
    )
    cand = doc(
        "2",
        source={"boc3_agents": {"co_name": "ACME"}},
        tokens={"legal_name.phonetic": {"BBB"}},
    )
    # The name signal is evaluable but scores 0.0, so no identity signal fired.
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_shared_vin_alone_satisfies_the_identity_guard():
    # The converse of the test above, and the reason vin-overlap was moved into
    # IDENTITY_SIGNAL_TYPES: a carrier that changes its name, address and phone
    # but keeps its trucks fires no other identity signal. Rejecting that pair
    # discarded exactly the profile the VIN signal exists to catch.
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1, min_total_score=0.0))
    pred = doc(
        "1",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"AAA"}},
    )
    cand = doc(
        "2",
        source={"crashes": [{"vehicle_identification_number": "1ABC"}]},
        tokens={"legal_name.phonetic": {"BBB"}},
    )
    pair = scorer.score_pair(pred, cand, ScoringContext())
    assert pair is not None
    assert pair.matched_on == ["vin-overlap"]


def test_returns_none_when_no_signal_is_evaluable():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring(min_signals=1))
    assert scorer.score_pair(doc("1"), doc("2"), ScoringContext()) is None


def test_contributions_record_per_signal_detail():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, cand = strong_pair()
    result = scorer.score_pair(pred, cand, ScoringContext())
    by_type = {c.signal_type: c for c in result.signals}
    assert by_type["name-phonetic"].weight == 0.5
    assert by_type["name-phonetic"].score == pytest.approx(1.0)
    assert by_type["name-phonetic"].contribution == pytest.approx(0.5)


def test_rejects_a_carrier_matched_against_itself():
    scorer = PairScorer([NAME_SIGNAL, VIN_SIGNAL], scoring())
    pred, _ = strong_pair()
    assert scorer.score_pair(pred, pred, ScoringContext()) is None


def test_rejects_zero_total_weight_config():
    with pytest.raises(ValueError, match="weights sum to zero"):
        PairScorer([cfg(type="name-phonetic", weight=0.0, fields=["legal_name"], subfield="phonetic")], scoring())


NAME_PHONETIC_BM = cfg(
    type="name-phonetic", weight=0.13, fields=["legal_name"], subfield="phonetic_bm"
)
NAME_TOKEN = cfg(type="name-token", weight=0.10, fields=["legal_name"], subfield="clean")


def test_signals_over_the_same_fields_share_an_evidence_key():
    # The two phonetic encoders and the cleaned form are three readings of one
    # field, deliberately listed separately so they can be weighted apart.
    keys = {build_signal(c).evidence_key for c in (NAME_SIGNAL, NAME_PHONETIC_BM, NAME_TOKEN)}
    assert len(keys) == 1


def test_signals_over_different_fields_have_different_evidence_keys():
    assert build_signal(NAME_SIGNAL).evidence_key != build_signal(VIN_SIGNAL).evidence_key


def test_min_signals_counts_evidence_not_signal_instances():
    # Three name arms over one field are one piece of evidence, so a pair
    # matching on nothing but a name must not clear a min_signals=2 floor
    # whose purpose is to demand corroboration from a second source.
    scorer = PairScorer(
        [NAME_SIGNAL, NAME_PHONETIC_BM, NAME_TOKEN], scoring(min_signals=2, min_total_score=0.0)
    )
    pred = doc("1", tokens={"legal_name.phonetic": {"SM0"}, "legal_name.phonetic_bm": {"zmit"}, "legal_name.clean": {"smith"}})
    cand = doc("2", tokens={"legal_name.phonetic": {"SM0"}, "legal_name.phonetic_bm": {"zmit"}, "legal_name.clean": {"smith"}})
    assert scorer.score_pair(pred, cand, ScoringContext()) is None


def test_name_plus_a_second_source_clears_the_floor():
    # The same three name arms DO clear it once genuinely independent
    # evidence is present, which is what the guard is actually for.
    scorer = PairScorer(
        [NAME_SIGNAL, NAME_PHONETIC_BM, NAME_TOKEN, VIN_SIGNAL],
        scoring(min_signals=2, min_total_score=0.0),
    )
    shared_vin = {"crashes": [{"vehicle_identification_number": "1ABC"}]}
    tokens = {"legal_name.phonetic": {"SM0"}, "legal_name.phonetic_bm": {"zmit"}, "legal_name.clean": {"smith"}}
    pred = doc("1", source=dict(shared_vin), tokens=dict(tokens))
    cand = doc("2", source=dict(shared_vin), tokens=dict(tokens))
    result = scorer.score_pair(pred, cand, ScoringContext())
    assert result is not None
    assert result.signals_present == 4
