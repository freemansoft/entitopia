"""Recall and precision against declared links, computed without a cluster.

Split from scripts/measure_declared_links.py so the arithmetic is testable on
plain dataclasses. The failure it exists to prevent is a recall figure read as
matcher quality when the denominator holds links the sweep could never reach: a
target missing from the index, or dated outside the gap window, counts against
recall unless it is named and removed. Every exclusion is therefore reported,
so a reader sees how far the denominator shrank and why.

The output carries counts and ratios only. A link's keys identify real
entities, and a score is an analysis rather than a finding, so none of them may
reach a committed number. sample_bands is the one exception, and its rows carry
real names on purpose: they are for a reviewer to read locally, and
scripts/sample_declared_bands.py refuses to write them outside a data/
directory.
"""

import random
from dataclasses import dataclass
from itertools import pairwise

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
    "predecessor_not_searched",
)

# Upper bounds (exclusive) of each gap bin, in days; the last bin is open.
_GAP_EDGES = (
    (-3650, "<-3650"),
    (-365, "-3650..-366"),
    (-180, "-365..-181"),
    (0, "-180..-1"),
    (366, "0..365"),
)
# The bins of the "Choosing overlap_days" table in FDIC-Banks/README.md. That
# table was first computed by a one-off, because the coarse bins merge the
# gap-0 spike with the empty stretch after it, and the whole floor from -3650
# to -366 into one bin, so they cannot show where the valley between the two
# modes lies.
FINE_GAP_EDGES = (
    (-3650, "<-3650"),
    (-1095, "-3650..-1096"),
    (-365, "-1095..-366"),
    (-180, "-365..-181"),
    (-90, "-180..-91"),
    (-60, "-90..-61"),
    (-30, "-60..-31"),
    (-7, "-30..-8"),
    (0, "-7..-1"),
    (1, "0"),
    (7, "1..6"),
    (366, "7..365"),
)

# The three columns part 1's hand check of FDIC pairs used. explained-missed is
# kept apart from look-alike because it is a defect in the explains rules, not
# in the matcher, and the two have different remedies.
VERDICTS = ("real", "explained-missed", "look-alike")
UNDATED_STRATUM = "undated"


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
    # Set on a fetched target with no parseable registration date, whatever
    # the status: the absorption sweep keeps such targets and measures them,
    # and the reader needs to see how much of its recall rests on them.
    target_undated: bool = False


@dataclass(frozen=True)
class PairRow:
    """One emitted pair reduced to what precision and the band counts need.

    candidate_undated is the absence of the candidate side's add_date on a
    step whose lifecycle names a registration date: the spec's
    `registration_date: unknown`. Read from the pair rather than stored as its
    own field, so it cannot disagree with the date the sweep wrote, and so it
    can be read off pair indexes swept before it existed.
    """

    pred_key: str
    declared: str
    score: float
    candidate_undated: bool = False


def step_is_dated(config) -> bool:
    """Whether a step's lifecycle names a registration date that could be missing.

    On an undated step every pair lacks add_date, and reading that as an
    undated candidate would report the whole sweep as the undated band.
    """
    lifecycle = getattr(config, "lifecycle", None)
    return bool(getattr(lifecycle, "registration_date", None))


def candidate_undated(source: dict, cand_side: str, dated: bool) -> bool:
    """Whether a pair's candidate has no registration date, read off its source.

    One reading for the measurement's undated counts and the sampler's undated
    stratum, so the two describe the same pairs.
    """
    return dated and not (source.get(cand_side) or {}).get("add_date")


def _band_key(band: float) -> str:
    return str(band)


def band_intervals(bands: tuple[float, ...]) -> list[tuple[str, float, float | None]]:
    """(label, lower, upper) for each half-open interval between bands, top open.

    Shared by summarize and sample_bands so the counts a findings table quotes
    and the sample drawn to judge them are cut at the same edges.
    """
    ordered = sorted(bands)
    intervals = []
    for lower, upper in pairwise(ordered):
        intervals.append(("{}-{}".format(lower, upper), lower, upper))
    intervals.append(("{}+".format(ordered[-1]), ordered[-1], None))
    return intervals


def interval_label(score: float, bands: tuple[float, ...]) -> str | None:
    """The interval a score falls in, or None below the lowest band.

    None rather than the lowest interval: a pair under the sweep's floor is
    not something the findings report, and filing it in the lowest interval
    would misstate what that interval holds.
    """
    for label, lower, upper in band_intervals(bands):
        if score >= lower and (upper is None or score < upper):
            return label
    return None


def summarize(
    links: list[LinkOutcome],
    pairs: list[PairRow],
    complete_truth: bool,
    bands: tuple[float, ...] = DEFAULT_BANDS,
) -> dict:
    """Counts and ratios for one half; counts are ints, ratios floats or None.

    A ratio over an empty denominator is None ("not evaluable"), never 0.0
    ("evaluated, nothing found"). A 0.0 recall over links the sweep could not
    reach reads as matcher failure, which is the misreading this module exists
    to prevent. The record still prints in full.

    precision is omitted, not zeroed, unless the truth rules are complete:
    otherwise an unlinked pair may be a real succession the data does not
    record, and counting it as a miss would understate precision.
    """
    unknown = {link.status for link in links} - set(MEASURED) - set(EXCLUSIONS)
    if unknown:
        raise ValueError("unknown link status: {}".format(sorted(unknown)))

    measured = [link for link in links if link.status in MEASURED]
    denominator = len(measured)
    emitted = [link for link in measured if link.status == "emitted"]

    def ratio(count: int) -> float | None:
        return count / denominator if denominator else None

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
    result["undated"] = _undated_counts(links, pairs, bands)
    result["pairs_in_band"] = _pairs_in_band(pairs, bands)
    if complete_truth:
        precision = {}
        for band in bands:
            truth = sum(1 for p in pairs if p.declared == "truth" and p.score >= band)
            none = sum(1 for p in pairs if p.declared == "none" and p.score >= band)
            precision[_band_key(band)] = truth / (truth + none) if truth + none else None
        result["precision_by_band"] = precision
    return result


