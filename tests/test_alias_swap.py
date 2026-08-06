"""Tests for the alias swap that keeps one alias pointing at exactly one index.

The defect these guard is silent and cumulative: `put_alias` is purely additive,
so every reload left the previous dated index attached and the alias resolved to
a growing union. Nothing errors — reads simply return each document once per
accumulated index, and a sweep reading through the alias scores a mixture of
differently-analyzed tokens while every carrier becomes its own perfect-scoring
successor.

The unit cases pin the action list rather than the outcome because the atomicity
requirement lives in the shape of the request: one `update_aliases` call, not a
remove followed by an add. A test asserting only the final alias membership
would pass against an implementation that leaves readers staring at a
resolves-to-nothing window mid-swap.

The live case exists because the unit fake cannot tell us what Elasticsearch
actually returns when an alias is missing, and that response is the one the
first-creation path depends on.
"""

from types import SimpleNamespace

import pytest
from elasticsearch import Elasticsearch, NotFoundError

from execute_project import apply_args_to_config
from utils.elasticsearch_utils import attach_alias

ALIAS = "test-alias-swap-000001"
FIRST_INDEX = "test-alias-swap-2026.01.01-000001"
SECOND_INDEX = "test-alias-swap-2026.01.02-000001"
THIRD_INDEX = "test-alias-swap-2026.01.03-000001"


class FakeIndicesClient:
    """Captures the action list `attach_alias` builds without an Elasticsearch.

    Records every `update_aliases` call rather than only the last, so a test can
    assert the swap was a single transaction — the property that separates this
    fix from a remove-then-add that briefly resolves to nothing.
    """

    def __init__(self, aliased_indexes=None):
        # None means the alias does not exist yet, which Elasticsearch reports
        # as a 404 rather than as an empty result.
        self.aliased_indexes = aliased_indexes
        self.calls = []

    def get_alias(self, name):
        if self.aliased_indexes is None:
            raise NotFoundError("alias [{}] missing".format(name), None, None)
        return {index: {"aliases": {name: {}}} for index in self.aliased_indexes}

    def update_aliases(self, actions):
        self.calls.append(actions)
        return {"acknowledged": True}


def only_call(fake):
    assert len(fake.calls) == 1, "the swap must be one atomic call, got {}".format(
        len(fake.calls)
    )
    return fake.calls[0]


def test_first_creation_with_no_existing_alias_is_a_bare_add():
    fake = FakeIndicesClient(aliased_indexes=None)

    attach_alias(fake, FIRST_INDEX, ALIAS)

    assert only_call(fake) == [{"add": {"index": FIRST_INDEX, "alias": ALIAS}}]


def test_one_existing_index_is_removed_as_the_new_one_is_added():
    fake = FakeIndicesClient(aliased_indexes=[FIRST_INDEX])

    attach_alias(fake, SECOND_INDEX, ALIAS)

    assert only_call(fake) == [
        {"remove": {"index": FIRST_INDEX, "alias": ALIAS}},
        {"add": {"index": SECOND_INDEX, "alias": ALIAS}},
    ]


def test_accumulated_indexes_are_all_removed_in_one_call():
    # The state every existing cluster is already in. The remove list is built
    # from live alias membership rather than from an assumption that exactly one
    # index holds it, which is what lets the first run after this lands clean up
    # without a migration script.
    fake = FakeIndicesClient(aliased_indexes=[FIRST_INDEX, SECOND_INDEX])

    attach_alias(fake, THIRD_INDEX, ALIAS)

    assert only_call(fake) == [
        {"remove": {"index": FIRST_INDEX, "alias": ALIAS}},
        {"remove": {"index": SECOND_INDEX, "alias": ALIAS}},
        {"add": {"index": THIRD_INDEX, "alias": ALIAS}},
    ]


