"""Download FDIC institution and merger-history records as one CSV each.

FDIC-Banks needs closed banks and the CERT of the bank that absorbed them, so
the matcher can be scored against declared successions rather than guesses.
The BankFind API serves CSV directly, which makes this a paging loop rather
than a scraper.

The guards mirror CMS-Providers/download_cms_provider.sh, which was written
after a 404 HTML page was cached as a .csv and a five-row stub passed a
"non-empty" check: a fetch that reports success over garbage is the failure
this repo keeps paying for.

Run from the FDIC-Banks directory:
    ../.venv/bin/python fetch_fdic.py --dataset institutions
    ../.venv/bin/python fetch_fdic.py --dataset history
"""

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

BASE_URL = "https://api.fdic.gov/banks"

# A generic agent, because the API wants none in particular and a request must
# never carry anything identifying the operator.
USER_AGENT = "entitopia-fetch"

# Verified 2026-11-26: the API returned a header plus 10,000 rows for
# limit=10000, so the whole institutions table is three pages plus the empty
# one that ends the fetch.
PAGE_SIZE = 10000

# Same plausibility floor as the CMS script. The smallest real file here has
# thousands of lines, so this cannot reject a genuine download; it exists to
# catch a stub or a truncated transfer.
MIN_PLAUSIBLE_LINES = 50

DATASETS = {
    "institutions": {
        "endpoint": "institutions",
        "dest": "data/institutions/institutions.csv",
        "fields": [
            "CERT",
            "NAME",
            "PRIORNAME1",
            "ADDRESS",
            "ADDRESS2",
            "CITY",
            "STALP",
            "ZIP",
            "WEBADDR",
            "ESTYMD",
            "ENDEFYMD",
            "ACTIVE",
            "INACTIVE",
            "NEWCERT",
            "ULTCERT",
            "CHANGEC1",
            "FED_RSSD",
            # Holding company. Profiled 2026-11-26: RSSDHCR is populated on
            # 13,550 of 23,634 inactive and 3,535 of 4,204 active records, so a
            # shared parent is available as a declared-link candidate.
            "RSSDHCR",
            "NAMEHCR",
        ],
    },
    # Read only by the NEWCERT coverage check; never loaded into the cluster.
    "history": {
        "endpoint": "history",
        # Institution-level events only. The CSV header is taken from the first
        # record the API returns, and branch events (the 7xx codes, over half the
        # table) lack the CERT columns: unfiltered, the file came back with
        # CHANGECODE, CHANGECODE_DESC, EFFDATE and ID and no CERT column at all.
        # Measured 2026-11-26; the header check in fetch() now refuses that.
        "filters": "CHANGECODE:[200 TO 299]",
        "dest": "data/history/history.csv",
        "fields": [
            "OUT_CERT",
            "SUR_CERT",
            "ACQ_CERT",
            "CHANGECODE",
            "CHANGECODE_DESC",
            "EFFDATE",
        ],
    },
}


def urlopen_text(url, user_agent):
    """Production opener, injected so tests never touch the network.

    Returns (status, body) rather than raising on HTTP errors so fetch() owns
    the one place that decides what counts as a failure.
    """
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def fetch(
    endpoint,
    fields,
    dest,
    page_size=PAGE_SIZE,
    opener=urlopen_text,
    min_lines=MIN_PLAUSIBLE_LINES,
    filters=None,
):
    """Page the endpoint into dest and return the number of data rows.

    The file is written beside dest and moved into place only after it passes
    the plausibility floor, so a failed or stubby run leaves whatever good file
    was already there untouched instead of replacing it with the stub.

    Paging ends only on a page with no data rows, which costs one extra
    request. Ending on the first short page assumed the server honours
    page_size; one that caps pages lower would end the fetch after page one,
    and that fraction of the data can still clear the plausibility floor. For
    the same reason the next offset follows the rows actually served, not
    page_size, or the rows between the cap and page_size would be skipped.
    Measured 2026-11-26: past the last row the API returns an empty body.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    total = 0
    offset = 0
    header_written = False
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as out:
            while True:
                url = "{}/{}?format=csv&limit={}&offset={}&fields={}".format(
                    BASE_URL, endpoint, page_size, offset, ",".join(fields)
                )
                if filters:
                    url += "&filters=" + urllib.parse.quote(filters)
                status, body = opener(url, USER_AGENT)
                if status != HTTPStatus.OK:
                    raise RuntimeError("{} returned HTTP {}".format(url, status))
                # An error page served with status 200 is the CMS incident.
                if body.lstrip().startswith("<"):
                    raise RuntimeError("{} returned HTML, not CSV".format(url))
                lines = body.splitlines(keepends=True)
                data = lines[1:]
                if not data:
                    break
                # The API ends a page without a newline, so appending the next
                # page fused two records into one row. Measured 2026-11-26: it
                # swallowed one institution at each of two page boundaries and
                # still reported success.
                if data and not data[-1].endswith("\n"):
                    data[-1] += "\n"
                if not header_written:
                    # A requested column missing from the header means the API
                    # dropped it silently, and every later join on it finds
                    # nothing while the load reports success.
                    header = {name.strip('"') for name in lines[0].strip().split(",")}
                    absent = [name for name in fields if name not in header]
                    if absent:
                        raise RuntimeError(
                            "{} omitted requested columns {}".format(url, absent)
                        )
                    out.write(lines[0])
                    header_written = True
                out.writelines(data)
                total += len(data)
                offset += len(data)
        with open(tmp, encoding="utf-8") as check:
            line_count = sum(1 for _ in check)
        if line_count < min_lines:
            raise RuntimeError(
                "{} has {} lines, below the {}-line plausibility floor".format(
                    dest, line_count, min_lines
                )
            )
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return total


def main():
    """Entry point; the dataset choice keeps history out of the default load."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    args = parser.parse_args()
    spec = DATASETS[args.dataset]
    count = fetch(
        spec["endpoint"],
        spec["fields"],
        Path(spec["dest"]),
        filters=spec.get("filters"),
    )
    print("Wrote {} rows to {}".format(count, spec["dest"]))


if __name__ == "__main__":
    sys.exit(main())
