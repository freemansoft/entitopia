"""The contract every dataset must meet to join carriers on `dot_number`.

These exist because the join does not compare what the mappings say. An enrich
index maps every field `keyword` regardless of the source mapping and reindexes
the source `_source` verbatim, so what actually decides a match is the string
each side's `_source` value renders to. Two datasets can therefore declare the
same type and still intersect to nothing, or declare different types and match
perfectly — which is exactly what this project shipped: `carriers` holds the
string `"23680"` while `crashes` holds the integer `23680`, and the join
works only because both render the same digits.

That makes the contract unenforceable by mapping alone, and a broken join is
the quietest failure this data has: a `float`/`keyword` mismatch on `crashes`
once produced **zero** enrich matches with no error anywhere, and restoring the
loader's zero-padding took `auth-history` and `boc3-agents` from 546,042 and
565,299 enriched documents to zero, again silently. Both were found by hand,
long after the fact.

So the contract is pinned here instead: every joined dataset normalizes
`dot_number` to one canonical string in its ingest pipeline, and declares it
`keyword` so the mapping documents the same thing the pipeline enforces. The
pipeline tests simulate the shipped pipeline against the shapes that have
actually broken this join, rather than asserting anything about the script's
text, because the text is not what runs.

Skipped rather than failed when Elasticsearch is unreachable, so the suite
stays runnable without Docker; the mapping test needs no cluster.
"""

import json

import pytest
from elasticsearch import Elasticsearch

ES_URL = "http://localhost:9200"

# Every dataset carriers enriches from on `dot_number`, plus carriers itself.
# `inspections-per-unit` is deliberately absent: it chains off `inspection_id`
# into inspections, never touching this join.
JOINED_DATASETS = [
    "carriers",
    "crashes",
    "inspections",
    "auth-history",
    "boc3-agents",
    "out-of-service-orders",
]

# Datasets whose pipeline must normalize the value. `carriers` is excluded
# because its pipeline enriches *from* the others and its CSV is the unpadded
# side of the join — it defines the canonical form rather than converting to it.
NORMALIZING_DATASETS = [
    "crashes",
    "inspections",
    "auth-history",
    "boc3-agents",
    "out-of-service-orders",
]

MAPPINGS = "DOT-Commercial/configuration/{}/index-mappings.json"
PIPELINES = "DOT-Commercial/configuration/{}-ingestion-setup/pipelines.json"

# Each shape has broken this join in production, except the plain one.
#   "00023680" — FMCSA zero-pads to eight characters in auth_history.csv and
#                boc3_agents.csv and not in carriers.csv.
#   "3240797.0" — what a column looks like when a NaN widens it to float64.
#   "0"        — an FMCSA placeholder, on 159,140 boc3-agents rows. It must
#                survive as "0" rather than being dropped or blanked.
CANONICAL_CASES = [
    ("23680", "23680"),
    ("00023680", "23680"),
    ("3240797.0", "3240797"),
    ("0", "0"),
]


@pytest.fixture(scope="module")
def es():
    client = Elasticsearch(ES_URL)
    try:
        if not client.ping():
            pytest.skip("Elasticsearch is not reachable at {}".format(ES_URL))
    except Exception:
        pytest.skip("Elasticsearch is not reachable at {}".format(ES_URL))
    return client


def load(path):
    with open(path) as handle:
        return json.load(handle)


def simulate(es, dataset, dot_number):
    """Run a dataset's shipped pipeline over one document and return its _source.

    Carries `inspection_id` because the whole pipeline is simulated rather than
    the dot_number processor alone — inspections chains a second enrich off that
    field and fails the document without it. Simulating the real pipeline is the
    point: a processor that normalizes correctly in isolation but sits after
    something that throws is still a broken pipeline.
    """
    pipeline = load(PIPELINES.format(dataset))
    response = es.ingest.simulate(
        pipeline={"processors": pipeline["processors"]},
        docs=[
            {
                "_index": dataset,
                "_id": "1",
                "_source": {"dot_number": dot_number, "inspection_id": "1"},
            }
        ],
    )
    doc = response["docs"][0]
    # Surfaced rather than left as a KeyError on ["doc"], which says nothing
    # about which processor refused the document.
    assert "error" not in doc, doc.get("error")
    return doc["doc"]["_source"]


@pytest.mark.parametrize("dataset", JOINED_DATASETS)
def test_every_joined_dataset_declares_dot_number_keyword(dataset):
    # The mapping does not decide the join, but a mapping that disagrees with
    # the pipeline is how the next person is misled about which one does.
    mapping = load(MAPPINGS.format(dataset))
    assert mapping["mappings"]["properties"]["dot_number"] == {"type": "keyword"}


@pytest.mark.parametrize("dataset", NORMALIZING_DATASETS)
@pytest.mark.parametrize(("raw", "expected"), CANONICAL_CASES)
def test_pipeline_normalizes_dot_number_to_a_canonical_string(es, dataset, raw, expected):
    got = simulate(es, dataset, raw)["dot_number"]
    assert got == expected
    # Asserted separately from the value because `23680 == "23680"` is false
    # in Python but the two are the same key to Elasticsearch — an equality
    # check alone would pass on a long and hide the shape this test is for.
    assert isinstance(got, str)


@pytest.mark.parametrize("dataset", NORMALIZING_DATASETS)
def test_pipeline_leaves_a_missing_dot_number_alone(es, dataset):
    # A row with no DOT number cannot join and must not be invented one. It
    # matters that this is absence rather than "0", which is a real placeholder
    # value the same column carries.
    assert simulate(es, dataset, None)["dot_number"] is None
