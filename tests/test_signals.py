import datetime
from types import SimpleNamespace

import pytest

from matching.documents import CarrierDoc, ScoringContext
from matching.signals import (
    MAX_SEED_TOKENS,
    SIGNAL_TYPES,
    build_signal,
    parse_flexible_date,
)


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


def test_agent_rarity_with_no_corpus_is_floor_zero_not_neutral():
    # 0.0 here is the bottom of the signal's range ("contributes nothing"),
    # not a neutral midpoint. Returning 1.0 (the "unseen agent" value) would
    # misrepresent an unmeasured agent as a known-rare one; a shared agent
    # under a missing corpus is real evidence the signal simply can't weigh.
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=0)
    assert ctx.agent_rarity("ANY") == 0.0


def test_agent_rarity_with_single_agent_corpus_does_not_raise():
    # total_agent_carriers == 1 makes log(N) == log(1) == 0, so the naive
    # log(N/count)/log(N) division is 0/0. This must return the same floor
    # value as the no-corpus case rather than raising ZeroDivisionError.
    ctx = ScoringContext(agent_counts={"ONLY FILER": 1}, total_agent_carriers=1)
    assert ctx.agent_rarity("ONLY FILER") == 0.0


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
    base = {
        "type": "address",
        "weight": 0.20,
        "fields": ["phy_street", "mailing_street"],
        "exact_subfield": "clean",
        "fuzzy_subfield": "tokens",
        "fuzzy_scale": 0.7,
    }
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


def test_parse_flexible_date_reads_iso():
    assert parse_flexible_date("2022-07-09") == datetime.date(2022, 7, 9)


def test_parse_flexible_date_reads_oracle_format_with_century_pivot():
    # 01-JUN-74 is a 1974 registration, not 2074.
    assert parse_flexible_date("01-JUN-74") == datetime.date(1974, 6, 1)


def test_parse_flexible_date_pivots_low_years_to_2000s():
    assert parse_flexible_date("23-JAN-02") == datetime.date(2002, 1, 23)


def test_parse_flexible_date_returns_none_for_junk():
    assert parse_flexible_date("") is None
    assert parse_flexible_date(None) is None
    assert parse_flexible_date("not a date") is None


def agent_cfg():
    return cfg(
        type="agent",
        weight=0.04,
        name_field="boc3_agents.co_name",
    )


