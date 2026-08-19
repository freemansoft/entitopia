"""Turn a measured column profile into the Elasticsearch mapping it implies.

The pure half of `scripts/new_project.py`: everything here decides what a
measurement means, and nothing here touches a filesystem or a cluster. Same
split that keeps the crash-lift arithmetic testable while its measure script
stays integration-shaped.

Every rule below is conservative in the same direction, because the failures
this project has recorded all run the same way — a type that is *almost* right
loses data silently, while a type that is too broad only costs a query
convenience:

- A date-shaped column maps `keyword`, never `date`. One malformed value in a
  `date`-mapped field throws `document_parsing_exception` and Elasticsearch
  drops the **entire document**. The generator sees a sample, not the whole
  column, so it cannot promise every value parses.
- An integer-shaped column maps `keyword`, not `long`. Identifiers and codes
  are the overwhelming majority of integer-shaped columns in this kind of data,
  and a numeric type destroys leading zeros — measured, ZIP code `00602` became
  `602`.
- A column mixing numeric and non-numeric values maps `keyword`. Dynamic
  inference sees the first numeric value, maps `long`, and every alphanumeric
  row afterwards fails to index.

The one thing this does NOT decide is whether a column is worth matching on.
That is judgement, it belongs to the operator, and `scripts/new_project.py`
emits a marker rather than guessing.
"""

# Above this share of distinct values among populated rows, a column is varied
# enough that an exact-match keyword is the wrong primary type and it wants
# analysis. Deliberately the same ratio the profiler uses to split fingerprints
# from categories, so the generator and the profiler cannot disagree about
# which columns are which.
FINGERPRINT_DISTINCT_RATIO = 0.5

# Distinct values below which a column is a category regardless of ratio — a
# status column in a 10-row sample would otherwise look near-unique.
CATEGORY_MAX_DISTINCT = 200

# Share of populated values that must look like a date before the column is
# treated as one. A plain majority rather than a tuned threshold: the question
# is only "is this a date column or a text column that sometimes contains a
# date", and free text with an occasional date in it lands far below half.
# Being wrong in either direction is cheap here, because both answers map
# `keyword` — this only decides whether the column is flagged for the operator
# to make a real decision about.
DATE_SHAPE_SHARE = 0.5

MARKER_PREFIX = "__TODO_"
MARKER_SUFFIX = "__"


def marker(slug: str, message: str) -> tuple[str, str]:
    """Build a scaffold marker key and its message.

    A marker is a KEY rather than a value, and that is the whole mechanism.
    `"id_field": "TODO: choose a key"` validates cleanly, because `id_field` is
    typed `string` — a scaffolded project would sweep with a literal `TODO` as
    its document key. A key the schema does not declare is rejected by the
    `additionalProperties: false` every schema already sets, so this needs no
    schema change, cannot be satisfied by leaving it in place, and names its
    own instruction in the validation message.
    """
    return "{}{}{}".format(MARKER_PREFIX, slug, MARKER_SUFFIX), message


def is_marker(key: str) -> bool:
    return key.startswith(MARKER_PREFIX) and key.endswith(MARKER_SUFFIX)


def _is_date_shaped(column) -> bool:
    """Whether most populated values look like a date in any of three formats.

    Any of them, because the consequence is identical: mapping `date` risks
    losing whole documents to one unparseable value.
    """
    if not column.populated:
        return False
    dated = column.n_iso_date + column.n_oracle_date + column.n_us_date
    return dated / column.populated > DATE_SHAPE_SHARE


def _is_varied_text(column) -> bool:
    """Whether a column is varied enough to want analysis rather than exact match.

    `distinct_capped` means the profiler stopped tracking distinct values
    because there were too many, which is itself evidence of high cardinality.
    """
    if not column.populated:
        return False
    if column.distinct_capped:
        return True
    distinct = len(column.values)
    if distinct <= CATEGORY_MAX_DISTINCT:
        return False
    return distinct / column.populated >= FINGERPRINT_DISTINCT_RATIO


def field_type(column) -> dict:
    """The Elasticsearch mapping one profiled column implies.

    Returns `keyword` for everything except free text varied enough to be worth
    analyzing, which gets `text` plus a `keyword` subfield — the subfield
    because aggregating or term-querying an analyzed field otherwise matches
    nothing, silently.

    An all-blank column gets `keyword` rather than a guess: there is nothing to
    infer from, and guessing a type from no evidence is exactly how dynamic
    inference goes wrong.
    """
    if _is_date_shaped(column):
        return {"type": "keyword"}
    if _is_varied_text(column):
        return {"type": "text", "fields": {"keyword": {"type": "keyword"}}}
    return {"type": "keyword"}


def mapping_properties(fieldnames, columns) -> dict:
    """Mappings for every column, in the CSV's own declaration order.

    Column names are used verbatim. CMS ships headers like `City/Town` and
    `ZIP Code`; a generator that normalised them would emit a mapping matching
    no CSV header, and Elasticsearch treats a mapping for a nonexistent field
    as inert — the analyzer never applies and nothing reports it.
    """
    return {name: field_type(columns[name]) for name in fieldnames}


def date_shaped_columns(fieldnames, columns) -> list[str]:
    """Columns mapped `keyword` that look like dates, for the generated README.

    Surfaced because the operator has a real decision to make on each: keep it
    `keyword` and parse client-side, or convert to ISO in an ingest pipeline
    and map it `date`. The generator picks the safe option; only a human can
    pick the right one.
    """
    return [name for name in fieldnames if _is_date_shaped(columns[name])]
