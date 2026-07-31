"""Set-overlap math and identifier normalization for entity matching.

Every function here is pure. Signals build on these so that scoring can be
tested without an Elasticsearch cluster.
"""

import re

# A phone that is one digit repeated (0000000000, 1111111111) is a placeholder,
# not a phone number. Left alone these cluster thousands of unrelated carriers.
_REPEATED_DIGIT = re.compile(r"^(\d)\1*$")
_NON_DIGIT = re.compile(r"\D")

MIN_PHONE_DIGITS = 7


def jaccard(a: set[str], b: set[str]) -> float:
    """Intersection over union. 0.0 when either set is empty.

    Used when full, symmetric overlap is the evidence wanted: unlike
    containment, it punishes a subset match, so an abbreviated name scores
    lower here even though abbreviation is a deliberate evasion tactic.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    """Intersection over the smaller set. 0.0 when either set is empty.

    This is what catches abbreviation: "SMITH LLC" is fully contained in
    "SMITH TRUCKING LLC" and scores 1.0, where jaccard scores 0.67.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def blended_overlap(a: set[str], b: set[str]) -> float | None:
    """Half jaccard, half containment. None when either set is empty.

    Pure jaccard would punish name abbreviation, which is one of the evasion
    tactics being hunted. Pure containment would treat any subset as a perfect
    match. Blending keeps full overlap ranked above a subset match while still
    scoring abbreviation highly.
    """
    if not a or not b:
        return None
    return 0.5 * jaccard(a, b) + 0.5 * containment(a, b)


def normalize_phone(value) -> str | None:
    """Digits only, or None when the value can't identify a single carrier.

    Real FMCSA data contains placeholder values like 0000000000 for carriers
    that never supplied a phone number. Left alone these would cluster
    thousands of unrelated carriers into one false match, so they're rejected
    here alongside blanks and numbers too short to be real.
    """
    if value is None:
        return None
    digits = _NON_DIGIT.sub("", str(value))
    if len(digits) < MIN_PHONE_DIGITS:
        return None
    if _REPEATED_DIGIT.match(digits):
        return None
    return digits


def normalize_text_identifier(value) -> str | None:
    """Trimmed and lowercased, or None for blanks.

    Casefolding lets values entered with inconsistent capitalization across
    records (agent names, emails, VINs) still intersect as equal.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None
