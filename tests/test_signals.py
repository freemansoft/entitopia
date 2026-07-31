from types import SimpleNamespace

import pytest

from matching.documents import CarrierDoc, ScoringContext
from matching.signals import SIGNAL_TYPES, build_signal


def make_doc(dot_number="1", source=None, tokens=None):
    return CarrierDoc(
        dot_number=dot_number,
        source=source or {},
        tokens=tokens or {},
    )


def test_token_set_reads_field_and_subfield():
    doc = make_doc(tokens={"legal_name.phonetic": {"SM0"}})
    assert doc.token_set("legal_name", "phonetic") == {"SM0"}


def test_token_set_missing_field_is_empty_not_error():
    doc = make_doc()
    assert doc.token_set("legal_name", "phonetic") == set()


def test_value_reads_a_plain_field():
    doc = make_doc(source={"phy_state": "OR"})
    assert doc.value("phy_state") == "OR"


def test_value_reads_a_dotted_path_into_a_nested_object():
    doc = make_doc(source={"boc3_agents": {"co_name": "ACME"}})
    assert doc.value("boc3_agents.co_name") == "ACME"


def test_value_collects_a_dotted_path_across_a_list():
    # Enriched fields arrive as lists because max_matches > 1.
    doc = make_doc(
        source={"crashes": [{"vin": "A"}, {"vin": "B"}]},
    )
    assert doc.value("crashes.vin") == ["A", "B"]


def test_value_flattens_doubly_nested_lists():
    # Inspection VINs sit two enrichment levels deep on a carrier:
    # inspections[] (max_matches 100) -> units[] (max_matches 10) -> vin.
    # Without flattening, the units step yields a list of lists and the final
    # step finds no dicts, silently returning None.
    doc = make_doc(
        source={
            "inspections": [
                {"units": [{"vin": "A"}, {"vin": "B"}]},
                {"units": [{"vin": "C"}]},
            ]
        }
    )
    assert doc.value("inspections.units.vin") == ["A", "B", "C"]


def test_value_missing_path_is_none():
    assert make_doc().value("nope.not_here") is None


def test_agent_rarity_common_agent_scores_low():
    # The real top BOC-3 filer: 134,283 of 1,426,508 filings (9.4%).
    # Normalized IDF puts it at ~0.167.
    ctx = ScoringContext(agent_counts={"BIG FILER": 134283}, total_agent_carriers=1426508)
    assert ctx.agent_rarity("BIG FILER") < 0.20


def test_agent_rarity_rare_agent_scores_high():
    ctx = ScoringContext(agent_counts={"TINY FILER": 2}, total_agent_carriers=1426508)
    assert ctx.agent_rarity("TINY FILER") > 0.94


def test_agent_rarity_ranks_common_below_rare():
    # The property that actually matters: a dominant filer must score well
    # below a rare one. 1 - count/N would put both above 0.9 and rank them
    # nearly equal, which is why that formula was rejected.
    ctx = ScoringContext(
        agent_counts={"BIG FILER": 134283, "TINY FILER": 2},
        total_agent_carriers=1426508,
    )
    assert ctx.agent_rarity("BIG FILER") < ctx.agent_rarity("TINY FILER") - 0.5


def test_agent_rarity_unknown_agent_is_maximally_rare():
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=1000)
    assert ctx.agent_rarity("UNSEEN") == 1.0


def test_agent_rarity_with_no_corpus_is_neutral_zero():
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=0)
    assert ctx.agent_rarity("ANY") == 0.0


def cfg(**kwargs):
    return SimpleNamespace(**kwargs)


def test_build_signal_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown signal type"):
        build_signal(cfg(type="not-a-signal", weight=0.1))


def test_name_overlap_registered_under_both_names():
    assert "name-phonetic" in SIGNAL_TYPES
    assert "name-token" in SIGNAL_TYPES


