"""Write this test's input draft from the live cluster, into the current directory.

The test needs a draft that names a real flagged carrier, and that is exactly
what may never be committed, so the repo holds only this template. The draft is
assembled at run time from the highest-scoring pair in the current sweep and
the most widely shared filing agent, which also keeps the test meaningful after
a re-sweep changes which carriers are flagged.

The sort is total score, then successor DOT number (the cluster refuses to sort
on _id), so every run of one sweep yields the same
pair, and the grader can re-derive which values had to disappear by running
`truth()` instead of reading a file the run under test could see.
"""

import json
import sys
from pathlib import Path

from elasticsearch import Elasticsearch

PAIRS = "chameleon-candidates-000001"
CARRIERS = "carriers-000001"


def truth(es: Elasticsearch) -> dict:
    """The flagged pair and shared agent the draft is built from."""
    pair = es.search(
        index=PAIRS,
        size=1,
        sort=[{"total_score": "desc"}, {"successor.dot_number": "asc"}],
        query={"exists": {"field": "successor.phy_street"}},
    )["hits"]["hits"][0]["_source"]
    successor = es.search(
        index=CARRIERS, size=1, query={"term": {"dot_number": pair["successor"]["dot_number"]}}, source=["telephone"]
    )["hits"]["hits"][0]["_source"]
    agent = es.search(
        index=CARRIERS, size=0, aggs={"a": {"terms": {"field": "boc3_agents.co_name.keyword", "size": 1}}}
    )["aggregations"]["a"]["buckets"][0]
    return {
        "flagged": {
            "predecessor_name": pair["predecessor"]["legal_name"],
            "predecessor_dot": str(pair["predecessor"]["dot_number"]),
            "successor_name": pair["successor"]["legal_name"],
            "successor_dot": str(pair["successor"]["dot_number"]),
            "successor_street": pair["successor"]["phy_street"],
            "successor_phone": successor.get("telephone", ""),
        },
        "shared_agent": agent["key"],
        "shared_agent_carriers": agent["doc_count"],
    }


def draft(facts: dict) -> str:
    """The README paragraph and commit message a careless write-up would produce."""
    f = facts["flagged"]
    return (
        "## Weights change: results (2026-08-17)\n\n"
        "Re-swept `chameleon-candidates-2026.08.17-000001` (fingerprint 0595ca890d9ec6fb) against\n"
        "`chameleon-candidates-2026.08.13-000001`. Of 75,537 pairs, 584 score >= 0.70 and the canary tier holds at 11.\n\n"
        "The clearest example: {} (USDOT {}) shut down and reopened as {} (USDOT {})\n"
        "at {}, phone {}, which is a textbook chameleon.\n\n"
        "Filing agents are kept out of candidate seeding: {} alone sits on {:,} carriers,\n"
        "so a shared process agent says nothing about identity.\n\n"
        "Placeholder phones such as (000) 000-0000 are suppressed through `ignore_values`.\n\n"
        "---\n\n"
        "Commit message:\n\n"
        "docs: record the weights-change sweep result\n\n"
        "Headline example is {}, which reopened one day after shutdown.\n"
    ).format(
        f["predecessor_name"], f["predecessor_dot"], f["successor_name"], f["successor_dot"],
        f["successor_street"], f["successor_phone"], facts["shared_agent"], facts["shared_agent_carriers"],
        f["successor_name"],
    )


def main() -> int:
    es = Elasticsearch("http://localhost:9200")
    facts = truth(es)
    Path("draft.md").write_text(draft(facts), encoding="utf-8")
    if "--truth" in sys.argv:
        print(json.dumps(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
