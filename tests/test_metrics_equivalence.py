"""The config-driven metrics and the hand-written ones must stay in agreement.

DOT-Commercial/precision_metrics.py was going to be retired once metrics.json
reproduced its numbers. It is kept instead, because it is the only independent
statement of what these eleven metrics mean — deleting it would leave the
config file as its own specification, and a filter transcribed subtly wrong
would be undetectable rather than merely undetected.

Keeping two implementations is only safe if they cannot drift, which is what
this file is for. It runs both over the same synthetic population and requires
identical records, so an edit to either that changes a number fails here rather
than in a comparison months later.

The population is built to exercise the cases where the two could plausibly
disagree: null gaps, absent names, negative gaps, exact-set versus
identity-set matching, and the band edges each metric sits on.
"""

import importlib.util
import json
from pathlib import Path

from utils.metric_runner import summarize

_ROOT = Path(__file__).parent.parent
_PRECISION_METRICS = _ROOT / "DOT-Commercial" / "precision_metrics.py"
_METRICS_JSON = (
    _ROOT / "DOT-Commercial" / "configuration" / "chameleon-detection" / "metrics.json"
)


def _load_precision_metrics():
    spec = importlib.util.spec_from_file_location("precision_metrics", _PRECISION_METRICS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


precision_metrics = _load_precision_metrics()
_CONFIG = json.loads(_METRICS_JSON.read_text())


def _pair(score, gap, matched, pred_name="ACME EXAMPLE", succ_name=None, pred="1", succ="2"):
    return {
        "total_score": score,
        "gap_days": gap,
        "matched_on": list(matched),
        "signals_present": len(matched),
        "predecessor": {
            "entity_key": pred,
            "dot_number": pred,
            "legal_name": pred_name,
        },
        "successor": {
            "entity_key": succ,
            "dot_number": succ,
            "legal_name": pred_name if succ_name is None else succ_name,
        },
        "signals": [{"signal_type": t, "score": 1.0} for t in matched],
    }


# Each entry is a case the two implementations could disagree on.
POPULATION = [
    # Canary shape: identical name, near-perfect score, gap inside a week.
    _pair(1.0, 3, ["shared-token", "name-token"]),
    # Same but one day past the canary window.
    _pair(1.0, 8, ["shared-token", "name-token"]),
    # Triage, bounded: corroborated, inside a year, non-negative gap.
    _pair(0.8, 200, ["exact-identifier", "name-token"]),
    # Triage, unbounded only: corroborated, negative gap inside the coherent
    # window -- counts for triage_unbounded but not triage_bounded.
    _pair(0.8, -30, ["exact-identifier", "name-token"]),
    # Corroborated but outside the coherent window entirely.
    _pair(0.8, -900, ["exact-identifier"]),
    # Null gap: not evaluable, must not count as coherent or as triage.
    _pair(0.9, None, ["exact-identifier", "name-token"]),
    # Shared-token only, exact set.
    _pair(0.5, 10, ["shared-token"]),
    # Shared-token plus a non-identity signal: identity-set-only, not exact-set.
    _pair(0.5, 10, ["shared-token", "temporal"], pred="3", succ="4"),
    # Both names absent: must NOT count as an identical-name match.
    _pair(0.9, 5, ["shared-token"], pred_name=None, pred="5", succ="6"),
    # Names differ.
    _pair(0.9, 5, ["shared-token"], succ_name="OTHER EXAMPLE", pred="7", succ="8"),
    # Below every score floor.
    _pair(0.1, 5, ["name-token"], pred="9", succ="10"),
    # Band edges: exactly 0.70, exactly 365 days, exactly -180.
    _pair(0.70, 365, ["exact-identifier", "name-token"], pred="11", succ="12"),
    _pair(0.70, -180, ["exact-identifier", "name-token"], pred="13", succ="14"),
    # Repeated predecessor, so predecessors_with_pairs differs from pairs.
    _pair(0.8, 20, ["exact-identifier"], pred="11", succ="15"),
]


def test_both_implementations_produce_identical_records():
    reference = precision_metrics.summarize(iter(POPULATION))
    candidate = summarize(_CONFIG["metrics"], iter(POPULATION))
    assert candidate == reference


def test_the_synthetic_population_actually_exercises_the_metrics():
    """A population where every metric is zero would make the test vacuous.

    This is the guard against the equivalence above passing because both
    implementations found nothing rather than because they agree.
    """
    record = summarize(_CONFIG["metrics"], iter(POPULATION))
    for name in (
        "pairs",
        "pairs_ge_070",
        "coherent_ge_070",
        "triage_unbounded",
        "triage_bounded",
        "identical_name_triage",
        "canary",
        "vin_only",
        "vin_only_identity",
        "predecessors_with_pairs",
    ):
        assert record[name] > 0, "{} is zero; the population does not exercise it".format(
            name
        )
    # And the two readings of "shared a token and nothing else" must actually
    # differ here, or the distinction they exist for is untested.
    assert record["vin_only"] != record["vin_only_identity"]


def test_both_implementations_declare_the_same_metric_names():
    assert set(precision_metrics.METRICS) == {
        metric["name"] for metric in _CONFIG["metrics"]
    }