def test_name_overlap_scores_identical_names_as_one():
    signal = build_signal(
        cfg(type="name-phonetic", weight=0.22, fields=["legal_name"], subfield="phonetic")
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}})
    cand = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_name_overlap_returns_none_when_tokens_absent():
    signal = build_signal(
        cfg(type="name-phonetic", weight=0.22, fields=["legal_name"], subfield="phonetic")
    )
    pred = make_doc(tokens={"legal_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": {"SM0"}})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_name_overlap_cross_field_matches_legal_name_against_dba():
    # The classic chameleon move: the old legal name becomes the new DBA.
    signal = build_signal(
        cfg(
            type="name-phonetic",
            weight=0.22,
            fields=["legal_name", "dba_name"],
            subfield="phonetic",
            cross_field=True,
        )
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0", "TRKN"}, "dba_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": set(), "dba_name.phonetic": {"SM0", "TRKN"}})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_name_overlap_without_cross_field_ignores_the_dba_crossover():
    signal = build_signal(
        cfg(
            type="name-phonetic",
            weight=0.22,
            fields=["legal_name", "dba_name"],
            subfield="phonetic",
            cross_field=False,
        )
    )
    pred = make_doc(tokens={"legal_name.phonetic": {"SM0"}, "dba_name.phonetic": set()})
    cand = make_doc(tokens={"legal_name.phonetic": set(), "dba_name.phonetic": {"SM0"}})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_signal_exposes_weight_as_float():
    signal = build_signal(
        cfg(type="name-token", weight="0.10", fields=["legal_name"], subfield="clean")
    )
    assert signal.weight == 0.10


def address_cfg(**overrides):
    base = dict(
        type="address",
        weight=0.20,
        fields=["phy_street", "mailing_street"],
        exact_subfield="clean",
        fuzzy_subfield="tokens",
        fuzzy_scale=0.7,
    )
    base.update(overrides)
    return cfg(**base)


def test_address_exact_match_scores_one():
    signal = build_signal(address_cfg())
    tokens = {"phy_street.clean": {"123 main street"}, "mailing_street.clean": set()}
    pred = make_doc(source={"phy_state": "OR"}, tokens=tokens)
    cand = make_doc(source={"phy_state": "OR"}, tokens=dict(tokens))
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_address_fuzzy_match_is_scaled_down():
    signal = build_signal(address_cfg())
    pred = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street"}, "phy_street.tokens": {"123", "main", "street"}},
    )
    cand = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street suite 4"}, "phy_street.tokens": {"123", "main", "street", "suite", "4"}},
    )
    # containment is 1.0 (pred tokens fully inside cand), scaled by fuzzy_scale
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.7)


def test_address_fuzzy_match_across_states_is_halved():
    # "100 MAIN ST" exists in every state; a fuzzy hit across states is weak.
    signal = build_signal(address_cfg())
    pred = make_doc(
        source={"phy_state": "OR"},
        tokens={"phy_street.clean": {"123 main street"}, "phy_street.tokens": {"123", "main", "street"}},
    )
    cand = make_doc(
        source={"phy_state": "TX"},
        tokens={"phy_street.clean": {"123 main street suite 4"}, "phy_street.tokens": {"123", "main", "street", "suite", "4"}},
    )
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.35)


def test_address_exact_match_across_states_is_not_halved():
    # Identical street in a different state is genuinely suspicious.
    signal = build_signal(address_cfg())
    tokens = {"phy_street.clean": {"123 main street"}}
    pred = make_doc(source={"phy_state": "OR"}, tokens=dict(tokens))
    cand = make_doc(source={"phy_state": "TX"}, tokens=dict(tokens))
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_address_returns_none_when_no_address_data():
    signal = build_signal(address_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None


def identifier_cfg():
    return cfg(
        type="exact-identifier",
        weight=0.12,
        phone_fields=["telephone", "fax"],
        text_fields=["email_address"],
    )


def test_exact_identifier_matching_phone_scores_one():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "(503) 289-5558"})
    cand = make_doc(source={"telephone": "503-289-5558"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_exact_identifier_matching_email_scores_one():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"email_address": "Joe@Example.com"})
    cand = make_doc(source={"email_address": "joe@example.com "})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_exact_identifier_different_values_score_zero():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "5032895558"})
    cand = make_doc(source={"telephone": "2025555555"})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_exact_identifier_placeholder_phones_never_match():
    signal = build_signal(identifier_cfg())
    pred = make_doc(source={"telephone": "0000000000"})
    cand = make_doc(source={"telephone": "0000000000"})
    assert signal.score(pred, cand, ScoringContext()) is None


def test_exact_identifier_returns_none_when_both_sides_blank():
    signal = build_signal(identifier_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None
