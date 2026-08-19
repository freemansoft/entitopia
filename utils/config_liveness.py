"""Check what the config claims about the source index against the index itself.

Configuration that parses and is inert is this repository's recurring failure,
and the sharpest instance of it is a field name that no longer exists.
Elasticsearch treats a mapping for a nonexistent field as inert: it dynamic-maps
the real field as plain text, the custom analyzer never applies, nothing errors,
and the only symptom is scores that are quietly worse. Three analyzers shipped
that way in one project because the source had renamed its columns.

A signal reading an undeclared subfield fails the same way one layer down. It
gets an empty token set, which the signal reads as "not evaluable" and drops —
indistinguishable from a record that genuinely has no name, and therefore
invisible in every count.

Needs a live cluster, which is why this is the last tier: the two cheaper ones
run in CI and catch everything they can before anything here is attempted.
"""

# Types that cannot be aggregated or matched exactly without a keyword subfield.
# A `term` query against one of these matches zero documents silently — the
# defect that made `{"term": {"tow_away": "Y"}}` return nothing while the data
# was full of them.
_ANALYZED_TYPES = frozenset({"text", "match_only_text", "annotated_text"})


def _walk(properties, prefix=""):
    """Flatten a mapping into (field paths, "path.subfield" paths, types by path).

    Two sets rather than one because they fail differently and deserve
    different messages: an absent field path means the config names a column
    the index does not have, while an absent subfield means the column exists
    but was never analyzed the way the signal expects.

    `properties` nests document structure; `fields` declares alternate
    analyses of one value. Both render as dotted paths, and conflating them
    would report every nested path as a missing subfield.
    """
    paths = set()
    subfields = set()
    types = {}
    for name, spec in (properties or {}).items():
        path = "{}{}".format(prefix, name)
        paths.add(path)
        if "type" in spec:
            types[path] = spec["type"]
        for subfield in (spec.get("fields") or {}):
            subfields.add("{}.{}".format(path, subfield))
        if spec.get("properties"):
            child_paths, child_subfields, child_types = _walk(
                spec["properties"], prefix="{}.".format(path)
            )
            paths |= child_paths
            subfields |= child_subfields
            types.update(child_types)
    return paths, subfields, types


def _signal_field_paths(raw):
    """Every source path the configured signals read, with the key that named it."""
    for signal in raw.get("signals", []) or []:
        for key in ("fields", "phone_fields", "text_fields", "name_field"):
            value = signal.get(key)
            if isinstance(value, str):
                yield signal.get("type"), key, value
            elif isinstance(value, list):
                for item in value:
                    yield signal.get("type"), key, item


def _declared_paths(raw):
    """Every mapping path the config claims exists, paired with what named it.

    Collected in one place so the "does the mapping declare this" rule is
    applied identically to every kind of reference, rather than once per block
    with a chance of drifting.
    """
    entity = raw.get("entity") or {}
    yield entity.get("key"), "entity.key"
    for field in entity.get("summary_fields") or []:
        yield field, "entity.summary_fields"

    yield (raw.get("population") or {}).get("sort_field"), "population.sort_field"

    lifecycle = raw.get("lifecycle") or {}
    for key in ("shutdown_date", "registration_date", "shutdown_reason"):
        yield lifecycle.get(key), "lifecycle.{}".format(key)

    for signal_type, key, path in _signal_field_paths(raw):
        yield path, "signal {!r} {}".format(signal_type, key)


def _check_paths(raw, paths, index, source) -> list[str]:
    """Report every config reference to a field the mapping does not declare."""
    return [
        "{}: {} names {!r}, which the mapping for {} does not declare".format(
            source, description, path, index
        )
        for path, description in _declared_paths(raw)
        if path and path not in paths
    ]


def _check_subfields(raw, paths, subfields, index, source) -> list[str]:
    """Report analyzed subfields a signal reads that the mapping never declared.

    The highest-value check here. An undeclared subfield yields an empty token
    set, which the signal reads as "not evaluable" and drops — the same result
    as a record with no value at all, so it is invisible in every count.

    Fields already reported as missing are skipped: saying it twice would bury
    the distinct problems under repetition.
    """
    messages = []
    for signal in raw.get("signals", []) or []:
        for key in ("subfield", "exact_subfield", "fuzzy_subfield"):
            subfield = signal.get(key)
            if not subfield:
                continue
            for field in signal.get("fields") or []:
                if field not in paths:
                    continue
                if "{}.{}".format(field, subfield) not in subfields:
                    messages.append(
                        "{}: signal {!r} reads subfield {!r} of {!r}, which the "
                        "mapping for {} does not declare; the signal will read an "
                        "empty token set and drop out as unevaluable".format(
                            source, signal.get("type"), subfield, field, index
                        )
                    )
    return messages


def _check_term_clauses(raw, types, index, source) -> list[str]:
    """Report `term` population clauses aimed at analyzed fields.

    A term query against an analyzed field matches zero documents and says
    nothing about it — the defect that made a term filter return an empty
    population while the data was full of matching records.
    """
    selectors = ((raw.get("population") or {}).get("selectors")) or {}
    return [
        "{}: selector {!r} uses a `term` clause on {!r}, which is mapped as "
        "{!r}; a term query against an analyzed field matches zero documents "
        "silently".format(source, name, field, types[field])
        for name, definition in selectors.items()
        for field in (definition.get("term") or {})
        if types.get(field) in _ANALYZED_TYPES
    ]


def check(es, entity_match: dict, source: str) -> list[str]:
    """Validate config against the live source index mapping.

    The mapping is read once and flattened. A config with forty field
    references would otherwise make forty round trips for information a single
    call carries.

    An unreachable index is reported as a finding rather than raised, so it
    lands in the same flat list as everything else the validator found — a
    caller should not have to handle one class of problem differently.
    """
    index = entity_match.get("source_index")
    if not index:
        return []

    try:
        mapping = es.indices.get_mapping(index=index)
    except Exception as e:
        return [
            "{}: could not read the mapping for source_index {!r}: {}".format(
                source, index, e
            )
        ]

    paths = set()
    subfields = set()
    types = {}
    # An alias may resolve to more than one index. Union them: a field present
    # in any is readable, and the sweep reads all of them.
    for body in mapping.values():
        index_paths, index_subfields, index_types = _walk(
            (body.get("mappings") or {}).get("properties")
        )
        paths |= index_paths
        subfields |= index_subfields
        types.update(index_types)

    return [
        *_check_paths(entity_match, paths, index, source),
        *_check_subfields(entity_match, paths, subfields, index, source),
        *_check_term_clauses(entity_match, types, index, source),
    ]
