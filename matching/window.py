"""One definition of which candidates a predecessor's shutdown admits.

Retrieval (matching/candidates.py) and the scorer gate (matching/scorer.py)
each used to read min_gap_days and max_gap_days separately and apply the rule
in their own idiom, one as a query clause and one as a Python comparison. Two
copies can drift: a candidate retrieval keeps but the scorer drops costs a
capped candidate slot for nothing, and one the scorer keeps but retrieval drops
is a silent recall loss. Both now ask this class, and a test pins that the
clause and the predicate agree.
"""

import datetime
from dataclasses import dataclass

from matching.documents import EntityDoc, read_path
from matching.signals import _latest_date

# Fallback look-back when no negative min_gap_days is configured. A successor
# that registered before the shutdown is still plausible (the new entity is
# often set up first), but an unbounded look-back would pull in every old record.
DEFAULT_BACKWARD_WINDOW_DAYS = 180

_UNDATED_CANDIDATES = ("keep", "exclude")
_UNDATED_PREDECESSORS = ("unbounded", "skip")


@dataclass(frozen=True)
class LifecycleWindow:
    """The gap window, the undated rules and the end-date rule, in one place.

    The defaults reproduce the behaviour before these settings existed, so a
    project that sets none of them sweeps exactly as it did: an undated
    candidate passes because the scorer reads a missing date as "not
    evaluable", and an undated predecessor is searched without a window.
    """

    lifecycle: object
    min_gap_days: float | None = None
    max_gap_days: float | None = None
    undated_candidates: str = "keep"
    undated_predecessors: str = "unbounded"

    def __post_init__(self):
        # A typo would otherwise fall through to the default branch and behave
        # as "keep"/"unbounded" while the operator believes otherwise.
        if self.undated_candidates not in _UNDATED_CANDIDATES:
            raise ValueError(
                "undated_candidates must be one of {}, got {!r}".format(
                    _UNDATED_CANDIDATES, self.undated_candidates
                )
            )
        if self.undated_predecessors not in _UNDATED_PREDECESSORS:
            raise ValueError(
                "undated_predecessors must be one of {}, got {!r}".format(
                    _UNDATED_PREDECESSORS, self.undated_predecessors
                )
            )

    @classmethod
    def from_config(cls, lifecycle, scoring_config):
        """Build from the lifecycle and scoring blocks; None when there is no lifecycle.

        None rather than an always-admit window so callers cannot mistake
        "no dated events in this dataset" for "a window that happens to pass
        everything", which matters for duplicate-detection projects.
        """
        if lifecycle is None:
            return None
        return cls(
            lifecycle=lifecycle,
            min_gap_days=getattr(scoring_config, "min_gap_days", None),
            max_gap_days=getattr(scoring_config, "max_gap_days", None),
            undated_candidates=getattr(scoring_config, "undated_candidates", "keep"),
            undated_predecessors=getattr(scoring_config, "undated_predecessors", "unbounded"),
        )

    def shutdown(self, pred_source: dict) -> datetime.date | None:
        """The predecessor's most recent shutdown, read the way the scorer reads it."""
        return _latest_date(read_path(pred_source, self.lifecycle.shutdown_date))

    def skips_predecessor(self, shutdown: datetime.date | None) -> bool:
        """Whether a predecessor with this shutdown gets no candidate search at all.

        Without a shutdown there is no window, so the search is unbounded and
        spends its capped slots on every record the seed matches. A dataset
        where that is noise opts in to skipping; the phase counts the skips so
        the omission shows in the run's totals instead of vanishing.
        """
        return shutdown is None and self.undated_predecessors == "skip"

    def _has_bounds(self) -> bool:
        return self.min_gap_days is not None or self.max_gap_days is not None

    def query_filter(self, shutdown: datetime.date | None) -> list[dict]:
        """The window as a retrieval filter, or [] when it constrains nothing.

        Must keep everything admits() keeps, or a precision gate becomes a
        recall loss. Bounds are inclusive and rounded to whole days because the
        scorer counts whole days. Measured 2026-11-06 on DOT-Commercial, the
        gap window cut the median predecessor's seed hits from 14,128 to 1,095.
        """
        if shutdown is None:
            return []
        filters = []
        registration = self.lifecycle.registration_date
        keep_undated = self.undated_candidates == "keep"
        if self._has_bounds() or not keep_undated:
            if self._has_bounds():
                bounds = {}
                if self.min_gap_days is not None:
                    bounds["gte"] = (
                        shutdown + datetime.timedelta(days=self.min_gap_days)
                    ).isoformat() + "||/d"
                if self.max_gap_days is not None:
                    bounds["lte"] = (
                        shutdown + datetime.timedelta(days=self.max_gap_days)
                    ).isoformat() + "||/d"
                dated = {"range": {registration: bounds}}
            else:
                dated = {"exists": {"field": registration}}
            if keep_undated:
                filters.append(
                    {
                        "bool": {
                            "should": [
                                dated,
                                {"bool": {"must_not": {"exists": {"field": registration}}}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            else:
                filters.append(dated)
        end_date = getattr(self.lifecycle, "end_date", None)
        if end_date:
            # A candidate that ended before the predecessor shut down cannot
            # be its successor; one with no end date is still operating.
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"range": {end_date: {"gte": shutdown.isoformat() + "||/d"}}},
                            {"bool": {"must_not": {"exists": {"field": end_date}}}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        return filters

    def admits(self, pred: EntityDoc, cand: EntityDoc) -> bool:
        """The scorer's gate: the rule query_filter applies, one pair at a time.

        An unparseable date counts as missing, not as incoherent: dropping it
        would discard every record carrying a malformed legacy date, a recall
        loss wearing a precision gain's clothes. Missing means "not evaluable"
        and passes unless undated_candidates is "exclude".
        """
        shutdown = _latest_date(pred.value(self.lifecycle.shutdown_date))
        if shutdown is None:
            return True
        registered = _latest_date(cand.value(self.lifecycle.registration_date))
        if registered is None:
            if self.undated_candidates == "exclude":
                return False
        elif self._has_bounds():
            gap = (registered - shutdown).days
            if self.min_gap_days is not None and gap < self.min_gap_days:
                return False
            if self.max_gap_days is not None and gap > self.max_gap_days:
                return False
        end_date = getattr(self.lifecycle, "end_date", None)
        if end_date:
            ended = _latest_date(cand.value(end_date))
            if ended is not None and ended < shutdown:
                return False
        return True

    def backward_window_days(self) -> float:
        """How far before a shutdown a successor may have registered.

        Consumed by callers that need a look-back horizon; falls back to
        DEFAULT_BACKWARD_WINDOW_DAYS so an unset min_gap_days does not mean
        an unbounded look-back.
        """
        if self.min_gap_days is not None and self.min_gap_days < 0:
            return abs(self.min_gap_days)
        return DEFAULT_BACKWARD_WINDOW_DAYS
