"""The anonymization scanner must see GLEIF LEIs, not pass over them.

Exists because GLEIF's key is a 20-character LEI that no existing pattern
extracts (a VIN is 17, a DOT number at most 8, a CERT at most 6). A scan of
text quoting one would report "clean" over a value it never looked up, the
quiet success the scanner exists to prevent. The checksum is what lets a bare
20-character run be trusted as an LEI: without it every uppercase hash would be
looked up and every lookup would read NOT_FOUND, burying real hits in noise.

The naming rule is pinned too: a parent or sibling pair is an `explains` pair,
so an LEI in one must read FLAGGED, not DECLARED.

Every value in this file is invented.
"""

import importlib.util
from pathlib import Path

_SCANNER = Path(__file__).resolve().parent.parent / ".claude/skills/anonymization-check/scripts/scan_identifiers.py"
_spec = importlib.util.spec_from_file_location("scan_identifiers", _SCANNER)
scan_identifiers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_identifiers)


def _lei(prefix18: str) -> str:
    """A valid LEI built independently of the scanner, so a wrong checker cannot agree with itself."""
    converted = "".join(str(int(c, 36)) for c in prefix18)
    return prefix18 + "{:02d}".format(98 - int(converted + "00") % 97)


def _values(text: str, kind: str) -> set[str]:
    return {c.value for c in scan_identifiers.extract(text) if c.kind == kind}


def _project(entity_key: str) -> scan_identifiers.Project:
    return scan_identifiers.Project(
        name="P/step",
        source_alias="src-000001",
        entity_key=entity_key,
        pair_pattern="p-pairs-*",
        fields_by_role={},
        ignore=set(),
    )


class _CountingEs:
    """Answers `count` by whether the query selects truth pairs or excludes them."""

    def __init__(self, truth: int, other: int):
        self.truth, self.other = truth, other

    def count(self, **kwargs):
        query = kwargs["query"]["bool"]
        if any(clause == {"term": {"declared": "truth"}} for clause in query.get("filter", [])):
            return {"count": self.truth}
        if any(clause == {"term": {"declared": "truth"}} for clause in query.get("must_not", [])):
            return {"count": self.other}
        raise AssertionError("pair count query neither selects nor excludes truth pairs")


class _NoSearchEs:
    def search(self, **kwargs):
        raise AssertionError("searched a project whose entity key is not an LEI")


class _RecordingEs:
    def __init__(self):
        self.query = None

    def search(self, **kwargs):
        self.query = kwargs["query"]
        return {"hits": {"total": {"value": 1, "relation": "eq"}, "hits": [{"_source": {"LEI": "x"}}]}}


def test_lei_is_extracted_bare():
    value = _lei("ZZZZ00INVENTED0001")
    assert _values("successor " + value, "lei") == {value}


def test_lei_with_bad_check_digits_is_not_extracted():
    value = _lei("ZZZZ00INVENTED0002")
    bad = value[:-1] + str((int(value[-1]) + 1) % 10)
    assert _values("successor " + bad, "lei") == set()


def test_lei_placeholder_is_not_extracted():
    assert _values("absorbed by <LEI-A>", "lei") == set()


def test_hash_is_not_an_lei():
    digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"
    assert _values(digest, "lei") == set()
    assert _values(digest.upper(), "lei") == set()


def test_lei_is_not_also_a_vin_or_number():
    value = _lei("123456789012345678")
    kinds = {c.kind for c in scan_identifiers.extract("see " + value)}
    assert kinds == {"lei"}


def test_lei_looked_up_only_where_the_entity_key_is_an_lei():
    candidate = scan_identifiers.Candidate("lei", _lei("ZZZZ00INVENTED0003"))
    assert scan_identifiers.source_lookup(_NoSearchEs(), _project("CERT"), {}, candidate) == ("", [])
    es = _RecordingEs()
    scan_identifiers.source_lookup(es, _project("LEI"), {}, candidate)
    assert es.query == {"bool": {"should": [{"term": {"LEI": candidate.value}}], "minimum_should_match": 1}}


def test_gleif_names_are_extracted():
    text = "QUUXWORTH VANTAGE HOLDINGS L.P. and Quuxworth Vantage Partners LP merged."
    assert {"QUUXWORTH VANTAGE HOLDINGS L.P", "Quuxworth Vantage Partners LP"} <= _values(text, "name")


def test_explains_pair_is_flagged():
    # Pinning test: the scanner already counts every non-truth pair as flagged,
    # which is what makes a parent or sibling (`explains`) pair anonymized.
    project = _project("LEI")
    flagged, truth = scan_identifiers.pair_count(_CountingEs(truth=0, other=2), project, ["x"])
    finding = scan_identifiers.Finding(
        scan_identifiers.Candidate("lei", _lei("ZZZZ00INVENTED0004")),
        source_hits={"P/step": "1"},
        flagged_pairs={"P/step": flagged},
        truth_pairs={"P/step": truth},
    )
    assert scan_identifiers.verdict_for(finding) == "FLAGGED"