def test_rerunning_the_same_day_does_not_remove_and_re_add_the_same_index():
    # `{now/d}` resolves to the same index name, so a same-day re-run must not
    # emit a self-remove. Elasticsearch would accept it, but the transaction
    # would read as though the alias had moved when nothing changed.
    fake = FakeIndicesClient(aliased_indexes=[FIRST_INDEX])

    attach_alias(fake, FIRST_INDEX, ALIAS)

    assert only_call(fake) == [{"add": {"index": FIRST_INDEX, "alias": ALIAS}}]


def test_retain_existing_keeps_the_old_indexes_attached():
    # The opt-in escape hatch for an operator who wants the previous index to
    # keep answering to the alias — the pre-fix additive behavior, reachable
    # only on request.
    fake = FakeIndicesClient(aliased_indexes=[FIRST_INDEX])

    attach_alias(fake, SECOND_INDEX, ALIAS, retain_existing=True)

    assert only_call(fake) == [{"add": {"index": SECOND_INDEX, "alias": ALIAS}}]


def test_retain_existing_does_not_even_ask_which_indexes_hold_the_alias():
    # Retaining means the live membership is irrelevant, so an alias lookup
    # failure must not be able to break a retained add.
    class ExplodingLookup(FakeIndicesClient):
        def get_alias(self, name):
            raise AssertionError("retain_existing must not query alias membership")

    fake = ExplodingLookup(aliased_indexes=[FIRST_INDEX])

    attach_alias(fake, SECOND_INDEX, ALIAS, retain_existing=True)

    assert only_call(fake) == [{"add": {"index": SECOND_INDEX, "alias": ALIAS}}]


def args_with(retain_aliases):
    return SimpleNamespace(
        step=None, phase=None, num_rows=None, retain_aliases=retain_aliases
    )


def test_alias_retention_is_off_unless_the_operator_asks_for_it():
    # The default is the whole point of the fix: an operator who runs a reload
    # the way every existing script and README already runs one must get a
    # single-index alias without knowing this flag exists.
    config = apply_args_to_config(SimpleNamespace(steps=[]), args_with(False))

    assert getattr(config, "retain_aliases", False) is False


def test_retain_aliases_flag_reaches_the_config_the_phase_reads():
    config = apply_args_to_config(SimpleNamespace(steps=[]), args_with(True))

    assert config.retain_aliases is True


@pytest.fixture
def live_client():
    """Real Elasticsearch, skipped when unreachable, cleaned up on both paths.

    The unit fake asserts what we ask Elasticsearch to do; only this asserts
    that Elasticsearch does what we think. Specifically it covers the missing
    alias 404, whose response body is an error document rather than an empty
    result — reading that body as alias membership was the trap in the design
    spec's sketch.
    """
    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}],
        request_timeout=30,
    )
    try:
        reachable = client.ping()
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("Elasticsearch is not reachable on localhost:9200")

    for index in (FIRST_INDEX, SECOND_INDEX):
        client.options(ignore_status=404).indices.delete(index=index)
    yield client
    for index in (FIRST_INDEX, SECOND_INDEX):
        client.options(ignore_status=404).indices.delete(index=index)


def test_live_alias_resolves_to_one_index_with_undoubled_counts(live_client):
    indices = live_client.indices
    indices.create(index=FIRST_INDEX)
    attach_alias(indices, FIRST_INDEX, ALIAS)
    indices.create(index=SECOND_INDEX)
    attach_alias(indices, SECOND_INDEX, ALIAS)

    assert set(indices.get_alias(name=ALIAS).body) == {SECOND_INDEX}

    # The count assertion, not the membership one, is what would have caught the
    # original defect: an alias can list the index you expect and still answer
    # reads from another one attached alongside it. Both indexes carry a
    # document so a swap that silently left the first attached would double it.
    for index in (FIRST_INDEX, SECOND_INDEX):
        live_client.index(index=index, document={"dot_number": "0000001"}, refresh=True)
    assert (
        live_client.count(index=ALIAS)["count"]
        == live_client.count(index=SECOND_INDEX)["count"]
        == 1
    )
