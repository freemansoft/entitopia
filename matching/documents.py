"""Data shapes that signals consume.

EntityDoc pairs a record's _source with the analyzed tokens Elasticsearch
produced for it. Tokens come from _mtermvectors rather than being recomputed in
Python, so scoring always sees exactly what the index sees.
"""

import math
from dataclasses import dataclass, field


@dataclass
class EntityDoc:
    """Pairs one record's raw _source with its Elasticsearch-analyzed tokens.

    Tokens are read from _mtermvectors rather than recomputed in Python so
    that scoring always sees the same phonetic encodings and synonym
    expansions the index actually produced, not a local approximation of them.

    `entity_key` is named for its role rather than for any dataset's column.
    It used to be `dot_number`, which meant every project's records had to
    pretend to be FMCSA carriers and made `entity-match` unreachable by
    configuration. There is deliberately no `dot_number` alias property: two
    names for one value give new code no rule about which to reach for, which
    is how the vocabulary got in here to begin with.
    """

    entity_key: str
    source: dict
    # Keyed "field.subfield", e.g. "legal_name.phonetic_bm"
    tokens: dict[str, set[str]] = field(default_factory=dict)

    def token_set(self, field_name: str, subfield: str) -> set[str]:
        """Tokens for one analyzed field, or an empty set if never indexed.

        Signals intersect sets freely; returning empty rather than raising on
        a missing field lets "not indexed for this record" be treated the
        same as "no overlap" without every caller needing a try/except.
        """
        return self.tokens.get("{}.{}".format(field_name, subfield), set())

    def value(self, path: str):
        """Read a dotted path out of this document's _source."""
        return read_path(self.source, path)


def read_path(source: dict, path: str):
    """Read a dotted path out of a raw _source dict.

    Lives outside EntityDoc because candidate *retrieval* needs it too, and
    at that point there is no EntityDoc yet — seed clauses are built from a
    predecessor's raw search hit, before tokens have been fetched. Keeping one
    implementation means a signal reads the same values when seeding the
    candidate query as it does later when scoring the pair; two copies would
    let those drift, and a signal that seeds on values it cannot then score is
    a silent recall bug.

    Enriched fields arrive as lists (max_matches > 1), so walking a path
    through a list collects the value from every element. Collected values
    are flattened at each step because enrichment nests two levels deep:
    a carrier's inspections[] each carry their own units[], so
    "inspections.units.insp_unit_vehicle_id_number" would otherwise produce
    a list of lists and find no dicts at the final step.

    Returns None when any part of the path is missing.
    """
    current = source
    for part in path.split("."):
        if isinstance(current, list):
            collected = []
            for item in _flatten(current):
                if isinstance(item, dict) and part in item:
                    collected.append(item[part])
            if not collected:
                return None
            current = _flatten(collected)
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _flatten(values):
    """One level of list flattening, leaving non-list elements alone.

    Enrichment nests two levels deep — a carrier's inspections[] each carry
    their own units[] — so walking a dotted path without this produces lists
    of lists at the inner level and silently finds nothing.
    """
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


# Below this many records in the corpus, log(N) is 0 or undefined and
# normalized IDF cannot be computed; rarity floors to 0.0 instead.
MIN_RARITY_CORPUS = 2


def normalize_rarity_key(value) -> str:
    """Casefold a value for keying and lookup.

    Must match how a signal normalizes values before intersecting them,
    otherwise a lookup silently misses and every value degrades to the 1.0
    "unseen" fallback — turning the rarity weighting off without any error.

    Shared with is_ignored below for the same reason: an operator writing
    "Unknown" in config must match a record carrying "UNKNOWN", and two
    normalizers that drift apart would disable one of the two silently.
    """
    return str(value).strip().lower()


