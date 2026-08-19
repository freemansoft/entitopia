"""Config that is individually well-formed and jointly incoherent.

Every rule here corresponds to a configuration a schema accepts and that
produces a silently degraded sweep — which is the failure mode this repository
keeps hitting, and the reason a schema alone is not enough. A schema can say
"this key has the right type"; it cannot say "this seed names a signal nobody
configured", because that fact lives in two places at once.
"""

import json
from pathlib import Path

from utils import config_coherence

_SHIPPED_PATH = (
    Path(__file__).parent.parent
    / "DOT-Commercial"
    / "configuration"
    / "chameleon-detection"
    / "entity-match.json"
)


def _base():
    return {
        "entity": {"key": "dot_number"},
        "population": {
            "mode": "lifecycle",
            "selector": "out-of-service",
            "selectors": {"out-of-service": {"term": {"a": "b"}}},
        },
        "candidates": {"seed_signals": ["name-phonetic"]},
        "signals": [
            {
                "type": "name-phonetic",
                "weight": 0.5,
                "fields": ["legal_name"],
                "subfield": "phonetic",
            }
        ],
        "scoring": {},
    }


def test_a_coherent_config_reports_nothing():
    assert config_coherence.check(_base(), "test.json") == []


def test_temporal_without_lifecycle_is_reported():
    # The signal raises at build time, but that surfaces as a stack trace part
    # way into a run. Reporting it here names the file and the fix instead.
    raw = _base()
    raw["signals"].append({"type": "temporal", "weight": 0.05, "max_gap_days": 365})
    assert any("lifecycle" in e for e in config_coherence.check(raw, "test.json"))


def test_a_seed_naming_an_unconfigured_signal_is_reported():
    # Silently caps recall at zero for that evidence: the seed matches no
    # configured signal, so no clause is ever built for it and nothing says so.
    raw = _base()
    raw["candidates"]["seed_signals"].append("shared-token")
    assert any("shared-token" in e for e in config_coherence.check(raw, "test.json"))


def test_a_selector_naming_an_undefined_entry_is_reported():
    raw = _base()
    raw["population"]["selector"] = "revoked-authority"
    assert any(
        "revoked-authority" in e for e in config_coherence.check(raw, "test.json")
    )


def test_a_selector_cycle_is_reported():
    raw = _base()
    raw["population"]["selectors"] = {"a": {"all": ["b"]}, "b": {"all": ["a"]}}
    raw["population"]["selector"] = "a"
    assert any("cycle" in e for e in config_coherence.check(raw, "test.json"))


def test_lifecycle_mode_without_a_selector_is_reported():
    raw = _base()
    del raw["population"]["selector"]
    assert config_coherence.check(raw, "test.json")


def test_a_gap_window_without_lifecycle_is_reported():
    # The window depends on the lifecycle block. Setting it with no block
    # leaves the gate silently off -- an operator's tightening that does
    # nothing, which is worse than no tightening because it reads as done.
    raw = _base()
    raw["scoring"] = {"min_gap_days": -180, "max_gap_days": 365}
    assert any("lifecycle" in e for e in config_coherence.check(raw, "test.json"))


def test_all_entities_mode_with_a_lifecycle_is_reported():
    # Not fatal in the matcher, but it means somebody expected succession from
    # a sweep that emits none, which is worth saying out loud.
    raw = _base()
    raw["population"]["mode"] = "all-entities"
    raw["lifecycle"] = {"shutdown_date": "a", "registration_date": "b"}
    assert config_coherence.check(raw, "test.json")


def test_all_entities_mode_needs_no_selector():
    raw = _base()
    raw["population"] = {"mode": "all-entities", "sort_field": "dot_number"}
    assert config_coherence.check(raw, "test.json") == []


def test_a_conclusive_signal_that_cannot_seed_is_reported():
    # A signal marked conclusive carries a match on its own, so a pair resting
    # only on it must be retrievable. Not seeding on it caps recall at zero for
    # exactly the profile the conclusive mark exists to catch -- the measured
    # case being an entity that changed name, address and phone but kept its
    # equipment.
    raw = _base()
    raw["signals"].append(
        {
            "type": "shared-token",
            "weight": 0.16,
            "conclusive": True,
            "fields": ["crashes.vin"],
        }
    )
    assert any("conclusive" in e for e in config_coherence.check(raw, "test.json"))


def test_every_problem_is_reported_not_just_the_first():
    raw = _base()
    raw["candidates"]["seed_signals"] = ["nope", "also-nope"]
    raw["population"]["selector"] = "undefined-one"
    assert len(config_coherence.check(raw, "test.json")) >= 3


def test_the_shipped_dot_config_is_coherent():
    raw = json.loads(_SHIPPED_PATH.read_text())
    assert config_coherence.check(raw, str(_SHIPPED_PATH)) == []
