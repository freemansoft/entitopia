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
    """One signal's evaluated result for a pair, kept for later explanation.

    Consumed by ScoredPair.signals and by anything rendering a match reason
    to a human (report output, review UI) — it is what lets that consumer say
    which signal moved the score and by how much, not just what the total was.
    """

    signal_type: str
    subfield: str | None
    weight: float
    score: float
    contribution: float


@dataclass
class ScoredPair:
    """A pair that survived every guard, with the evidence that got it there.

    The system's job is to tell a human WHY a pair was flagged, not just that
    it was, so this retains per-signal detail (signals, matched_on) alongside
    the total rather than collapsing to a single score.
    """

    predecessor: CarrierDoc
    successor: CarrierDoc
    total_score: float
    signals: list[SignalContribution] = field(default_factory=list)
    matched_on: list[str] = field(default_factory=list)
    # Count of signals that were evaluable (returned a score, possibly 0.0),
    # not the count that fired (score > 0.0) — that list is matched_on. Do not
    # conflate the two: a signal can be evaluable and still contribute nothing.
    signals_present: int = 0


class PairScorer:
    """Aggregation point: run every configured signal over one pair and decide.

    A sweep generates millions of candidate pairs; most are noise. This is
    the single place that turns one pair's signal scores into a yes/no on
    whether it is worth a human's attention, so that decision logic exists
    in exactly one spot rather than being reimplemented at each call site.

    It owns the guards, so a pair can fire several signals and still be
    rejected — score alone is not sufficient, only score plus a real identity
    link plus enough corroborating evidence. score_pair returning None means
    "not worth reporting," which is how the sweep filters down to a
    reviewable list rather than surfacing every pair that scored above zero.
    """

    def __init__(self, signal_configs, scoring_config):
        """Build the scorer's signals and guard thresholds once, up front.

        Rejecting a zero total configured weight here catches a config error
        immediately, at startup, rather than letting every pair silently fail
        to score. This is distinct from the total_weight <= 0 guard inside
        score_pair: that one covers a pair where every signal happened to be
        unevaluable, a per-pair case this constructor-time check cannot see
        since it only knows the configured weights, not any pair's data.
        """
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
        """Run all signals over a pair, renormalize, and apply the guards.

        Signals that return None (not evaluable, e.g. no BOC-3 record on
        either side) are dropped entirely rather than treated as a zero
        score. The renormalization divisor below is the summed weight of
        only those evaluable signals, not every configured signal's weight —
        dividing by the full configured weight would penalize a carrier for
        missing data instead of judging it neutrally on what could actually
        be evaluated.
        """
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

        # `fired` (score > 0), not `contributions` (merely evaluable), feeds
        # the identity check: around 340,000 carriers have been shut down, so
        # a pair whose only identity signal was evaluable but scored 0.0 has
        # no real identity link and must be rejected even if other,
        # non-identity signals fired.
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