def test_agent_shared_rare_agent_scores_high():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={"TINY FILER": 2}, total_agent_carriers=1426508)
    pred = make_doc(source={"boc3_agents": [{"co_name": "TINY FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "TINY FILER"}]})
    # True normalized IDF for count=2 is ~0.951. An earlier draft asserted
    # > 0.99, which passed only because a case mismatch made the lookup miss
    # and fall back to the 1.0 "unseen agent" value — a test passing for the
    # wrong reason, and masking the bug the next test now pins.
    assert signal.score(pred, cand, ctx) > 0.94


def test_agent_lookup_is_case_insensitive():
    # AgentSignal lowercases names before intersecting them, while the sweep
    # builds agent_counts from a terms aggregation. If those two disagree on
    # case the lookup misses, every agent scores the 1.0 "unseen" fallback,
    # and the rarity weighting silently turns itself off. ScoringContext
    # normalizes both sides so casing cannot cause that.
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={"BIG FILER": 134283}, total_agent_carriers=1426508)
    pred = make_doc(source={"boc3_agents": [{"co_name": "big filer"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "BiG FiLeR"}]})
    assert signal.score(pred, cand, ctx) < 0.20


def test_agent_shared_common_agent_scores_low():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={"BIG FILER": 134283}, total_agent_carriers=1426508)
    pred = make_doc(source={"boc3_agents": [{"co_name": "BIG FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "BIG FILER"}]})
    # The real top BOC-3 filer: 134,283 of 1,426,508 filings. Normalized IDF
    # puts it at 0.1668, so this bound must sit above that, not below it.
    assert signal.score(pred, cand, ctx) < 0.20


def test_agent_no_shared_agent_scores_zero():
    signal = build_signal(agent_cfg())
    ctx = ScoringContext(agent_counts={}, total_agent_carriers=100)
    pred = make_doc(source={"boc3_agents": [{"co_name": "A FILER"}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "B FILER"}]})
    assert signal.score(pred, cand, ctx) == 0.0


def test_agent_blank_names_never_match():
    # co_name is blank on 23.3% of BOC-3 rows.
    signal = build_signal(agent_cfg())
    pred = make_doc(source={"boc3_agents": [{"co_name": ""}]})
    cand = make_doc(source={"boc3_agents": [{"co_name": "  "}]})
    assert signal.score(pred, cand, ScoringContext()) is None


def temporal_cfg(**overrides):
    base = {
        "type": "temporal",
        "weight": 0.05,
        "predecessor_date": "out_of_service_orders.oos_date",
        "successor_date": "add_date",
        "max_gap_days": 365,
    }
    base.update(overrides)
    return cfg(**base)


def test_temporal_same_day_reopen_scores_one():
    signal = build_signal(temporal_cfg())
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2022-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_temporal_decays_linearly_over_the_window():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2022-02-20"})  # 50 days
    assert signal.score(pred, cand, ScoringContext()) == pytest.approx(0.5)


def test_temporal_beyond_the_window_scores_zero():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    cand = make_doc(source={"add_date": "2024-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_temporal_uses_the_latest_shutdown_date():
    signal = build_signal(temporal_cfg(max_gap_days=100))
    pred = make_doc(
        source={"out_of_service_orders": [{"oos_date": "2010-01-01"}, {"oos_date": "2022-01-01"}]}
    )
    cand = make_doc(source={"add_date": "2022-01-01"})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_temporal_pre_registered_shell_scores_at_half_weight():
    # Registering the successor before the shutdown is a real tactic, but
    # weaker evidence than registering right after. 90 days before is halfway
    # through the 180-day backward window, scaled by 0.5 => 0.25.
    signal = build_signal(temporal_cfg(max_gap_days=365))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-07-01"}]})
    earlier = make_doc(source={"add_date": "2022-04-02"})  # 90 days before
    assert signal.score(pred, earlier, ScoringContext()) == pytest.approx(0.25)


def test_temporal_beyond_the_backward_window_scores_zero():
    signal = build_signal(temporal_cfg(max_gap_days=365))
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-07-01"}]})
    earlier = make_doc(source={"add_date": "2021-01-01"})  # far before the window
    assert signal.score(pred, earlier, ScoringContext()) == 0.0


def test_temporal_returns_none_when_a_date_is_missing():
    signal = build_signal(temporal_cfg())
    pred = make_doc(source={"out_of_service_orders": [{"oos_date": "2022-01-01"}]})
    assert signal.score(pred, make_doc(), ScoringContext()) is None


def vin_cfg():
    return cfg(
        type="vin-overlap",
        weight=0.08,
        fields=["crashes.vehicle_identification_number"],
    )


def test_vin_overlap_shared_vin_scores_one():
    signal = build_signal(vin_cfg())
    pred = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    cand = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_vin_overlap_no_shared_vin_scores_zero():
    signal = build_signal(vin_cfg())
    pred = make_doc(source={"crashes": [{"vehicle_identification_number": "1ABC"}]})
    cand = make_doc(source={"crashes": [{"vehicle_identification_number": "2XYZ"}]})
    assert signal.score(pred, cand, ScoringContext()) == 0.0


def test_vin_overlap_returns_none_without_vins():
    signal = build_signal(vin_cfg())
    assert signal.score(make_doc(), make_doc(), ScoringContext()) is None


# --- seed_clauses: candidate retrieval driven by the signals themselves ---


def test_agent_signal_declines_to_seed():
    # 87 BOC-3 agents cover 519,139 filings, so seeding on one would return
    # essentially random carriers. Declining is what keeps it corroboration-only.
    signal = build_signal(
        SimpleNamespace(type="agent", weight=0.04, name_field="boc3_agents.co_name")
    )
    assert signal.seed_clauses({"boc3_agents": {"co_name": "ACME"}}) == []


def test_shared_token_signal_seeds_a_terms_clause_per_field():
    signal = build_signal(
        SimpleNamespace(
            type="vin-overlap",
            weight=0.08,
            fields=["crashes.vin", "inspections.units.vin"],
        )
    )
    source = {
        "crashes": [{"vin": "1FUJGLDR0CSBP9784"}],
        "inspections": [{"units": [{"vin": "56EA75C28KA000073"}]}],
    }
    clauses = signal.seed_clauses(source)
    assert len(clauses) == 2
    # Every field is queried with the union of tokens, so a VIN seen only in
    # crashes still retrieves a carrier that saw it only in inspections.
    # Values keep their original case: the fields are keyword-mapped, so a
    # casefolded term would match nothing and seed zero candidates.
    for clause in clauses:
        terms = next(iter(clause["terms"].values()))
        assert terms == ["1FUJGLDR0CSBP9784", "56EA75C28KA000073"]


def test_shared_token_signal_seeds_nothing_without_tokens():
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    assert signal.seed_clauses({}) == []


def test_shared_token_seed_tokens_are_capped_and_sorted():
    # An unbounded terms clause on a large fleet would slow the whole sweep.
    vins = ["VIN{:05d}".format(i) for i in range(MAX_SEED_TOKENS + 50)]
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    clauses = signal.seed_clauses({"crashes": [{"vin": v} for v in vins]})
    terms = clauses[0]["terms"]["crashes.vin"]
    assert len(terms) == MAX_SEED_TOKENS
    assert terms == sorted(terms)


def test_shared_token_registered_under_neutral_alias():
    # The logic is "same globally-unique token", not anything about vehicles.
    assert SIGNAL_TYPES["shared-token"] is SIGNAL_TYPES["vin-overlap"]


def test_name_signal_seeds_and_declares_its_token_subfields():
    signal = build_signal(
        SimpleNamespace(
            type="name-phonetic",
            weight=0.22,
            fields=["legal_name", "dba_name"],
            subfield="phonetic",
            cross_field=True,
        )
    )
    clauses = signal.seed_clauses({"legal_name": "ACME TRUCKING"})
    assert clauses == [{"match": {"legal_name.phonetic": {"query": "ACME TRUCKING"}}}]
    assert signal.token_subfields() == {"legal_name.phonetic", "dba_name.phonetic"}


def test_address_signal_seeds_on_exact_subfield_only():
    # Seeding on the fuzzy subfield would drag in every street with a shared
    # token; the fuzzy comparison still happens later during scoring.
    signal = build_signal(
        SimpleNamespace(
            type="address",
            weight=0.2,
            fields=["phy_street"],
            exact_subfield="clean",
            fuzzy_subfield="tokens",
            fuzzy_scale=0.7,
        )
    )
    clauses = signal.seed_clauses({"phy_street": "100 MAIN ST"})
    assert clauses == [{"match": {"phy_street.clean": {"query": "100 MAIN ST"}}}]
    assert signal.token_subfields() == {"phy_street.clean", "phy_street.tokens"}


def test_shared_token_seeds_preserve_case_for_keyword_fields():
    # Regression: seeding with normalize_text_identifier's casefolded output
    # matched nothing against a keyword mapping, so the signal silently
    # retrieved no candidates at all.
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    clauses = signal.seed_clauses({"crashes": [{"vin": "1FUJGLDR0CSBP9784"}]})
    assert clauses[0]["terms"]["crashes.vin"] == ["1FUJGLDR0CSBP9784"]


def test_shared_token_score_still_normalizes_case():
    # Seeding uses raw values; scoring compares normalized ones, so a
    # case difference between two records still scores as a match.
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    pred = make_doc(source={"crashes": [{"vin": "1FUJGLDR0CSBP9784"}]})
    cand = make_doc(dot_number="2", source={"crashes": [{"vin": "1fujgldr0csbp9784"}]})
    assert signal.score(pred, cand, ScoringContext()) == 1.0


def test_suppressed_token_is_not_evaluable_rather_than_zero():
    # FMCSA crash reports carry the literal VIN "UNKNOWN" on 79 carriers. Two
    # carriers both filing it share nothing, so the signal must report "no
    # usable evidence" (None), not "evaluated, matched" (1.0).
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    ctx = ScoringContext(ignored_values={"crashes.vin": {"unknown"}})
    pred = make_doc(source={"crashes": [{"vin": "UNKNOWN"}]})
    cand = make_doc(dot_number="2", source={"crashes": [{"vin": "UNKNOWN"}]})
    assert signal.score(pred, cand, ctx) is None


def test_suppression_leaves_real_tokens_alone():
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    ctx = ScoringContext(ignored_values={"crashes.vin": {"unknown"}})
    pred = make_doc(source={"crashes": [{"vin": "UNKNOWN"}, {"vin": "1FUJGLDR0CSBP9784"}]})
    cand = make_doc(dot_number="2", source={"crashes": [{"vin": "1FUJGLDR0CSBP9784"}]})
    assert signal.score(pred, cand, ctx) == 1.0


def test_suppressed_tokens_are_not_seeded():
    # Seeding on "GGGG" would retrieve all 158 carriers that recorded it.
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    ctx = ScoringContext(ignored_values={"crashes.vin": {"gggg"}})
    clauses = signal.seed_clauses({"crashes": [{"vin": "GGGG"}]}, ctx)
    assert clauses == []


def test_ignored_values_are_scoped_to_their_field():
    # "0" is not a VIN but is a fine street number, so an ignore list keyed by
    # field must not leak across attributes.
    ctx = ScoringContext(ignored_values={"crashes.vin": {"0"}})
    assert ctx.is_ignored("crashes.vin", "0")
    assert not ctx.is_ignored("phy_street", "0")


def test_wildcard_ignore_applies_to_every_field():
    ctx = ScoringContext(ignored_values={"*": {"n/a"}})
    assert ctx.is_ignored("crashes.vin", "N/A")
    assert ctx.is_ignored("anything.else", "n/a")


def test_declared_ignore_values_are_case_insensitive():
    # An operator writing "Unknown" in config must match a record's "UNKNOWN",
    # or the ignore list silently does nothing.
    ctx = ScoringContext(ignored_values={"crashes.vin": {"Unknown"}})
    assert ctx.is_ignored("crashes.vin", "UNKNOWN")


# --- exact identifiers: shared values that are correct but not identifying ---


def exact_identifier_signal():
    return build_signal(
        SimpleNamespace(
            type="exact-identifier",
            weight=0.12,
            phone_fields=["telephone"],
            text_fields=["email_address"],
        )
    )


def test_ignored_phone_is_not_evaluable():
    # "(000) 000-0000" sits on 664 carriers. Two of them share nothing.
    signal = exact_identifier_signal()
    ctx = ScoringContext(ignored_values={"telephone": {"(000) 000-0000"}})
    pred = make_doc(source={"telephone": "(000) 000-0000"})
    cand = make_doc(dot_number="2", source={"telephone": "(000) 000-0000"})
    assert signal.score(pred, cand, ctx) is None


def test_ignore_matches_the_normalized_phone_form_too():
    # The frequency scan contributes what ES indexed, "(000) 000-0000", while
    # an operator may write the normalized digits. Both must work.
    signal = exact_identifier_signal()
    ctx = ScoringContext(ignored_values={"telephone": {"0000000000"}})
    pred = make_doc(source={"telephone": "(000) 000-0000"})
    cand = make_doc(dot_number="2", source={"telephone": "(000) 000-0000"})
    assert signal.score(pred, cand, ctx) is None


def test_ignored_shared_service_email_is_not_identity_evidence():
    # A permit filing service's address is correct data on hundreds of
    # unrelated carriers, so it cannot establish that two of them are one.
    signal = exact_identifier_signal()
    ctx = ScoringContext(ignored_values={"email_address": {"permits@example-service.com"}})
    pred = make_doc(source={"email_address": "PERMITS@EXAMPLE-SERVICE.COM"})
    cand = make_doc(dot_number="2", source={"email_address": "PERMITS@EXAMPLE-SERVICE.COM"})
    assert signal.score(pred, cand, ctx) is None


def test_real_shared_phone_still_scores():
    signal = exact_identifier_signal()
    ctx = ScoringContext(ignored_values={"telephone": {"0000000000"}})
    pred = make_doc(source={"telephone": "(555) 867-5309"})
    cand = make_doc(dot_number="2", source={"telephone": "(555) 867-5309"})
    assert signal.score(pred, cand, ctx) == 1.0


def test_ignored_identifier_does_not_seed():
    # Seeding on a filing service's email retrieves every carrier it ever
    # filed for, crowding out real candidates under max_candidates.
    signal = exact_identifier_signal()
    ctx = ScoringContext(ignored_values={"email_address": {"permits@example-service.com"}})
    assert signal.seed_clauses({"email_address": "PERMITS@EXAMPLE-SERVICE.COM"}, ctx) == []


def test_exact_identifier_declares_keyword_agg_fields():
    # These are text-mapped, so the frequency scan must aggregate the subfield.
    signal = exact_identifier_signal()
    assert signal.exact_evidence_fields() == [
        ("telephone", "telephone.keyword"),
        ("email_address", "email_address.keyword"),
    ]


def test_shared_token_aggregates_on_the_field_itself():
    # VIN fields are keyword-mapped, so there is no subfield to aggregate.
    signal = build_signal(
        SimpleNamespace(type="vin-overlap", weight=0.08, fields=["crashes.vin"])
    )
    assert signal.exact_evidence_fields() == [("crashes.vin", "crashes.vin")]


def test_similarity_signals_declare_no_exact_evidence():
    # A common name token is handled by weighting, not by exclusion.
    signal = build_signal(
        SimpleNamespace(
            type="name-phonetic", weight=0.22, fields=["legal_name"], subfield="phonetic"
        )
    )
    assert signal.exact_evidence_fields() == []
