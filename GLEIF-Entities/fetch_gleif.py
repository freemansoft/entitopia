"""Download the GLEIF golden copy: Level 1 LEI records and Level 2 relationships.

GLEIF-Entities scores the matcher against the successors GLEIF itself records,
and tags parent and sibling pairs from the Level 2 relationships. Both files
must come from one publish, because prepare_gleif.py joins one onto the other
and a parent list from a different day would disagree with the entities
silently.

The guards mirror CMS-Providers/download_cms_provider.sh, written after a 404
HTML page was cached as a .csv and a five-row stub passed a "non-empty" check:
a fetch that reports success over garbage is the failure this repo keeps
paying for. The file URLs are resolved from the publish API at run time for the
same reason CMS resolves its own: each golden-copy file name carries its
publish timestamp, so a hard-coded URL rots within hours.

Run from the repository root:
    .venv/bin/python GLEIF-Entities/fetch_gleif.py
"""

import argparse
import json
import os
import sys
import urllib.request
import zipfile
from http import HTTPStatus
from pathlib import Path

# Verified 2026-11-26: returns {"data": {"publish_date", "lei2", "rr",
# "repex"}}, each file type holding full_file.{csv,json,xml}.url and a
# delta_files block of partial publishes that must not be mistaken for it.
GOLDEN_COPY_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"

# A generic agent: the API needs no key, and a request must never carry
# anything identifying the operator.
USER_AGENT = "entitopia-fetch"

# Large enough that a 500 MB file is a few thousand reads, small enough that a
# read never holds much of it in memory.
CHUNK_BYTES = 1 << 20

TIMEOUT_SECONDS = 120

_RAW = Path(__file__).parent / "data" / "raw"

# Plausibility floors, two orders of magnitude below the real files (488 MB and
# 23 MB on 2026-11-26), so they catch a stub or a truncated transfer without
# ever rejecting a genuine download, like the CMS line floor.
FILES = {
    "lei2": (_RAW / "lei2.zip", 10_000_000),
    "rr": (_RAW / "rr.zip", 1_000_000),
}


def _request(url):
    """Every request carries the generic agent and nothing else."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def resolve_latest(opener=urllib.request.urlopen) -> dict[str, str]:
    """Return the full-file CSV zip URL of each file type from the latest publish.

    Only `full_file.csv` is accepted. The same publish lists delta files
    (last day, last week) whose zips are valid and well above any floor, so
    picking one would load a fraction of the register and report success.
    """
    with opener(_request(GOLDEN_COPY_API), timeout=TIMEOUT_SECONDS) as response:
        if response.status != HTTPStatus.OK:
            raise RuntimeError("{} returned HTTP {}".format(GOLDEN_COPY_API, response.status))
        publish = json.loads(response.read())
    urls = {}
    for kind in FILES:
        try:
            urls[kind] = publish["data"][kind]["full_file"]["csv"]["url"]
        except (KeyError, TypeError) as error:
            raise RuntimeError(
                "{} lists no full-file CSV for {}".format(GOLDEN_COPY_API, kind)
            ) from error
    return urls


def download(url, dest: Path, min_bytes: int, opener=urllib.request.urlopen) -> int:
    """Stream url into dest and return its size in bytes.

    The body is written to dest.part and renamed only once it has passed every
    check. On any failure both files are removed, including a dest left by an
    earlier run: keeping it would let prepare_gleif.py pair this publish's other
    file with one from an older publish, and the join would report success.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    succeeded = False
    try:
        written = 0
        with opener(_request(url), timeout=TIMEOUT_SECONDS) as response:
            if response.status != HTTPStatus.OK:
                raise RuntimeError("{} returned HTTP {}".format(url, response.status))
            chunk = response.read(CHUNK_BYTES)
            # An error page served with status 200 is the CMS incident.
            if chunk.lstrip().startswith(b"<"):
                raise RuntimeError("{} returned HTML, not a zip".format(url))
            with open(part, "wb") as out:
                while chunk:
                    out.write(chunk)
                    written += len(chunk)
                    chunk = response.read(CHUNK_BYTES)
        if written < min_bytes:
            raise RuntimeError(
                "{} is {} bytes, below the {}-byte plausibility floor".format(
                    url, written, min_bytes
                )
            )
        if not zipfile.is_zipfile(part):
            raise RuntimeError("{} is not a zip".format(url))
        os.replace(part, dest)
        succeeded = True
    finally:
        if not succeeded:
            part.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
    return written


def main():
    """Entry point: resolve both URLs first, so a missing file fails before any download."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    urls = resolve_latest()
    for kind, (dest, floor) in FILES.items():
        print("Downloading {}\n  from {}".format(kind, urls[kind]), flush=True)
        size = download(urls[kind], dest, floor)
        print("  wrote {} ({:,} bytes)".format(dest, size), flush=True)


if __name__ == "__main__":
    sys.exit(main())
