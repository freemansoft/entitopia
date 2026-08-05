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


def fingerprint_analysis(settings):
    """Hash the analysis block of an index-settings payload; None when absent.

    Takes the plain dict handed to indices.create rather than the SimpleNamespace
    config, because that dict is the exact structure that reached Elasticsearch —
    hashing anything earlier in the pipeline would let a serialization change move
    the fingerprint without any analyzer changing, which is the one thing a
    staleness check must never do.

    An absent analysis block and a present-but-empty one (`{}`) both return
    None: neither declares anything to fingerprint, so there is nothing to
    compare and no reason to distinguish them. The alternative — hashing `{}`
    — would give every analyzer-free index across the whole cluster the same
    non-None fingerprint, so two indices that share nothing but the absence of
    analyzers would compare equal and report a false match.
    """
    if not settings:
        return None
    analysis = (settings.get("index") or {}).get("analysis") or settings.get("analysis")
    if not analysis:
        return None
    canonical = json.dumps(analysis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
