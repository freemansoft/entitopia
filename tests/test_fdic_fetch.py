"""Tests for FDIC-Banks/fetch_fdic.py.

CMS-Providers once cached a 404 HTML page as a .csv forever, and a later
checkout carried a header-plus-five-rows stub that a mere "file is non-empty"
guard accepted. A fetch that writes either one reports success while every
downstream phase loads garbage. Each test pins one of those failures, using a
fake opener so no network is involved:

    page boundaries     a page body with no trailing newline fused two records
                        into one row, silently losing a bank at each boundary
    short-page paging   a miscounted last page silently drops or repeats rows
    HTML body           an error page served with status 200 must raise
    dropped columns     the API omitted CERT columns from an unfiltered history
                        CSV, which a join would read as zero successions
    stub result         an implausibly small file must not be left in place

Loaded by path because FDIC-Banks is a project directory, not a package.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "FDIC-Banks" / "fetch_fdic.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_fdic", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_fdic = _load()

HEADER = "CERT,NAME\n"


class FakeOpener:
    """Serves queued bodies in order and records each requested URL."""

    def __init__(self, bodies, status=200):
        self.bodies = list(bodies)
        self.status = status
        self.urls = []

    def __call__(self, url, user_agent):
        self.urls.append((url, user_agent))
        return self.status, self.bodies.pop(0)


def _rows(start, count):
    return "".join("{},BANK {}\n".format(i, i) for i in range(start, start + count))


def test_pages_until_short_page(tmp_path):
    opener = FakeOpener(
        [HEADER + _rows(1, 3), HEADER + _rows(4, 3), HEADER + _rows(7, 1)]
    )
    dest = tmp_path / "out.csv"
    count = fetch_fdic.fetch(
        "institutions", ["CERT", "NAME"], dest, page_size=3, opener=opener, min_lines=1
    )
    assert count == 7
    lines = dest.read_text().splitlines()
    assert lines.count("CERT,NAME") == 1
    assert len(lines) == 8
    assert "offset=3" in opener.urls[1][0]
    assert opener.urls[0][1] == "entitopia-fetch"


def test_html_body_raises(tmp_path):
    opener = FakeOpener(["<!DOCTYPE html><html>not found</html>"])
    with pytest.raises(RuntimeError):
        fetch_fdic.fetch(
            "institutions", ["CERT"], tmp_path / "out.csv", opener=opener, min_lines=1
        )


def test_non_200_raises(tmp_path):
    opener = FakeOpener([HEADER], status=503)
    with pytest.raises(RuntimeError):
        fetch_fdic.fetch(
            "institutions", ["CERT"], tmp_path / "out.csv", opener=opener, min_lines=1
        )


def test_stub_not_kept(tmp_path):
    dest = tmp_path / "out.csv"
    opener = FakeOpener([HEADER + _rows(1, 9)])
    with pytest.raises(RuntimeError):
        fetch_fdic.fetch("institutions", ["CERT", "NAME"], dest, opener=opener)
    assert not dest.exists()


def test_stub_does_not_replace_good_file(tmp_path):
    dest = tmp_path / "out.csv"
    dest.write_text("previous good content\n")
    opener = FakeOpener([HEADER + _rows(1, 2)])
    with pytest.raises(RuntimeError):
        fetch_fdic.fetch("institutions", ["CERT", "NAME"], dest, opener=opener)
    assert dest.read_text() == "previous good content\n"


def test_page_without_trailing_newline_does_not_fuse_rows(tmp_path):
    opener = FakeOpener(
        [(HEADER + _rows(1, 3)).rstrip("\n"), (HEADER + _rows(4, 1)).rstrip("\n")]
    )
    dest = tmp_path / "out.csv"
    count = fetch_fdic.fetch(
        "institutions", ["CERT", "NAME"], dest, page_size=3, opener=opener, min_lines=1
    )
    assert count == 4
    assert len(dest.read_text().splitlines()) == 5


def test_missing_requested_column_raises(tmp_path):
    opener = FakeOpener([HEADER + _rows(1, 2)])
    with pytest.raises(RuntimeError, match="OUT_CERT"):
        fetch_fdic.fetch(
            "history",
            ["CERT", "OUT_CERT"],
            tmp_path / "out.csv",
            opener=opener,
            min_lines=1,
        )


def test_filters_are_url_encoded_into_the_request(tmp_path):
    opener = FakeOpener([HEADER + _rows(1, 2)])
    fetch_fdic.fetch(
        "history",
        ["CERT"],
        tmp_path / "out.csv",
        opener=opener,
        min_lines=1,
        filters="CHANGECODE:[200 TO 299]",
    )
    assert "filters=CHANGECODE%3A%5B200%20TO%20299%5D" in opener.urls[0][0]


def test_pipeline_drops_end_date_sentinel_before_converting():
    """The 12/31/9999 sentinel must be removed before the date processor.

    Loaded as a date it makes active banks "end in 9999" and places 22 inactive
    banks in nonsense windows, with no error anywhere.
    """
    path = (
        _SCRIPT.parent
        / "configuration"
        / "institutions-ingestion-setup"
        / "pipelines.json"
    )
    processors = json.loads(path.read_text())["processors"]
    kinds = [next(iter(p)) for p in processors]
    removal = next(
        p["remove"]
        for p in processors
        if "remove" in p and "12/31/9999" in p["remove"].get("if", "")
    )
    assert removal["field"] == "ENDEFYMD"
    end_date = next(
        i for i, p in enumerate(processors) if p.get("date", {}).get("field") == "ENDEFYMD"
    )
    assert kinds.index("remove") < end_date
