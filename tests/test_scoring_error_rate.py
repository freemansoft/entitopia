"""A sweep where most comparisons failed is not a sweep that completed.

Per-pair scoring errors were logged and counted and nothing more, so a run in
which EVERY comparison raised still logged "entity-match complete" and exited
cleanly. Measured 2026-08-19 on a second project's first sweep: 532,529
candidates examined, 532,529 errors, 0 pairs — caused by a config key the
schema called optional that the signal read with no default. The only thing
that drew attention was a separate zero-pairs warning; had one pair scored, it
would have read as a small success.

That is this repository's signature failure, so it now raises. The threshold
rather than any-error-at-all is the other half of the decision: one malformed
record must not discard the work done on every other.
"""

import logging

import pytest

from phase_providers.phase_entity_match import (
    MAX_SCORING_ERROR_SHARE,
    PhaseEntityMatch,
)


def _phase():
    return PhaseEntityMatch(es=None, project="p", one_step="s", project_config=None)


def _stats(candidates, errors):
    return {"candidates": candidates, "errors": errors}


def test_a_total_failure_raises():
    # The measured case, scaled down: every comparison failed.
    with pytest.raises(RuntimeError, match="configuration fault"):
        _phase()._raise_on_catastrophic_error_rate(_stats(532_529, 532_529))


def test_a_clean_run_does_not_raise():
    _phase()._raise_on_catastrophic_error_rate(_stats(22_316_064, 0))


def test_a_handful_of_failures_is_survivable(caplog):
    # A few bad records across a large sweep is what the per-pair try/except is
    # for. Discarding the whole run over them would be the worse failure.
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(_stats(100_000, 12))
    assert any("unaffected" in r.message for r in caplog.records)


def test_the_threshold_is_inclusive_at_the_ceiling():
    # At exactly the ceiling the cause has stopped being individual records.
    at_ceiling = int(100_000 * MAX_SCORING_ERROR_SHARE)
    with pytest.raises(RuntimeError):
        _phase()._raise_on_catastrophic_error_rate(_stats(100_000, at_ceiling))


def test_just_below_the_threshold_warns_rather_than_raising(caplog):
    below = int(100_000 * MAX_SCORING_ERROR_SHARE) - 1
    with caplog.at_level(logging.WARNING):
        _phase()._raise_on_catastrophic_error_rate(_stats(100_000, below))
    assert any("were skipped" in r.message for r in caplog.records)


def test_a_sweep_that_examined_nothing_does_not_raise():
    # Zero candidates is a different problem -- an empty or unreachable source
    # index -- and the zero-pairs warning already names it. Dividing by zero
    # here would replace a clear message with a traceback.
    _phase()._raise_on_catastrophic_error_rate(_stats(0, 0))


def test_the_message_names_the_counts_so_the_cause_can_be_found():
    with pytest.raises(RuntimeError) as excinfo:
        _phase()._raise_on_catastrophic_error_rate(_stats(1_000, 1_000))
    message = str(excinfo.value)
    assert "1000" in message
    assert "100" in message  # the percentage
