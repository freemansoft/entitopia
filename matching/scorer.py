"""Pair scoring: aggregate signals, renormalize, apply guards."""

import logging
from dataclasses import dataclass, field

from matching.documents import CarrierDoc, ScoringContext
from matching.signals import build_signal

logger = logging.getLogger(__name__)

# A pair must be tied together by at least one of these. Temporal proximity and
# a shared process agent corroborate; they cannot carry a match on their own.
IDENTITY_SIGNAL_TYPES = frozenset(
    {"name-phonetic", "name-token", "address", "exact-identifier"}
)


@dataclass
class SignalContribution:
    signal_type: str
    subfield: str | None
    weight: float
    score: float
    contribution: float


@dataclass
class ScoredPair:
    predecessor: CarrierDoc
    successor: CarrierDoc
    total_score: float
    signals: list[SignalContribution] = field(default_factory=list)
    matched_on: list[str] = field(default_factory=list)
    signals_present: int = 0


class PairScorer:
    def __init__(self, signal_configs, scoring_config):
        self.signals = [build_signal(c) for c in signal_configs]
        if sum(s.weight for s in self.signals) <= 0:
            raise ValueError("signal weights sum to zero; nothing can be scored")

        self.min_total_score = float(getattr(scoring_config, "min_total_score", 0.0))
        self.min_signals = int(getattr(scoring_config, "min_signals", 1))
        self.require_identity_signal = bool(
            getattr(scoring_config, "require_identity_signal", True)
        )

    def score_pair(
        self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext
    ) -> ScoredPair | None:
        if pred.dot_number == cand.dot_number:
            return None

        contributions: list[SignalContribution] = []
        for signal in self.signals:
            score = signal.score(pred, cand, ctx)
            if score is None:
                continue
            contributions.append(
                SignalContribution(
                    signal_type=signal.signal_type,
                    subfield=getattr(signal.config, "subfield", None),
                    weight=signal.weight,
                    score=score,
                    contribution=signal.weight * score,
                )
            )

        if len(contributions) < self.min_signals:
            return None

        fired = [c for c in contributions if c.score > 0.0]
        if self.require_identity_signal and not any(
            c.signal_type in IDENTITY_SIGNAL_TYPES for c in fired
        ):
            return None

        total_weight = sum(c.weight for c in contributions)
        if total_weight <= 0:
            return None
        total_score = sum(c.contribution for c in contributions) / total_weight

        if total_score < self.min_total_score:
            return None

        return ScoredPair(
            predecessor=pred,
            successor=cand,
            total_score=total_score,
            signals=contributions,
            matched_on=sorted({c.signal_type for c in fired}),
            signals_present=len(contributions),
        )
