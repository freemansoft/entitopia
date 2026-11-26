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
        # state_field is listed because the address signal once hard-coded
        # "phy_state", a field the hospital index does not have, and nothing
        # here could see it: every fuzzy hospital address match was halved.
        for key in ("fields", "phone_fields", "text_fields", "name_field", "state_field"):
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
    for key in ("shutdown_date", "registration_date", "shutdown_reason", "end_date"):
        yield lifecycle.get(key), "lifecycle.{}".format(key)

    for rule in (raw.get("declared_links") or {}).get("rules") or []:
        yield rule.get("field"), "declared_links rule {!r}".format(rule.get("name"))

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


def _check_identifier_keywords(raw, paths, subfields, index, source) -> list[str]:
    """Report exact-identifier fields whose `.keyword` subfield is undeclared.

    The signal aggregates on `<field>.keyword` to find shared values to
    suppress, and for a text field it also seeds candidates with a term query
    on it. A phone field seeds on its normalized `.clean` subfield instead, so
    there only the frequency scan goes blind. On a field mapped as a bare
    keyword the reads find nothing, yet scoring still runs and the sweep logs
    success. A bare-keyword website field did exactly that, and the only symptom
    was counts identical to a run without the signal. Same silent-failure
    class as the undeclared analyzed subfields above.
    """
    # What a missing `.keyword` costs differs by kind, and the message must say
    # which: telling an operator the phone seed is dead sends them after the
    # wrong read.
    consequence = {
        "phone_fields": "the shared-value scan reads it, so the signal suppresses "
        "no shared value (the seed reads .clean and is unaffected)",
        "text_fields": "the candidate seed and the shared-value scan read it, so "
        "the signal retrieves and suppresses nothing",
    }
    messages = []
    for signal in raw.get("signals", []) or []:
        if signal.get("type") != "exact-identifier":
            continue
        for key in ("phone_fields", "text_fields"):
            for field in signal.get(key) or []:
                # A field the mapping lacks entirely is already reported once.
                if field in paths and "{}.keyword".format(field) not in subfields:
                    messages.append(
                        "{}: exact-identifier {} names {!r}, but the mapping for "
                        "{} declares no {!r} subfield; {}".format(
                            source, key, field, index, field + ".keyword",
                            consequence[key],
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
        *_check_identifier_keywords(entity_match, paths, subfields, index, source),
        *_check_term_clauses(entity_match, types, index, source),
    ]
