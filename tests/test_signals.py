from matching.documents import CarrierDoc, ScoringContext


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
