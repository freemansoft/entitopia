"""The page a human uses to label a review sample, and the summary of their labels.

Exists because the labels are the only recall-adjacent evidence this project
has, and both ends can lose them quietly: a page that breaks on a carrier name
containing markup shows the reviewer the wrong record, and a summary that
counted "unsure" as a miss would understate precision in exactly the bands
where reviewers hesitate most.
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "DOT-Commercial" / "scripts" / "review_page.py"
_spec = importlib.util.spec_from_file_location("review_page", _SCRIPT)
review_page = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review_page)


def row(score, verdict=None, name="ZQET HAULING LLC"):
    return {
        "predecessor": {"dot_number": "1", "legal_name": name},
        "successor": {"dot_number": "2", "legal_name": name},
        "total_score": score,
        "gap_days": 3,
        "matched_on": ["name-token"],
        "signals": [],
        "verdict": verdict,
    }


def test_page_embeds_the_sample_so_a_name_cannot_close_the_script_tag():
    page = review_page.render_page([row(0.8, name="ZQET </script><b>X</b> LLC")], "review-sample.jsonl")
    embedded = page.split('<script type="application/json" id="sample">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded)[0]["predecessor"]["legal_name"] == "ZQET </script><b>X</b> LLC"


def test_page_offers_only_the_three_verdicts_the_summary_understands():
    page = review_page.render_page([row(0.8)], "review-sample.jsonl")
    for verdict in review_page.VERDICTS:
        assert '"{}"'.format(verdict) in page
    assert review_page.VERDICTS == ("chameleon", "not-chameleon", "unsure")


def test_summary_reports_precision_per_band_over_decided_pairs_only():
    rows = [row(0.80, "chameleon"), row(0.85, "not-chameleon"), row(0.75, "unsure"), row(0.72, None),
            row(0.95, "chameleon")]
    summary = review_page.summarize(rows)
    assert summary["0.70-0.90"] == {"chameleon": 1, "not-chameleon": 1, "unsure": 1, "unreviewed": 1,
                                    "precision": 0.5}
    assert summary["0.90-1.00"]["precision"] == 1.0


def test_summary_precision_is_none_when_nothing_in_a_band_was_decided():
    # None, not 0.0: a band nobody judged has no precision, and reporting zero
    # would read as "every pair was wrong".
    assert review_page.summarize([row(0.4, "unsure")])["0.35-0.50"]["precision"] is None
