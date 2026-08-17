"""Set-identity of two pair populations, independent of the metric counts.

Eleven aggregate counts can coincide across a population that has genuinely
changed -- a pair lost and a pair gained in the same score band cancel out in
every one of them. This is the check no count can satisfy, and it is what the
compatibility gate leans on to say a refactor moved nothing.

Composite pair ids are label-independent by construction (compute_id over
literal p/s keys), so this comparison stays valid across the entity-key rename.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "compare_pair_ids.py"


def _load():
    spec = importlib.util.spec_from_file_location("compare_pair_ids", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_pair_ids = _load()


def test_identical_sets_report_no_difference():
    result = compare_pair_ids.diff_id_sets({"a", "b"}, {"a", "b"})
    assert result.identical
    assert result.only_in_baseline == []
    assert result.only_in_candidate == []


def test_a_lost_and_a_gained_pair_are_both_reported():
    # The cancelling case: same count, different population. This is the whole
    # reason the check exists alongside the metric comparison.
    result = compare_pair_ids.diff_id_sets({"a", "b"}, {"a", "c"})
    assert not result.identical
    assert result.only_in_baseline == ["b"]
    assert result.only_in_candidate == ["c"]


def test_differences_are_sorted_for_a_stable_report():
    # A report that reorders between runs cannot be diffed against itself.
    result = compare_pair_ids.diff_id_sets({"z", "y", "x"}, set())
    assert result.only_in_baseline == ["x", "y", "z"]


def test_an_empty_candidate_is_not_mistaken_for_agreement():
    # A sweep that wrote nothing must fail loudly, not read as "no differences
    # found". This is the shape a broken run actually takes.
    result = compare_pair_ids.diff_id_sets({"a"}, set())
    assert not result.identical
    assert result.only_in_baseline == ["a"]


def test_two_empty_sets_are_identical_but_reported_as_empty():
    result = compare_pair_ids.diff_id_sets(set(), set())
    assert result.identical
    assert result.counts == (0, 0)


def test_counts_report_both_population_sizes():
    result = compare_pair_ids.diff_id_sets({"a", "b"}, {"a"})
    assert result.counts == (2, 1)
