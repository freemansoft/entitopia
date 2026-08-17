"""Pair scoring: aggregate signals, renormalize, apply guards."""

import logging
from dataclasses import dataclass, field

from matching.documents import EntityDoc, ScoringContext
from matching.signals import _latest_date, build_signal

logger = logging.getLogger(__name__)

# A pair must be tied together by at least one of these. Temporal proximity and
# a shared process agent corroborate; they cannot carry a match on their own.
#
# A shared globally-unique token belongs here rather than among the
# corroborating signals: two carriers reporting the same VIN are operating the
# same physical vehicle, which is a stronger claim of shared identity than a
# similar name. Excluding it made the identity guard reject exactly the pairs
# this signal exists to catch — a carrier that changes its name, address and
# phone but keeps its trucks fires no other identity signal, so the guard
# threw the pair away no matter how the score came out.
IDENTITY_SIGNAL_TYPES = frozenset(
    {
        "name-phonetic",
        "name-token",
        "address",
        "exact-identifier",
        "vin-overlap",
        "shared-token",
    }
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

    predecessor: EntityDoc
    successor: EntityDoc
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

        # Signal types whose firing alone clears min_total_score. Collected
        # here rather than consulted per pair so the guard below stays a set
        # membership test in the hot loop.
        self.conclusive_types = {s.signal_type for s in self.signals if s.conclusive}
        self.min_total_score = float(getattr(scoring_config, "min_total_score", 0.0))
        self.min_signals = int(getattr(scoring_config, "min_signals", 1))
        self.require_identity_signal = bool(
            getattr(scoring_config, "require_identity_signal", True)
        )
        # A gap window the pair must fall inside to be emitted at all, rather
        # than a 0.05-weighted signal it can simply outvote. The temporal
        # signal carries at most ~0.053 of a 0.94 total while the three name
        # signals carry 0.45, so a pair registered years before the shutdown
        # still cleared 0.70 on name and address alone — 42.1% of the >= 0.70
        # tier had exactly that shape and is not a reincarnation by the
        # project's own definition. None on either bound leaves the gate off,
        # so a deployment that has not opted in keeps its population.
        self.min_gap_days = getattr(scoring_config, "min_gap_days", None)
        self.max_gap_days = getattr(scoring_config, "max_gap_days", None)
        # The raw config, not the built Signal: the gate needs the two field
        # paths, and reading them from the same entry that produces the score
        # is what stops the gate and the score from disagreeing about which
        # dates a pair's gap is measured between.
        self._temporal_config = next(
            (c for c in signal_configs if getattr(c, "type", None) == "temporal"), None
        )

    def _gap_outside_window(self, pred, cand):
        """Whether this pair's timing puts it outside the configured window.

        Returns False when either date is unparseable: that is "not evaluable",
        not "incoherent", and dropping it would discard every carrier carrying
        a malformed legacy date — a recall loss wearing a precision gain's
        clothes. Same distinction the signals draw between None and 0.0.
        """
        if self.min_gap_days is None and self.max_gap_days is None:
            return False
        if self._temporal_config is None:
            return False
        shutdown = _latest_date(pred.value(self._temporal_config.predecessor_date))
        registered = _latest_date(cand.value(self._temporal_config.successor_date))
        if shutdown is None or registered is None:
            return False
        gap = (registered - shutdown).days
        if self.min_gap_days is not None and gap < self.min_gap_days:
            return True
        return self.max_gap_days is not None and gap > self.max_gap_days

    def score_pair(  # noqa: PLR0911 -- one guard clause per rejection reason is
        # this module's whole design (see module and class docstrings); folding
        # the new gap-window guard into an existing branch would hide a distinct
        # rejection reason behind another one's return statement.
        self, pred: EntityDoc, cand: EntityDoc, ctx: ScoringContext
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
        if pred.entity_key == cand.entity_key:
            return None

        if self._gap_outside_window(pred, cand):
            return None

        contributions: list[SignalContribution] = []
        evidence_keys: set[frozenset[str]] = set()
        for signal in self.signals:
            score = signal.score(pred, cand, ctx)
            if score is None:
                continue
            evidence_keys.add(signal.evidence_key)
            contributions.append(
                SignalContribution(
                    signal_type=signal.signal_type,
                    subfield=getattr(signal.config, "subfield", None),
                    weight=signal.weight,
                    score=score,
                    contribution=signal.weight * score,
                )
            )

        # Count distinct evidence, not signal instances. Config lists three
        # name signals over the same two fields (two phonetic encoders plus
        # the cleaned form), so counting instances would let a pair matching
        # on nothing but a name clear a floor whose whole purpose is to demand
        # corroboration from a second, independent source.
        if len(evidence_keys) < self.min_signals:
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

        # A conclusive signal that fired overrides the score floor. Averaging
        # cannot represent "this one fact settles it": a shared non-placeholder
        # VIN scores 1.0 at weight 0.08, so a pair sharing nothing else totals
        # about 0.11 against a floor of 0.35 and was discarded — exactly the
        # carrier that changed its name, address and phone but kept its trucks.
        # Raising the weight enough to clear the floor on its own (~0.46) would
        # make that signal dominate every other pair it touches, so the
        # override is expressed as a guard rather than as weight.
        #
        # Only this floor is bypassed. min_signals and require_identity_signal
        # still apply, so a conclusive signal cannot manufacture a match out of
        # a pair with no other evaluable evidence.
        if total_score < self.min_total_score and not any(
            c.signal_type in self.conclusive_types and c.score > 0.0
            for c in contributions
        ):
            return None

        return ScoredPair(
            predecessor=pred,
            successor=cand,
            total_score=total_score,
            signals=contributions,
            matched_on=sorted({c.signal_type for c in fired}),
            signals_present=len(contributions),
        )
