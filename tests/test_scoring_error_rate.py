"""A sweep where most of the work failed is not a sweep that completed.

Per-pair errors were once logged and counted and nothing more, so a run in
which EVERY comparison raised still logged "entity-match complete" and exited
cleanly. Measured 2026-08-19 on a second project's first sweep: 532,529
candidates examined, 532,529 errors, 0 pairs — caused by a config key the
schema called optional that the signal read with no default.

The first version of the guard was then wrong in a way a review caught: it
divided one combined error count by `candidates`, which counts only the lookups
that SUCCEEDED. A systemic failure in candidate retrieval therefore left
`candidates` at zero, the zero-check returned early, and the guard stayed
silent on the most severe form of exactly the failure it exists to catch.
`test_total_lookup_failure_raises` is that case.

Each failure kind is now judged against what was ATTEMPTED, not what
succeeded.
"""

import logging

import pytest

from phase_providers.phase_entity_match import (
    MAX_SCORING_ERROR_SHARE,
    PhaseEntityMatch,
)


def _phase():
    return PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)


def _stats(predecessors=0, candidates=0, lookup=0, scoring=0, index=0):
    return {
        "predecessors": predecessors,
        "candidates": candidates,
        "lookup_errors": lookup,
        "scoring_errors": scoring,
        "index_errors": index,
    }


# --- the case the first version missed -------------------------------------


def test_total_lookup_failure_raises():
    """Every candidate lookup raised, so nothing was ever compared.

    The regression this file exists for, and the shape is the whole point:
    `candidates` is 0 precisely BECAUSE the failure was total. A guard keyed on
    `candidates` divides by a number its own failure suppressed, short-circuits
    on the zero, and reports nothing wrong. Keyed on `predecessors` — what was
    attempted — it reads 100%.
    """
    with pytest.raises(RuntimeError, match="candidate lookups") as excinfo:
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=5419, candidates=0, lookup=5419)
        )
    assert "100.00%" in str(excinfo.value)


def test_errors_with_nothing_attempted_still_raise():
    """Defensive: unreachable today, and cheap to keep that way.

    Both counters increment inside loops over the thing they count, so an
    error with zero attempts should be impossible. It is guarded anyway
    because the alternative to a clear failure here is ZeroDivisionError, and
    because the arithmetic that makes it unreachable is exactly the kind that
    a later refactor can quietly break.
    """
    with pytest.raises(RuntimeError, match="failed all"):
        _phase()._raise_on_catastrophic_error_rate(_stats(predecessors=0, lookup=3))


def test_partial_lookup_failure_above_the_ceiling_raises():
    with pytest.raises(RuntimeError, match="candidate lookups"):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=500, lookup=500)
        )


def test_a_few_lookup_failures_are_survivable(caplog):
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=99_000, lookup=5)
        )
    assert any("candidate lookups failed" in r.message for r in caplog.records)


# --- scoring failures ------------------------------------------------------


def test_total_scoring_failure_raises():
    # The measured case: every comparison raised on a missing config key.
    with pytest.raises(RuntimeError, match="comparisons"):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=5419, candidates=532_529, scoring=532_529)
        )


def test_a_clean_run_does_not_raise():
    _phase()._raise_on_catastrophic_error_rate(
        _stats(predecessors=48_540, candidates=22_316_064)
    )


def test_a_handful_of_scoring_failures_is_survivable(caplog):
    # A few bad records across a large sweep is what the per-pair try/except is
    # for. Discarding the whole run over them would be the worse failure.
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=100_000, scoring=12)
        )
    assert any("unaffected" in r.message for r in caplog.records)


def test_the_threshold_is_inclusive_at_the_ceiling():
    at_ceiling = int(100_000 * MAX_SCORING_ERROR_SHARE)
    with pytest.raises(RuntimeError):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=100_000, scoring=at_ceiling)
        )


def test_just_below_the_threshold_warns_rather_than_raising(caplog):
    below = int(100_000 * MAX_SCORING_ERROR_SHARE) - 1
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=100_000, scoring=below)
        )
    assert any("were skipped" in r.message for r in caplog.records)


# --- index write failures are a different thing ----------------------------


def test_index_write_failures_warn_but_never_raise(caplog):
    """A cluster refusing a document is not a comparison being wrong.

    Folding these into the comparison share would let a transient write
    problem be reported as a configuration fault, and the message would name
    the wrong cause for someone trying to fix it.
    """
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=1000, candidates=100_000, index=100_000)
        )
    assert any("write failures" in r.message for r in caplog.records)


def test_index_failures_do_not_push_comparisons_over_the_ceiling():
    # Combined counting would raise here; separate counting must not.
    _phase()._raise_on_catastrophic_error_rate(
        _stats(predecessors=1000, candidates=1000, scoring=1, index=500)
    )


# --- degenerate inputs -----------------------------------------------------


def test_a_sweep_that_attempted_nothing_at_all_does_not_raise():
    # An empty or unreachable source index is a different problem, and the
    # zero-pairs warning already names it. Raising here would replace a clear
    # message with a confusing one.
    _phase()._raise_on_catastrophic_error_rate(_stats())


def test_the_message_names_the_counts_so_the_cause_can_be_found():
    with pytest.raises(RuntimeError) as excinfo:
        _phase()._raise_on_catastrophic_error_rate(
            _stats(predecessors=100, candidates=1_000, scoring=1_000)
        )
    message = str(excinfo.value)
    assert "1000" in message
    assert "100" in message  # the percentage
