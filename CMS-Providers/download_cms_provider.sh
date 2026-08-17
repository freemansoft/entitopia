#!/usr/bin/env bash
# assumes running in CMS-Providers dir
#
# Downloads the three CMS provider-data CSVs used by this project.
#
# The direct file URLs under .../resources/<hash>_<timestamp>/<file>.csv are
# NOT stable -- CMS republishes each dataset under a new hash/timestamp path and
# the old path starts returning 404. Hardcoding them (as this script used to)
# silently rots: plain `curl` writes the 404 HTML page into the .csv file, and a
# "skip if the file exists" guard then caches that corruption forever.
#
# Instead we resolve the *current* downloadURL for each file at runtime from the
# CMS provider-data metastore, then download with `curl --fail` so an HTTP error
# aborts instead of producing a bogus CSV.

set -euo pipefail

METASTORE_URL="https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items?show-reference-ids=true"

# The "already present" guard below tests plausibility, not mere existence.
#
# `[ -s "$dest" ]` alone -- non-empty -- is the same shape as the `[ ! -f ]`
# guard this script was written to replace, and fails the same way against a
# different corruption. Measured 2026-08-16: a checkout carried a
# Hospital_General_Information.csv holding a header and five rows in place of
# the 5,432-row extract. Being non-empty, it satisfied the guard and would have
# been skipped on every future run, exactly as the 404 HTML page was cached
# forever by the old one.
#
# The smallest real file here is hospitals at ~5,400 lines, so this threshold is
# two orders of magnitude below the genuine article and cannot reject a real
# download; it exists to catch a stub or a truncated transfer, not to validate
# row counts. A file that fails it is re-downloaded rather than reported,
# because there is nothing an operator would do with the warning except delete
# the file and re-run.
MIN_PLAUSIBLE_LINES=50

# target-data-dir : source filename (the filename each config's index-config.json expects)
DATASETS=(
    "doctors-clinicians:DAC_NationalDownloadableFile.csv"
    "hospitals:Hospital_General_Information.csv"
    "facillity-affiliations:Facility_Affiliation.csv"
)

echo "Fetching CMS provider-data catalog ..."
CATALOG="$(curl -sSfL "$METASTORE_URL")"

for entry in "${DATASETS[@]}"; do
    dir="${entry%%:*}"
    filename="${entry##*:}"
    dest="data/${dir}/${filename}"

    if [ -s "$dest" ]; then
        existing_lines="$(wc -l < "$dest" | tr -d '[:space:]')"
        if [ "$existing_lines" -ge "$MIN_PLAUSIBLE_LINES" ]; then
            echo "Skipping $dest (already present, $existing_lines lines)"
            continue
        fi
        echo "Re-downloading $dest: $existing_lines lines is below the $MIN_PLAUSIBLE_LINES-line"
        echo "  plausibility floor, so the file on disk is a stub or a truncated transfer"
    fi

    # Pull the current downloadURL whose path ends in this filename out of the catalog.
    url="$(printf '%s' "$CATALOG" | FILENAME="$filename" python3 -c '
import json, os, sys
filename = os.environ["FILENAME"]
catalog = json.load(sys.stdin)
for dataset in catalog:
    for dist in dataset.get("distribution", []) or []:
        download_url = dist.get("downloadURL") or (dist.get("data", {}) or {}).get("downloadURL", "")
        if download_url.rsplit("/", 1)[-1] == filename:
            print(download_url)
            sys.exit(0)
sys.exit(1)
')" || { echo "ERROR: could not find current URL for $filename in the CMS catalog" >&2; exit 1; }

    echo "Downloading $filename"
    echo "  from $url"
    mkdir -p "data/${dir}"
    curl -sSfL "$url" --output "$dest"
    echo "  wrote $dest ($(wc -l < "$dest") lines)"
done

echo "Done."
