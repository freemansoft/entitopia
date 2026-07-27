#!/usr/bin/env bash
# assumes running in DOT-Commercial dir

mkdir -p configuration
mkdir -p data

source configuration/download.env

while [[ $# -gt 0 ]]; do
    case "$1" in
        --year=*)
            YEAR="${1#*=}"
            ;;
        --month=*)
            MONTH="${1#*=}"
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
done

resolve() {
    local template="$1"
    template="${template//\{year\}/$YEAR}"
    template="${template//\{month\}/$MONTH}"
    echo "$template"
}

MISSING=()

download_dataset() {
    local name="$1"
    local host="$2"
    local path_template="$3"
    local zip_template="$4"
    local data_subdir="$5"

    local url="${host}$(resolve "$path_template")"
    local zip_name
    zip_name="$(resolve "$zip_template")"
    local file="data/${data_subdir}/${zip_name}"

    if [ -f "$file" ]; then
        echo "Already downloaded: $file"
        return
    fi

    mkdir -p "data/${data_subdir}"
    echo "Downloading $name from $url"
    if curl --fail "$url" --output "$file" && unzip -t "$file" > /dev/null 2>&1; then
        unzip -o "$file" -d "data/${data_subdir}"
    else
        echo "No data found for $name (year=$YEAR, month=$MONTH)" >&2
        rm -f "$file"
        MISSING+=("$name")
    fi
}

download_dataset "census" "$CENSUS_HOST" "$CENSUS_REMOTE_PATH_TEMPLATE" "$CENSUS_LOCAL_ZIP_TEMPLATE" "carriers"
download_dataset "inspection" "$INSPECTION_HOST" "$INSPECTION_REMOTE_PATH_TEMPLATE" "$INSPECTION_LOCAL_ZIP_TEMPLATE" "inspections"
download_dataset "crash" "$CRASH_HOST" "$CRASH_REMOTE_PATH_TEMPLATE" "$CRASH_LOCAL_ZIP_TEMPLATE" "crashes"

if [ ${#MISSING[@]} -gt 0 ]; then
    missing_list=$(printf '%s, ' "${MISSING[@]}")
    missing_list="${missing_list%, }"
    echo "No data found for: ${missing_list} (year=$YEAR, month=$MONTH)" >&2
    exit 1
fi