def _undated_counts(links, pairs, bands):
    """How much of a result rests on successors with no registration date.

    GLEIF's absorption sweep keeps undated candidates and the spec reports
    them as their own band. Without these counts its recall and findings would
    read as if every successor had been dated and placed in the window.
    Measured and excluded links are both counted, because an undated target is
    measured in absorption and excluded from reappearance, and either figure
    alone hides the other sweep's share. Pair counts are cumulative at or above
    each band, read the same way as recall_by_band.
    """
    undated_links = [link for link in links if link.target_undated]
    undated_pairs = [pair for pair in pairs if pair.candidate_undated]
    return {
        "target_links_measured": sum(1 for link in undated_links if link.status in MEASURED),
        "target_links_excluded": sum(
            1 for link in undated_links if link.status in EXCLUSIONS
        ),
        "pairs_by_band": {
            _band_key(band): sum(1 for pair in undated_pairs if pair.score >= band)
            for band in bands
        },
    }


def _pairs_in_band(pairs, bands):
    """Pair counts by tag in each half-open score interval.

    The per-band findings figures for a dataset whose truth is incomplete,
    where no precision can be computed: the reader takes the share of truth as
    truth / (truth + explains + none). Intervals rather than the cumulative
    counts used elsewhere, because the hand-review sample is drawn per interval
    and its verdicts must be set against the same pairs. none_undated is the
    subset of none with an undated candidate, so the share covers every pair
    and the dated findings are the difference.
    """
    counts = {
        label: {"truth": 0, "explains": 0, "none": 0, "none_undated": 0}
        for label, _, _ in band_intervals(bands)
    }
    for pair in pairs:
        label = interval_label(pair.score, bands)
        if label is None or pair.declared not in ("truth", "explains", "none"):
            continue
        counts[label][pair.declared] += 1
        if pair.declared == "none" and pair.candidate_undated:
            counts[label]["none_undated"] += 1
    return counts


def gap_histogram(links: list[LinkOutcome], edges=_GAP_EDGES) -> dict:
    """Days from predecessor shutdown to target registration, binned.

    Shows where the truth links sit relative to the gap window, which is what
    a window is tuned against. Every bin is printed so an empty one is visible.
    edges are exclusive upper bounds; the last bin is open and takes its label
    from the last edge. FINE_GAP_EDGES is for choosing overlap_days, where the
    valley between the two modes has to be read.
    """
    last = ">{}".format(edges[-1][0] - 1)
    labels = [label for _, label in edges] + [last]
    counts = dict.fromkeys(labels, 0)
    for link in links:
        if link.gap_days is None:
            continue
        for edge, label in edges:
            if link.gap_days < edge:
                counts[label] += 1
                break
        else:
            counts[last] += 1
    return counts


def sample_bands(
    pairs: list[dict],
    bands: tuple[float, ...],
    per_band: int,
    seed: int,
    declared: str = "none",
) -> list[dict]:
    """Up to per_band pairs of one declared tag from each interval, for hand review.

    Stratified because a draw from the whole pool lands almost entirely in the
    lowest interval, which is the largest, and leaves the intervals that decide
    the reviewer's threshold with a handful of rows each. Undated candidates
    form their own stratum and are kept out of the intervals, because the
    findings report them as their own band and a pair drawn twice would be
    judged twice.

    Each pool is sorted by _id before drawing: scan order is not stable between
    runs, and a sample that changes with it cannot be handed to a second
    reviewer to check agreement. pairs are pair sources carrying their _id and
    a candidate_undated flag; every dict-valued field is a side's summary and
    is kept for the reviewer to read.
    """
    strata = {label: [] for label, _, _ in band_intervals(bands)}
    strata[UNDATED_STRATUM] = []
    for pair in pairs:
        if pair.get("declared", "none") != declared:
            continue
        label = interval_label(pair["total_score"], bands)
        if label is None:
            continue
        strata[UNDATED_STRATUM if pair.get("candidate_undated") else label].append(pair)

    rng = random.Random(seed)
    rows = []
    for label, pool in strata.items():
        pool.sort(key=lambda pair: pair["_id"])
        for pair in rng.sample(pool, min(per_band, len(pool))):
            row = {
                "_id": pair["_id"],
                "band": label,
                "declared": pair.get("declared", "none"),
                "total_score": pair["total_score"],
                "gap_days": pair.get("gap_days"),
            }
            row.update({k: v for k, v in pair.items() if isinstance(v, dict)})
            # null, never a default: a filled value reads as an answer nobody gave.
            row["verdict"] = None
            rows.append(row)
    return rows
