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


# Elasticsearch's fuzziness: AUTO thresholds: no edits for a word of up to 2
# letters, one edit up to 5, two beyond. Copied so the scorer credits exactly
# what a fuzzy match query would; a looser budget on short words makes every
# two- and three-letter word collide with dozens of others.
AUTO_NO_EDITS_MAX_LEN = 2
AUTO_ONE_EDIT_MAX_LEN = 5


def levenshtein(a: str, b: str) -> int:
    """Single-letter insertions, deletions and substitutions turning a into b."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def auto_edit_budget(word: str) -> int:
    """The edits Elasticsearch's fuzziness: AUTO allows for a word this long."""
    if len(word) <= AUTO_NO_EDITS_MAX_LEN:
        return 0
    return 1 if len(word) <= AUTO_ONE_EDIT_MAX_LEN else 2


def _within_budget(x: str, y: str) -> bool:
    # The shorter word sets the budget. That keeps the test symmetric, and it
    # stops a long word from licensing a two-edit match to a short one.
    budget = auto_edit_budget(min(x, y, key=len))
    return abs(len(x) - len(y)) <= budget and levenshtein(x, y) <= budget


def _max_fuzzy_matching(a: set[str], b: set[str]) -> int:
    """Most words of a that can each be paired with a distinct word of b.

    A maximum bipartite matching rather than a greedy pass: greedy pairing
    depends on iteration order, so the same two names could score
    differently from one run or one direction to the next.
    """
    left = sorted(a)
    right = sorted(b)
    edges = [[j for j, y in enumerate(right) if _within_budget(x, y)] for x in left]
    partner = [-1] * len(right)

    def augment(i: int, seen: list[bool]) -> bool:
        for j in edges[i]:
            if not seen[j]:
                seen[j] = True
                if partner[j] == -1 or augment(partner[j], seen):
                    partner[j] = i
                    return True
        return False

    return sum(augment(i, [False] * len(right)) for i in range(len(left)))


def fuzzy_blended_overlap(a: set[str], b: set[str]) -> float | None:
    """blended_overlap, with two words counted equal within the AUTO edit budget.

    Exists for the typo the exact form cannot see. `blended_overlap` needs
    words to match letter for letter, so `QUILBY` against `QUILLBY` costs the
    whole distinctive word, and the phonetic encoders do worse: the typo
    changes the sound code while a look-alike with the same surname keeps it.
    scripts/compare_name_matchers.py measured both on carrier names before
    this was added.

    Each word pairs at most once, so repeating a word cannot inflate the
    score. With every word matching exactly this equals blended_overlap.
    None when either set is empty, as there.
    """
    if not a or not b:
        return None
    matched = _max_fuzzy_matching(a, b)
    jaccard_like = matched / (len(a) + len(b) - matched)
    containment_like = matched / min(len(a), len(b))
    return 0.5 * jaccard_like + 0.5 * containment_like


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
