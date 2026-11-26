"""Recall and precision against declared links, computed without a cluster.

Split from scripts/measure_declared_links.py so the arithmetic is testable on
plain dataclasses. The failure it exists to prevent is a recall figure read as
matcher quality when the denominator holds links the sweep could never reach: a
target missing from the index, or dated outside the gap window, counts against
recall unless it is named and removed. Every exclusion is therefore reported,
so a reader sees how far the denominator shrank and why.

The output carries counts and ratios only. A link's keys identify real
entities, and a score is an analysis rather than a finding, so none of them may
reach a committed number.
"""

from dataclasses import dataclass

DEFAULT_BANDS = (0.35, 0.5, 0.6, 0.7, 0.8, 0.9)

# In the denominator: the sweep had a fair chance at these.
MEASURED = ("emitted", "not_retrieved", "retrieved_not_emitted")
# Out of the denominator. Always printed, even at zero, because an absent key
# reads as "not checked" rather than "none".
EXCLUSIONS = (
    "target_missing_from_index",
    "target_outside_window",
    "target_undated",
    "predecessor_undated",
)

# Upper bounds (exclusive) of each gap bin, in days; the last bin is open.
_GAP_EDGES = (
    (-3650, "<-3650"),
    (-365, "-3650..-366"),
    (-180, "-365..-181"),
    (0, "-180..-1"),
    (366, "0..365"),
)
_GAP_LAST = ">365"


@dataclass(frozen=True)
class LinkOutcome:
    """What happened to one truth link in a sweep.

    status is one of MEASURED or EXCLUSIONS. gap_days is set whenever the
    target was found and both dates exist, whatever the status, because the
    gap histogram is for judging the window and so must include the links the
    window excluded. A predecessor_undated outcome may be built from a target
    the predecessor names but that was never fetched.
    """

    pred_key: str
    target_key: str | None
    status: str
    score: float | None = None
    gap_days: int | None = None


@dataclass(frozen=True)
class PairRow:
    """One emitted pair reduced to what precision needs."""

    pred_key: str
    declared: str
    score: float


def _band_key(band: float) -> str:
    return str(band)


def summarize(
    links: list[LinkOutcome],
    pairs: list[PairRow],
    complete_truth: bool,
    bands: tuple[float, ...] = DEFAULT_BANDS,
) -> dict:
    """Counts and ratios for one half; plain ints and floats only.

    A zero denominator yields 0.0 rather than raising, so an empty half still
    prints a full record. precision is omitted, not zeroed, unless the truth
    rules are complete: otherwise an unlinked pair may be a real succession the
    data does not record, and counting it as a miss would understate precision.
    """
    unknown = {link.status for link in links} - set(MEASURED) - set(EXCLUSIONS)
    if unknown:
        raise ValueError("unknown link status: {}".format(sorted(unknown)))

    measured = [link for link in links if link.status in MEASURED]
    denominator = len(measured)
    emitted = [link for link in measured if link.status == "emitted"]

    def ratio(count: int) -> float:
        return count / denominator if denominator else 0.0

    result = {
        "denominator": denominator,
        "exclusions": {
            name: sum(1 for link in links if link.status == name) for name in EXCLUSIONS
        },
        "recall": ratio(len(emitted)),
        "recall_by_band": {
            _band_key(band): ratio(sum(1 for link in emitted if link.score >= band))
            for band in bands
        },
        "misses": {
            "not_retrieved": sum(1 for link in measured if link.status == "not_retrieved"),
            "retrieved_not_emitted": sum(
                1 for link in measured if link.status == "retrieved_not_emitted"
            ),
        },
        "explains_by_band": {
            _band_key(band): sum(
                1 for pair in pairs if pair.declared == "explains" and pair.score >= band
            )
            for band in bands
        },
    }
    if complete_truth:
        precision = {}
        for band in bands:
            truth = sum(1 for p in pairs if p.declared == "truth" and p.score >= band)
            none = sum(1 for p in pairs if p.declared == "none" and p.score >= band)
            precision[_band_key(band)] = truth / (truth + none) if truth + none else 0.0
        result["precision_by_band"] = precision
    return result


def gap_histogram(links: list[LinkOutcome]) -> dict:
    """Days from predecessor shutdown to target registration, binned.

    Shows where the truth links sit relative to the gap window, which is what
    a window is tuned against. Every bin is printed so an empty one is visible.
    """
    labels = [label for _, label in _GAP_EDGES] + [_GAP_LAST]
    counts = dict.fromkeys(labels, 0)
    for link in links:
        if link.gap_days is None:
            continue
        for edge, label in _GAP_EDGES:
            if link.gap_days < edge:
                counts[label] += 1
                break
        else:
            counts[_GAP_LAST] += 1
    return counts
