"""The anonymization scanner must see FDIC banks the way it sees carriers and hospitals.

Exists because the scanner passed a branch naming real FDIC certificates as
clean. It did search the bank sweeps' pair indexes, since projects are read from
config, but nothing ever reached the lookup: a CERT is written as `CERT <n>` or
`"NEWCERT": "<n>"`, which no key label matched, and most CERTs are shorter
than the five digits a bare number needs. Bank names ending in "Bank" or
"Association" matched no name suffix either. So the scan reported "clean" over
values it never looked up, the quiet-success failure the scanner exists to
prevent.

The declared tag is pinned here too. The naming rule lets a pair FDIC itself
recorded as a succession (`declared: truth`) be named, but never an `explains`
or `none` pair, so a bank whose only pairs are recorded successions must not
read as FLAGGED, and one with a single undeclared pair must.

Every value below is invented.
"""

import importlib.util
from pathlib import Path

_SCANNER = Path(__file__).resolve().parent.parent / ".claude/skills/anonymization-check/scripts/scan_identifiers.py"
_spec = importlib.util.spec_from_file_location("scan_identifiers", _SCANNER)
scan_identifiers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_identifiers)


def _values(text: str, kind: str) -> set[str]:
    """Extracted values of one kind, so each test states only what it is about."""
    return {c.value for c in scan_identifiers.extract(text) if c.kind == kind}


def _project(entity_key: str) -> scan_identifiers.Project:
    """A minimal project, because lookups branch only on its entity key and pair pattern."""
    return scan_identifiers.Project(
        name="P/step",
        source_alias="src-000001",
        entity_key=entity_key,
        pair_pattern="p-pairs-*",
        fields_by_role={},
        ignore=set(),
    )


class _CountingEs:
    """Answers `count` by whether the query asks for truth pairs or for the rest.

    The scanner distinguishes the two only through the query it builds, so the
    fake reads that query rather than trusting call order.
    """

    def __init__(self, truth: int, other: int):
        self.truth, self.other, self.calls = truth, other, []

    def count(self, **kwargs):
        self.calls.append(kwargs)
        query = kwargs["query"]["bool"]
        if any(clause == {"term": {"declared": "truth"}} for clause in query.get("filter", [])):
            return {"count": self.truth}
        if any(clause == {"term": {"declared": "truth"}} for clause in query.get("must_not", [])):
            return {"count": self.other}
        raise AssertionError("pair count query neither selects nor excludes truth pairs")


class _NoSearchEs:
    """Fails if the scanner queries a project it should have skipped."""

    def search(self, **kwargs):
        raise AssertionError("searched a project whose entity key is not a CERT")


class _RecordingEs:
    """Records the one search a CERT lookup makes, so the clause can be checked."""

    def __init__(self):
        self.query = None

    def search(self, **kwargs):
        self.query = kwargs["query"]
        return {"hits": {"total": {"value": 1, "relation": "eq"}, "hits": [{"_source": {"CERT": "990005"}}]}}


def test_labelled_cert_is_extracted():
    assert _values("CERT 990005 merged away.", "cert") == {"990005"}


def test_json_successor_fields_are_extracted_however_short():
    # The shape the test fixtures and configs use; a 1-digit CERT is real.
    assert _values('{"NEWCERT": "990003", "ULTCERT": "990004"}', "cert") == {"990003", "990004"}


def test_markdown_code_label_is_extracted():
    # Docs put the field name in backticks: `NEWCERT` 990005.
    assert _values("a record with `NEWCERT` 990003 and **ULTCERT** 990004", "cert") == {"990003", "990004"}


def test_cert_placeholder_is_not_extracted():
    assert _values("absorbed by <CERT-1>", "cert") == set()


def test_a_percentage_after_the_label_is_not_a_cert():
    # The FDIC README's coverage line reads "acquiring CERT: **99.89%**".
    assert _values("to the acquiring CERT: **99.89% of events**", "cert") == set()


def test_cert_is_not_also_reported_as_a_bare_number():
    assert _values("CERT 990005", "number") == set()


def test_cert_is_looked_up_only_where_the_entity_key_is_a_cert():
    candidate = scan_identifiers.Candidate("cert", "990005")
    assert scan_identifiers.source_lookup(_NoSearchEs(), _project("dot_number"), {}, candidate) == ("", [])
    es = _RecordingEs()
    assert scan_identifiers.source_lookup(es, _project("CERT"), {}, candidate) == ("1", ["990005"])
    assert es.query == {"bool": {"should": [{"term": {"CERT": "990005"}}], "minimum_should_match": 1}}


def test_bank_names_are_extracted():
    text = "Zqet State Bank, ZQET NATIONAL BANK and Vrell Savings and Loan Association."
    assert {"Zqet State Bank", "ZQET NATIONAL BANK", "Vrell Savings and Loan Association"} <= _values(text, "name")


def test_bank_name_keeps_its_town():
    # "First National Bank" alone is shared by hundreds and reads SHARED; the
    # town is what identifies one bank, so it has to reach the lookup.
    assert "First Zqet Bank of Vrellton" in _values("then First Zqet Bank of Vrellton failed", "name")


def test_pair_count_separates_recorded_successions():
    es = _CountingEs(truth=13, other=4)
    assert scan_identifiers.pair_count(es, _project("CERT"), ["990005"]) == (4, 13)


def test_entity_in_truth_pairs_only_is_declared_not_flagged():
    finding = scan_identifiers.Finding(
        scan_identifiers.Candidate("cert", "990005"),
        source_hits={"P/step": "1"},
        flagged_pairs={"P/step": 0},
        truth_pairs={"P/step": 13},
    )
    assert scan_identifiers.verdict_for(finding) == "DECLARED"


def test_one_undeclared_pair_makes_it_flagged():
    finding = scan_identifiers.Finding(
        scan_identifiers.Candidate("cert", "990005"),
        source_hits={"P/step": "1"},
        flagged_pairs={"P/step": 1},
        truth_pairs={"P/step": 13},
    )
    assert scan_identifiers.verdict_for(finding) == "FLAGGED"


def test_sweep_without_declared_links_still_flags_every_pair():
    # DOT and CMS pairs carry no `declared` field; must_not on a missing field
    # keeps them all, so their verdicts cannot change.
    es = _CountingEs(truth=0, other=7)
    assert scan_identifiers.pair_count(es, _project("dot_number"), ["990006"]) == (7, 0)
