"""Stable hash of an index's analysis settings, so a sweep can tell whether the
tokens it scores were produced by the configuration currently on disk.

Exists because phase_entity_match can verify that a scored subfield EXISTS but
not that it was built by the current analyzers. Change a synonym list, skip the
reindex, and every address score is computed from stale tokens with no error
anywhere — the silent-wrong-output failure this repo keeps hitting. Callers use
this to make that mismatch visible; it is deliberately not a mechanism for
preventing it.
"""

import hashlib
import json


def _analyzer_bindings(properties, prefix=""):
    """Collect {path: {analyzer, search_analyzer}} from a mappings `properties` tree.

    index-settings.json defines what analyzers exist; index-mappings.json's
    `properties` decides which subfield actually uses which one (e.g.
    `phy_street.tokens` binds to `street_tokens`). A fingerprint that only
    hashed the settings block would stay unchanged if that binding were
    repointed at a different analyzer — exactly the silent-wrong-output
    failure this module exists to catch. Walking `fields` and `properties`
    recursively is necessary because multi-fields (`legal_name.phonetic`) and
    nested objects (`boc3_agents.co_name`) both hide analyzer bindings a
    shallow scan would miss.

    Only `analyzer`/`search_analyzer` are pulled out of each node, not the
    node itself, so an unrelated mapping edit — a new field, a `type` change,
    a keyword field with no analyzer — leaves the fingerprint untouched. A
    fingerprint that moves for reasons that don't affect scoring trains an
    operator to stop trusting it.
    """
    bindings = {}
    if not properties:
        return bindings
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            continue
        path = "{}.{}".format(prefix, name) if prefix else name
        entry = {
            key: definition[key]
            for key in ("analyzer", "search_analyzer")
            if definition.get(key) is not None
        }
        if entry:
            bindings[path] = entry
        bindings.update(_analyzer_bindings(definition.get("fields"), path))
        bindings.update(_analyzer_bindings(definition.get("properties"), path))
    return bindings


def fingerprint_analysis(settings, mapping_properties=None):
    """Hash the analysis block plus analyzer bindings; None when both are absent.

    Takes the plain dicts handed to indices.create/put_mapping rather than the
    SimpleNamespace config, because those dicts are the exact structures that
    reached Elasticsearch — hashing anything earlier in the pipeline would let
    a serialization change move the fingerprint without any analyzer changing,
    which is the one thing a staleness check must never do.

    `mapping_properties` is optional and defaults to None so existing callers
    that only ever had settings in hand keep working unchanged; it should be
    index-mappings.json's `mappings.properties` dict when the caller has it,
    since that is where a subfield's analyzer choice actually lives (see
    `_analyzer_bindings`).

    An absent analysis block and empty bindings are each treated as "declares
    nothing"; only when BOTH sources declare nothing does this return None,
    the same as a present-but-empty analysis block (`{}`). The alternative —
    hashing `{}` — would give every analyzer-free index across the whole
    cluster the same non-None fingerprint, so two indices that share nothing
    but the absence of analyzers would compare equal and report a false
    match. Either source alone declaring something is enough to fingerprint.
    """
    analysis = None
    if settings:
        analysis = (settings.get("index") or {}).get("analysis") or settings.get("analysis")
    bindings = _analyzer_bindings(mapping_properties)
    if not analysis and not bindings:
        return None
    canonical = json.dumps(
        {"analysis": analysis or {}, "bindings": bindings},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
