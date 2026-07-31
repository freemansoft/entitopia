"""Signal implementations.

Each signal scores one kind of evidence that two carriers are the same
operation. Every score() returns a float in [0.0, 1.0] or None.

None means "not evaluable" — data missing on one or both sides. It is not the
same as 0.0, which means "evaluated, no similarity". Returning 0.0 for missing
data would penalize carriers for absent records rather than judging them
neutrally.
"""

import logging

from matching.documents import CarrierDoc, ScoringContext
from matching.tokens import blended_overlap

logger = logging.getLogger(__name__)


class Signal:
    type_names: tuple[str, ...] = ()

    def __init__(self, config):
        self.config = config
        self.signal_type = config.type
        self.weight = float(config.weight)

    def score(self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext) -> float | None:
        raise NotImplementedError


class NameOverlapSignal(Signal):
    """Token-set overlap over name fields.

    Registered for both name-phonetic and name-token: the math is identical and
    only the subfield differs. Listing the same type twice in config with
    different subfields is how the double-metaphone and Beider-Morse arms get
    weighted independently.
    """

    type_names = ("name-phonetic", "name-token")

    def score(self, pred, cand, ctx):
        fields = list(self.config.fields)
        subfield = self.config.subfield
        cross_field = getattr(self.config, "cross_field", False)

        if cross_field:
            pairings = [(p, c) for p in fields for c in fields]
        else:
            pairings = [(f, f) for f in fields]

        best = None
        for pred_field, cand_field in pairings:
            score = blended_overlap(
                pred.token_set(pred_field, subfield),
                cand.token_set(cand_field, subfield),
            )
            if score is not None and (best is None or score > best):
                best = score
        return best


SIGNAL_TYPES: dict[str, type[Signal]] = {}


def _register(signal_class: type[Signal]) -> None:
    for name in signal_class.type_names:
        SIGNAL_TYPES[name] = signal_class


_register(NameOverlapSignal)


def build_signal(config) -> Signal:
    signal_class = SIGNAL_TYPES.get(config.type)
    if signal_class is None:
        raise ValueError(
            "unknown signal type {!r}; known types are {}".format(
                config.type, ", ".join(sorted(SIGNAL_TYPES))
            )
        )
    return signal_class(config)
