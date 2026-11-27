"""Tests for GLEIF-Entities/fetch_gleif.py.

CMS-Providers once saved a 404 HTML page as a .csv and cached it forever, and a
later checkout carried a header-plus-five-rows stub that a "file is non-empty"
guard accepted as the real extract. Either one reports a successful fetch while
every later phase loads garbage. A golden-copy zip can fail the same ways, plus
one more: a transfer cut off mid-stream leaves a file that looks like a zip
until it is opened. Each test pins one of those, through a fake opener so no
network is involved:

    publish URLs       the full-file CSV URLs are read from the publish JSON,
                       because their names carry the publish timestamp
    HTML body          an error page served with status 200 must raise
    failed download    a transfer that dies part-way leaves no file at all, so
                       the next run cannot mistake it, or a file from an older
                       publish, for this publish's data
    non-zip body       bytes that are not a zip are refused
    stub body          a zip below the plausibility floor is refused

Loaded by path because GLEIF-Entities is a project directory, not a package.
"""

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "GLEIF-Entities" / "fetch_gleif.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_gleif", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_gleif = _load()

# Trimmed from the real response of 2026-11-26: the same nesting, the delta
# files and the JSON/XML variants a careless lookup could pick instead, and
# invented URLs.
PUBLISH = {
    "data": {
        "publish_date": "2026-01-02 08:00:00",
        "lei2": {
            "type": "lei2",
            "full_file": {
                "csv": {"format": "csv", "url": "https://example.test/lei2-golden-copy.csv.zip"},
                "json": {"format": "json", "url": "https://example.test/lei2-golden-copy.json.zip"},
            },
            "delta_files": {
                "LastDay": {"csv": {"url": "https://example.test/lei2-last-day.csv.zip"}}
            },
        },
        "rr": {
            "type": "rr",
            "full_file": {
                "csv": {"format": "csv", "url": "https://example.test/rr-golden-copy.csv.zip"},
                "xml": {"format": "xml", "url": "https://example.test/rr-golden-copy.xml.zip"},
            },
            "delta_files": {
                "LastDay": {"csv": {"url": "https://example.test/rr-last-day.csv.zip"}}
            },
        },
        "repex": {
            "type": "repex",
            "full_file": {"csv": {"url": "https://example.test/repex-golden-copy.csv.zip"}},
        },
    }
}


class FakeResponse:
    """Serves a body in fixed-size reads, optionally failing after the first."""

    def __init__(self, body, status=200, fail_after_first=False):
        self.stream = io.BytesIO(body)
        self.status = status
        self.fail_after_first = fail_after_first
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.fail_after_first and self.reads > 1:
            raise ConnectionResetError("connection reset mid-transfer")
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Returns one prepared response and records each request it was handed."""

    def __init__(self, response):
        self.response = response
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        return self.response


def _zip_bytes(size):
    """A genuine zip of at least ``size`` bytes, stored uncompressed."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("data.csv", b"x" * size)
    return buffer.getvalue()


def test_resolves_full_file_urls():
    opener = FakeOpener(FakeResponse(json.dumps(PUBLISH).encode()))
    assert fetch_gleif.resolve_latest(opener=opener) == {
        "lei2": "https://example.test/lei2-golden-copy.csv.zip",
        "rr": "https://example.test/rr-golden-copy.csv.zip",
    }
    request = opener.requests[0]
    assert request.full_url == fetch_gleif.GOLDEN_COPY_API
    assert request.get_header("User-agent") == fetch_gleif.USER_AGENT


def test_publish_missing_a_file_raises():
    trimmed = json.loads(json.dumps(PUBLISH))
    del trimmed["data"]["rr"]["full_file"]["csv"]
    with pytest.raises(RuntimeError, match="rr"):
        fetch_gleif.resolve_latest(opener=FakeOpener(FakeResponse(json.dumps(trimmed).encode())))


def test_html_body_raises(tmp_path):
    dest = tmp_path / "lei2.zip"
    body = b"<!DOCTYPE html><html><body>Not Found</body></html>" + b" " * 4096
    with pytest.raises(RuntimeError, match="HTML"):
        fetch_gleif.download(
            "https://example.test/x.zip", dest, 10, opener=FakeOpener(FakeResponse(body))
        )
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_failed_download_leaves_nothing(tmp_path):
    dest = tmp_path / "lei2.zip"
    # A file from an earlier publish: keeping it would pair it with the other
    # file of the new publish.
    dest.write_bytes(_zip_bytes(10))
    body = _zip_bytes(fetch_gleif.CHUNK_BYTES * 3)
    response = FakeResponse(body, fail_after_first=True)
    with pytest.raises(ConnectionResetError):
        fetch_gleif.download("https://example.test/x.zip", dest, 10, opener=FakeOpener(response))
    assert response.reads == 2
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_non_zip_refused(tmp_path):
    dest = tmp_path / "rr.zip"
    body = b"LEI,Name\n" + b"A,B\n" * 1000
    with pytest.raises(RuntimeError, match="not a zip"):
        fetch_gleif.download(
            "https://example.test/x.zip", dest, 10, opener=FakeOpener(FakeResponse(body))
        )
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_below_floor_refused(tmp_path):
    dest = tmp_path / "rr.zip"
    body = _zip_bytes(100)
    with pytest.raises(RuntimeError, match="floor"):
        fetch_gleif.download(
            "https://example.test/x.zip", dest, 10_000, opener=FakeOpener(FakeResponse(body))
        )
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_good_download_lands_at_dest(tmp_path):
    dest = tmp_path / "raw" / "rr.zip"
    body = _zip_bytes(fetch_gleif.CHUNK_BYTES * 2)
    opener = FakeOpener(FakeResponse(body))
    assert fetch_gleif.download("https://example.test/x.zip", dest, 1000, opener=opener) == len(
        body
    )
    assert dest.read_bytes() == body
    assert not dest.with_name(dest.name + ".part").exists()
    assert opener.requests[0].get_header("User-agent") == fetch_gleif.USER_AGENT


def test_non_200_status_raises(tmp_path):
    dest = tmp_path / "rr.zip"
    response = FakeResponse(_zip_bytes(100), status=503)
    with pytest.raises(RuntimeError, match="503"):
        fetch_gleif.download("https://example.test/x.zip", dest, 10, opener=FakeOpener(response))
    assert not dest.exists()
