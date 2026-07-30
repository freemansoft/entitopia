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
        echo "Skipping $dest (already present)"
        continue
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