@dataclass
class FieldRarityTable:
    """Value frequencies for one field, as normalized inverse document frequency.

    Exists because a signal scoring a shared value highly is asserting that the
    value discriminates, and only the corpus can say whether it does. One
    project's shared-filing-agent field carried 89 distinct values across 1.43M
    rows, so two unrelated records share one about 7% of the time by chance;
    unweighted, that signal fires on noise.

    Uses log(N/count)/log(N), NOT 1 - count/N. With 89 values the largest share
    is 9.4%, so 1 - share compresses every value into [0.906, 1.0] and carries
    no discriminating power at all. Normalized IDF spreads the same population
    across [0.167, 1.0].

    This was ScoringContext.agent_rarity, which named one project's field in
    framework code. The arithmetic is unchanged; only the vocabulary is.
    """

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def __post_init__(self):
        # Normalized on the way in so callers cannot introduce a silent case
        # mismatch, regardless of how they built the dict.
        self.counts = {normalize_rarity_key(k): v for k, v in self.counts.items()}

    def rarity(self, value: str) -> float:
        """1.0 for a value nobody uses, near 0.0 for a dominant one.

        Returns 0.0 — the floor of the signal's range, not "neutral" and
        emphatically not "maximally common" — when there is no usable corpus:
        either total is 0 (frequencies were never gathered) or 1 (log(N) is 0,
        making the ratio undefined). A shared value under either condition is
        still real evidence; scoring it 1.0 (the "unseen" placeholder) would
        misrepresent a known value as novel, and inventing a mid-range value
        would fabricate precision the data cannot support. 0.0 makes the
        signal contribute nothing until real statistics exist, rather than
        pretend to a discriminating power it does not have.
        """
        if self.total < MIN_RARITY_CORPUS:
            return 0.0
        count = self.counts.get(normalize_rarity_key(value), 0)
        if count <= 0:
            return 1.0
        return math.log(self.total / count) / math.log(self.total)


@dataclass
class ScoringContext:
    """Corpus-level statistics gathered once per sweep."""

    # Field path -> that field's value frequencies. Keyed by field because the
    # same string can be a dominant value on one field and a rare one on
    # another, so a single global table would misprice both.
    rarity_tables: dict[str, FieldRarityTable] = field(default_factory=dict)
    # Field path -> normalized values that must not be treated as evidence on
    # that field. The key "*" applies to every field. Two sources merge here:
    # values an operator declared in entity-match.json's ignore_values, and
    # values the corpus itself exposed as non-unique (the literal VINs
    # "UNKNOWN" on 79 carriers and "GGGG" on 158). Keyed by field rather than
    # global because a value that is meaningless in one attribute can be
    # perfectly valid in another — "0" is a junk VIN but a real street number.
    ignored_values: dict[str, set[str]] = field(default_factory=dict)

    def is_ignored(self, field_path: str, value: str) -> bool:
        """Whether a value carries no evidence on this particular field.

        A signal whose premise is "this value is unique worldwide" has no
        defensible score when the premise is false: two carriers both
        reporting "UNKNOWN" share nothing. Callers drop the value entirely
        rather than scoring it 0.0, so the signal reports None (no usable
        evidence) instead of "evaluated, matched" — the difference between no
        evidence and damning evidence.
        """
        normalized = normalize_rarity_key(value)
        if normalized in self.ignored_values.get("*", ()):
            return True
        return normalized in self.ignored_values.get(field_path, ())

    def __post_init__(self):
        # Normalize the ignore-list values on the way in: an operator writing
        # "Unknown" in config must match a record carrying "UNKNOWN", or the
        # ignore list silently does nothing. Rarity table keys are normalized
        # by FieldRarityTable itself, for the same reason.
        self.ignored_values = {
            path: {normalize_rarity_key(v) for v in values}
            for path, values in self.ignored_values.items()
        }

    def rarity(self, field_path: str, value: str) -> float:
        """How rare a value is on one field, or 0.0 when nothing was gathered.

        0.0 rather than 1.0 for an absent table, matching FieldRarityTable's
        own floor: no table means frequencies were never collected, and
        treating an unmeasured value as novel would overstate the evidence
        rather than withhold judgement.
        """
        table = self.rarity_tables.get(field_path)
        if table is None:
            return 0.0
        return table.rarity(value)
