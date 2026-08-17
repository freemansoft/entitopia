"""A reader of one pair must be able to tell what evidence fired.

Deleting the vin-overlap type name removes the only clue the emitted document
carried about what a shared-token signal actually read — the type string was
doing the explaining. These pin the two replacements: the field paths the
signal reads, derived so they cannot drift, and the operator's own label.

Paths only, never values. A matched identifier belongs to a flagged entity, and
putting it in the pair document would publish an identifying value next to an
allegation.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.documents import EntityDoc, ScoringContext
from matching.scorer import IDENTITY_SIGNAL_TYPES, PairScorer
from matching.signals import SIGNAL_TYPES, build_signal

_PHASE = Path(__file__).parent.parent / "phase_providers" / "phase_entity_match.py"


def _load_phase():
    spec = importlib.util.spec_from_file_location("phase_entity_match", _PHASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase = _load_phase()

_DOT_ENTITY_MATCH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)


def _shared_token(**overrides):
    base = {"type": "shared-token", "weight": 0.16, "fields": ["crashes.vin"]}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_vin_overlap_is_no_longer_a_registered_type():
    assert "vin-overlap" not in SIGNAL_TYPES
    assert "shared-token" in SIGNAL_TYPES


def test_vin_overlap_is_no_longer_an_identity_type():
    assert "vin-overlap" not in IDENTITY_SIGNAL_TYPES
    assert "shared-token" in IDENTITY_SIGNAL_TYPES


def test_a_config_still_saying_vin_overlap_fails_loudly():
    # It must not score nothing silently. The message names the replacement,
    # because that is the only thing the reader needs.
    with pytest.raises(ValueError, match="unknown signal type") as excinfo:
        build_signal(_shared_token(type="vin-overlap"))
    assert "shared-token" in str(excinfo.value)


def test_signal_reports_the_fields_it_reads():
    signal = build_signal(
        _shared_token(fields=["crashes.vin", "inspections.units.vin"])
    )
    assert signal.fields_read() == ["crashes.vin", "inspections.units.vin"]


def test_fields_read_covers_a_signal_with_several_field_keys():
    # exact-identifier splits its inputs across two config keys, and a reader
    # of the pair needs both to know what fired.
    signal = build_signal(
        SimpleNamespace(
            type="exact-identifier",
            weight=0.19,
            phone_fields=["telephone", "fax"],
            text_fields=["email_address"],
        )
    )
    assert signal.fields_read() == ["telephone", "fax", "email_address"]


def test_signal_name_defaults_to_none():
    assert build_signal(_shared_token()).signal_name is None


def test_signal_name_comes_from_config():
    # The label lives on the signal instance in project config, so the
    # framework never learns the word "vin" and a project names its own.
    assert build_signal(_shared_token(name="vin-overlap")).signal_name == "vin-overlap"


def test_shipped_dot_config_labels_its_shared_token_signal():
    """Without the label DOT's pairs lose the word that explained them.

    The type name used to carry this meaning implicitly. Nothing else in the
    emitted document says "vehicle", so if the label were dropped the only
    remaining clue would be the field paths.
    """
    signals = json.loads(_DOT_ENTITY_MATCH.read_text())["signals"]
    shared = [s for s in signals if s["type"] == "shared-token"]
    assert len(shared) == 1
    assert shared[0]["name"] == "vin-overlap"
    assert shared[0]["conclusive"] is True
    assert shared[0]["max_shared_entities"] == 5


def test_emitted_contribution_carries_the_label_and_the_field_paths():
    """What a reader of one pair actually sees, end to end.

    The unit tests above cover the accessors; this covers the document, which
    is the thing that survives the run and gets quoted months later.
    """
    scoring = SimpleNamespace(
        min_total_score=0.0, min_signals=1, require_identity_signal=False
    )
    scorer = PairScorer(
        [_shared_token(name="vin-overlap", fields=["crashes.vin"])], scoring
    )
    pred = EntityDoc(entity_key="1", source={"crashes": [{"vin": "1ABC"}]}, tokens={})
    cand = EntityDoc(entity_key="2", source={"crashes": [{"vin": "1ABC"}]}, tokens={})
    pair = scorer.score_pair(pred, cand, ScoringContext())
    assert pair is not None

    instance = phase.PhaseEntityMatch(
        es=None, project="p", one_step="s", project_config=None
    )
    document = instance._to_action(
        pair,
        "candidates-000001",
        phase.RunProvenance(
            run_id="r", generated_at="2026-08-17T00:00:00+00:00", source_index="src"
        ),
    )["_source"]

    (emitted,) = document["signals"]
    assert emitted["signal_type"] == "shared-token"
    assert emitted["signal_name"] == "vin-overlap"
    assert emitted["fields"] == ["crashes.vin"]
    # matched_on stays keyed by type: IDENTITY_SIGNAL_TYPES and the metric
    # predicates all operate on that set, so a label there would change metric
    # values for no gain.
    assert document["matched_on"] == ["shared-token"]


def test_emitted_contribution_omits_an_unset_label():
    # Absent rather than null, matching how the provenance fields treat
    # unknown: a key that is not there cannot later be quoted as a value.
    scoring = SimpleNamespace(
        min_total_score=0.0, min_signals=1, require_identity_signal=False
    )
    scorer = PairScorer([_shared_token(fields=["crashes.vin"])], scoring)
    pred = EntityDoc(entity_key="1", source={"crashes": [{"vin": "1ABC"}]}, tokens={})
    cand = EntityDoc(entity_key="2", source={"crashes": [{"vin": "1ABC"}]}, tokens={})
    pair = scorer.score_pair(pred, cand, ScoringContext())

    instance = phase.PhaseEntityMatch(
        es=None, project="p", one_step="s", project_config=None
    )
    document = instance._to_action(
        pair,
        "candidates-000001",
        phase.RunProvenance(
            run_id="r", generated_at="2026-08-17T00:00:00+00:00", source_index="src"
        ),
    )["_source"]

    (emitted,) = document["signals"]
    assert "signal_name" not in emitted
    assert emitted["fields"] == ["crashes.vin"]


def test_emitted_fields_never_carry_the_matched_value():
    """Paths, not values. The rule this guards is the repo's anonymization rule.

    A matched identifier belongs to an entity the matcher flagged, so writing
    it into the pair document would publish an identifying value next to an
    allegation. Asserting the absence directly, because this is the kind of
    thing a well-meaning "make the output more useful" change would add.
    """
    scoring = SimpleNamespace(
        min_total_score=0.0, min_signals=1, require_identity_signal=False
    )
    scorer = PairScorer(
        [_shared_token(name="vin-overlap", fields=["crashes.vin"])], scoring
    )
    secret = "1FUJGLDR0CSBP9784"
    pred = EntityDoc(entity_key="1", source={"crashes": [{"vin": secret}]}, tokens={})
    cand = EntityDoc(entity_key="2", source={"crashes": [{"vin": secret}]}, tokens={})
    pair = scorer.score_pair(pred, cand, ScoringContext())

    instance = phase.PhaseEntityMatch(
        es=None, project="p", one_step="s", project_config=None
    )
    action = instance._to_action(
        pair,
        "candidates-000001",
        phase.RunProvenance(
            run_id="r", generated_at="2026-08-17T00:00:00+00:00", source_index="src"
        ),
    )
    assert secret not in json.dumps(action)


def test_shipped_dot_config_seeds_on_the_renamed_type():
    # seed_signals matches on signal_type, so a stale name here silently caps
    # recall at zero for the entire fraud profile this signal exists to catch.
    config = json.loads(_DOT_ENTITY_MATCH.read_text())
    seeds = config["candidates"]["seed_signals"]
    assert "vin-overlap" not in seeds
    assert "shared-token" in seeds
    configured_types = {s["type"] for s in config["signals"]}
    assert set(seeds) <= configured_types, "a seed names a signal that is not configured"
