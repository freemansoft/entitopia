"""Deterministic document _id construction.

Shared by index-populate (CSV rows) and entity-match (scored pairs) so both
build composite keys the same way.
"""

import hashlib
import json

# Marks an _id that came from the whole-row fallback rather than from the
# configured key. Present in the data on purpose: it is what makes keyless rows
# findable after the fact (`prefix` query on _id) by an operator repairing the
# source, and it is what index-populate counts to report how many rows took the
# fallback.
BLANK_KEY_PREFIX = "blank-key:"


def compute_id(record, id_field):
    """Build a deterministic document _id so re-running an ingest overwrites
    the existing document instead of creating a duplicate.

    Raises KeyError when a named field is absent; callers fall back to
    Elasticsearch auto-generated ids.

    A key that is *present but empty* gets the whole-row hash below instead,
    because the auto-generated id it used to receive is fresh on every run —
    which quietly breaks the overwrite guarantee this function exists to make.
    Measured: one blank `docket_number` among 1,860,604 rows left a re-populated
    index holding 1,860,605 documents, growing by one per reload with nothing
    reporting it.

    An all-empty *composite* key fails the other way and is caught by the same
    fallback: joining empties yields one constant string, so every such row
    would collapse onto a single document rather than each getting its own.

    A *partially* empty composite key is still discriminating, so it keeps the
    join and renders the empty components as nothing. It used to render an
    absent one as `str(None)`, which put a Python repr in the key space and,
    worse, gave a row whose column genuinely held the string `"None"` the same
    `_id` as one where the column was missing — two different rows silently
    overwriting each other. Blankness is judged by `_is_blank` here and for the
    all-empty fallback above, so a component cannot be blank enough to hash the
    row but not blank enough to render as empty.
    """
    fields = id_field if isinstance(id_field, list) else [id_field]
    values = [record[field] for field in fields]
    # Emptiness is judged on the values rather than on the joined string,
    # because only an *entirely* empty key is unusable; a partially empty one
    # still addresses a document and hashing it would throw away a usable key.
    if all(_is_blank(value) for value in values):
        return blank_key_id(record)
    if isinstance(id_field, list):
        return "|".join("" if _is_blank(value) else str(value) for value in values)
    return values[0]


def _is_blank(value):
    """Whether one keyed column contributes nothing that could address a document.

    Whitespace counts as empty: a key of `" "` addresses a document in
    Elasticsearch but not in any source anyone can search or correct.
    """
    return value is None or str(value).strip() == ""


def blank_key_id(record):
    """A stable _id for a row whose configured key is empty.

    Hashes the whole row so the id is a property of the data rather than of the
    run, which is the only thing that makes a reload overwrite rather than
    accumulate. Two byte-identical keyless rows therefore collapse onto one
    document — correct here, since nothing in the row distinguishes them, and
    the alternative is unbounded growth across reloads.

    Keys are sorted so column order in the CSV cannot change the id, and
    `default=str` keeps the hash from depending on which numpy scalar type
    pandas happened to infer for a column.

    Not truncated, unlike the analysis fingerprint: that one is compared for
    equality and a collision merely reports a false match, while a collision
    here silently overwrites one row with a different one.
    """
    canonical = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "{}{}".format(BLANK_KEY_PREFIX, digest)
