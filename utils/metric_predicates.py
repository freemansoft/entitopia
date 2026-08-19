"""The closed menu of tests a project may run against one scored pair.

Exists so a project can declare what it measures about its own pairs in JSON
rather than in Python. The menu is closed for the same reason the population
clause menu is: a predicate name nobody implemented must fail loudly, because a
typo that quietly matches nothing reports a metric of zero as though it had been
measured, and zero is a plausible-looking number.

Two of these carry a decision rather than an implementation, and both are the
same decision in different clothes — missing data is not evidence:

`gap_between` / `gap_lte` / `gap_gte` never match a null gap. A pair whose date
could not be parsed is "not evaluable", which is not the same as "outside the
window". Counting it as inside would inflate the coherence metrics this harness
exists to move.

`fields_equal` treats null as not-equal. Two records that both lack a name are
not "the same name"; a naive == would count every pair of nameless records as an
identical-name match. Blank must never match blank.
"""

from matching.scorer import IDENTITY_SIGNAL_TYPES


def _read(pair, path):
    """Read a dotted path out of a pair document, or None."""
    current = pair
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _score(pair) -> float:
    """A pair's score, with a missing one read as 0.0.

    A pair carrying no total_score is malformed, not high-scoring. Reading it
    as missing-and-therefore-passing would let junk into every guarded band.
    """
    return pair.get("total_score") or 0.0


def _matched(pair) -> set:
    return set(pair.get("matched_on") or ())


def _score_gte(value, pair):
    return _score(pair) >= value


def _score_lt(value, pair):
    return _score(pair) < value


def _gap_between(bounds, pair):
    """Inclusive on both ends. A null gap never matches — see the module docstring."""
    gap = pair.get("gap_days")
    if gap is None:
        return False
    low, high = bounds
    return low <= gap <= high


def _gap_lte(value, pair):
    gap = pair.get("gap_days")
    return gap is not None and gap <= value


def _gap_gte(value, pair):
    gap = pair.get("gap_days")
    return gap is not None and gap >= value


def _has_signal_type(types, pair):
    """Whether any of these signal types fired for this pair."""
    return bool(_matched(pair) & set(types))


def _matched_on_equals(types, pair):
    """Exact set equality: this pair matched on these signals and nothing else."""
    return _matched(pair) == set(types)


def _matched_identity_equals(types, pair):
    """Set equality after restricting to identity signals.

    Distinct from matched_on_equals because a corroborating signal firing
    alongside does not change which identity evidence carried the pair. The two
    readings disagreed by 156 pairs on the shipped baseline, which is why the
    project tracks both rather than collapsing them.
    """
    return (_matched(pair) & IDENTITY_SIGNAL_TYPES) == set(types)


def _fields_equal(path, pair):
    """Whether both sides carry the same non-null value for one summary field.

    Null on either side is not-equal — see the module docstring.
    """
    left = _read(pair.get("predecessor") or {}, path)
    right = _read(pair.get("successor") or {}, path)
    return left is not None and left == right


def _signal_count_gte(value, pair):
    return len(pair.get("signals") or ()) >= value


def _all(clauses, pair):
    return all(evaluate(clause, pair) for clause in clauses)


def _any(clauses, pair):
    return any(evaluate(clause, pair) for clause in clauses)


def _not(clause, pair):
    return not evaluate(clause, pair)


_MENU = {
    "score_gte": _score_gte,
    "score_lt": _score_lt,
    "gap_between": _gap_between,
    "gap_lte": _gap_lte,
    "gap_gte": _gap_gte,
    "has_signal_type": _has_signal_type,
    "matched_on_equals": _matched_on_equals,
    "matched_identity_equals": _matched_identity_equals,
    "fields_equal": _fields_equal,
    "signal_count_gte": _signal_count_gte,
    "all": _all,
    "any": _any,
    "not": _not,
}

# Exported so schema/metrics.schema.json's enum is generated from the same
# source as the implementation, rather than restated and left to drift.
PREDICATES = frozenset(_MENU)


def evaluate(predicate: dict, pair: dict) -> bool:
    """Test one predicate against one pair document.

    An empty predicate matches everything: a metric with no filter counts every
    pair, and that is how it says so.

    Requires exactly one key. Two would be ambiguous, and silently applying one
    of them would produce a number nobody could account for.
    """
    if not predicate:
        return True
    if len(predicate) != 1:
        raise ValueError(
            "a predicate must declare exactly one test, found {}".format(
                ", ".join(sorted(predicate)) or "(none)"
            )
        )
    name, value = next(iter(predicate.items()))
    test = _MENU.get(name)
    if test is None:
        raise ValueError(
            "unknown predicate {!r}; known predicates are {}".format(
                name, ", ".join(sorted(PREDICATES))
            )
        )
    return test(value, pair)
