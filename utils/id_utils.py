"""Deterministic document _id construction.

Shared by index-populate (CSV rows) and entity-match (scored pairs) so both
build composite keys the same way.
"""


def compute_id(record, id_field):
    """Build a deterministic document _id so re-running an ingest overwrites
    the existing document instead of creating a duplicate.

    Raises KeyError when a named field is absent; callers fall back to
    Elasticsearch auto-generated ids.
    """
    if isinstance(id_field, list):
        return "|".join(str(record[field]) for field in id_field)
    return record[id_field]
