"""Compare what a dataset's index-mappings.json pins against what its CSV holds.

Exists because profiling the data and reading the mappings were separate acts
and nothing compared the two. The loader used to infer a type per column, which
hid the gap: an unpinned numeric column still arrived numeric, so nobody had to
notice it was unpinned. Once the loader reads every column as a string --
necessary, because inference destroyed leading zeros before Elasticsearch was
reached -- an unpinned column becomes `text` instead. Measured when that change
was made: 66 fields across three DOT-Commercial datasets changed type silently,
one of them a field utils/crash_lift.py documents as depending on `long`.

The four datasets that were already fully pinned moved not one field. That is
the whole argument for this check: "pin every field you rely on" is the
project's stated rule, and this is the first thing that can tell you whether a
project actually follows it, before a reload rather than after.

Kept free of Elasticsearch and pandas imports so it stays callable from a test
with plain lists, the same split that keeps utils/sweep_compare.py testable
while scripts/compare_sweeps.py stays integration-shaped.
"""

import re
from dataclasses import dataclass, field

# Leading zero on a multi-character value: the padding is data, and any numeric
# type discards it. `0` alone is just zero.
ZERO_PADDED = re.compile(r"^0\d")
INTEGER = re.compile(r"^-?\d+$")
DECIMAL = re.compile(r"^-?\d*\.\d+$")
BOOLEANS = frozenset({"true", "false"})

# Oracle-style `01-JUN-74` as FMCSA ships it, and US `3/15/2002`. Elasticsearch
# auto-detects neither, so a column of these lands as text and no range query
# on it works. Mapping them as `date` is its own trap -- Java's `yy` pivots to
# 2000-2099, turning a 1974 registration into 2074 -- which is why this only
# reports the shape and leaves the remedy to the reader.
DATE_SHAPES = (
    re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
)


@dataclass
class Coverage:
    """What one dataset's configuration does and does not account for.

    `unpinned` is the actionable half -- every one of those columns lands as
    `text`. `dead` is quieter but means the extract changed shape under the
    config, which usually invalidates more than the one pin.
    """

    unpinned: list = field(default_factory=list)
    dead: list = field(default_factory=list)

    @property
    def covered(self):
        return not self.unpinned and not self.dead


def compare(columns, pinned, enriched=()):
    """Which CSV columns lack a pin, and which pins name nothing in the CSV.

    `enriched` names fields written onto the document after load by an enrich
    processor -- carriers pins `inspections` and `crashes` as enrichment
    targets, and they are correctly absent from the CSV. Without this the check
    would report them as stale on the one project that uses enrichment, and a
    check that cries wolf gets switched off.
    """
    columns = list(columns)
    pinned = list(pinned)
    exempt = set(enriched)
    return Coverage(
        unpinned=sorted(c for c in columns if c not in set(pinned)),
        dead=sorted(p for p in pinned if p not in set(columns) and p not in exempt),
    )


def looks_like_date(values):
    """Whether a column carries dates Elasticsearch will not auto-detect.

    Separate from recommend_type because the answer is not a mapping type. A
    non-ISO date column is the one case where "leave it as text" is actively
    wrong and "map it as date" is also wrong, so the check reports the shape
    and points at the README rather than pretending there is a safe pin.
    """
    present = _present(values)
    return bool(present) and all(
        any(shape.match(v) for shape in DATE_SHAPES) for v in present
    )


def _present(values):
    """Values that carry evidence, blanks dropped.

    The same "not evaluable" versus "evaluated and disagreed" distinction the
    scoring model draws: one stray blank must not change a column's verdict.
    """
    return [str(v).strip() for v in values if v is not None and str(v).strip() != ""]


def recommend_type(values):
    """The mapping type a column's own values argue for, or None to leave it text.

    None means "text is genuinely right" rather than "could not decide": a
    column mixing numbers and words is safe as text, and choosing between text
    and keyword there is a query-shape decision a human should make.

    Blanks are skipped rather than counted, the same "not evaluable" versus
    "evaluated and disagreed" distinction the scoring model draws -- one stray
    blank must not downgrade a zero-padded key.

    Recommends `double`, never `float`, for fractional columns. Elasticsearch
    `float` is 32-bit and rounds integers above 2^24 to even; on this project's
    own data that made `term final_status_date=20250919` and `=20250920` return
    the same 39,400 documents.
    """
    present = _present(values)
    if not present:
        return None
    if any(ZERO_PADDED.match(v) for v in present) and all(INTEGER.match(v) for v in present):
        return "keyword"
    if all(v.lower() in BOOLEANS for v in present):
        return "boolean"
    if all(INTEGER.match(v) for v in present):
        return "long"
    if all(INTEGER.match(v) or DECIMAL.match(v) for v in present):
        return "double"
    return None
