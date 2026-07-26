#!/usr/bin/env bash
# Driver for entitopia: brings up a disposable Elasticsearch cluster with the
# required analysis plugins, builds a Python 3.11+ venv, writes tiny synthetic
# fixture data (the real CMS/DOT download URLs go stale, see SKILL.md
# Gotchas), runs the CSV -> Elasticsearch pipeline, and verifies documents
# landed. Run from the repo root: .claude/skills/run-entitopia/driver.sh <cmd>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

ES_CONTAINER=entitopia-es-dev
ES_IMAGE=entitopia-es-dev:8.6.2
ES_URL=http://localhost:9200
PY=python3.12
[ -x "$(command -v python3.12)" ] || PY=python3

es_up() {
    if docker ps --format '{{.Names}}' | grep -qx "$ES_CONTAINER"; then
        echo "Elasticsearch already running"
    else
        docker rm -f "$ES_CONTAINER" >/dev/null 2>&1 || true
        if ! docker image inspect "$ES_IMAGE" >/dev/null 2>&1; then
            echo "Building $ES_IMAGE (base ES image + analysis-icu + analysis-phonetic plugins)"
            tmpdir=$(mktemp -d)
            cat > "$tmpdir/Dockerfile" <<'EOF'
FROM docker.elastic.co/elasticsearch/elasticsearch:8.6.2
RUN bin/elasticsearch-plugin install --batch analysis-icu analysis-phonetic
EOF
            docker build -t "$ES_IMAGE" "$tmpdir"
            rm -rf "$tmpdir"
        fi
        echo "Starting $ES_CONTAINER"
        docker run -d --name "$ES_CONTAINER" \
            -p 9200:9200 \
            -e discovery.type=single-node \
            -e xpack.security.enabled=false \
            -e ES_JAVA_OPTS="-Xms512m -Xmx512m" \
            "$ES_IMAGE" >/dev/null
    fi

    echo "Waiting for Elasticsearch health..."
    for _ in $(seq 1 60); do
        if curl -s -o /dev/null -w '%{http_code}' "$ES_URL/_cluster/health" 2>/dev/null | grep -q 200; then
            curl -s "$ES_URL/_cluster/health"
            echo
            return 0
        fi
        sleep 2
    done
    echo "Elasticsearch did not become healthy in time" >&2
    exit 1
}

es_down() {
    docker rm -f "$ES_CONTAINER" >/dev/null 2>&1 || true
    echo "Removed $ES_CONTAINER"
}

venv_setup() {
    if [ ! -x .venv/bin/python3 ]; then
        "$PY" -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    bash dependencies.sh
}

es_config_write() {
    cat > es_config.json <<'EOF'
{
    "timeout": 180,
    "host": "localhost",
    "port": 9200,
    "scheme": "http",
    "username": "",
    "password": ""
}
EOF
}

fixtures_cms() {
    mkdir -p CMS-Providers/data/hospitals
    cat > CMS-Providers/data/hospitals/Hospital_General_Information.csv <<'EOF'
Facility ID,Facility Name,Addresss,City,State,Phone Number
010001,SOUTHEAST HEALTH MEDICAL CENTER,1108 ROSS CLARK CIRCLE,DOTHAN,AL,(334) 793-8701
010005,MARSHALL MEDICAL CENTER SOUTH,2505 U S HIGHWAY 431 NORTH,BOAZ,AL,(256) 593-8310
010006,ELIZA COFFEE MEMORIAL HOSPITAL,205 MARENGO STREET,FLORENCE,AL,(256) 768-9191
010007,MIZELL MEMORIAL HOSPITAL,702 N MAIN ST,OPP,AL,(334) 493-3541
010008,CRENSHAW COMMUNITY HOSPITAL,101 HOSPITAL CIRCLE,LUVERNE,AL,(334) 335-3374
EOF
}

fixtures_dot() {
    mkdir -p DOT-Commercial/data/crashes DOT-Commercial/data/inspections DOT-Commercial/data/carriers
    cat > DOT-Commercial/data/crashes/2023Feb_Crash.txt <<'EOF'
REPORT_NUMBER,REPORT_SEQ_NO,DOT_NUMBER,VEHICLE_ID_NUMBER
RPT001,1,1000001,1FDXE4FS0AA000001
RPT002,1,1000002,1FDXE4FS0AA000002
EOF
    cat > DOT-Commercial/data/inspections/2023Feb_Inspection.txt <<'EOF'
UNIQUE_ID,DOT_NUMBER,VIN,VIN2
INS001,1000001,1FDXE4FS0AA000001,1FDXE4FS0AA000001
INS002,1000002,1FDXE4FS0AA000002,1FDXE4FS0AA000002
EOF
    cat > DOT-Commercial/data/carriers/FMCSA_CENSUS1_2023Feb.txt <<'EOF'
DOT_NUMBER,LEGAL_NAME,DBA_NAME,PHY_STREET,PHY_CITY,PHY_STATE,MAILING_STREET,MAILING_CITY,MAILING_STATE,TELEPHONE,EMAIL_ADDRESS
1000001,ACME TRUCKING LLC,ACME,123 MAIN ST,SPRINGFIELD,IL,123 MAIN ST,SPRINGFIELD,IL,2175551234,dispatch@acme.example
1000002,BOLT FREIGHT INC,BOLT,456 OAK AVE,DECATUR,IL,456 OAK AVE,DECATUR,IL,2175555678,ops@boltfreight.example
EOF
}

run_project() {
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python3 execute_project.py "$@"
}

verify_cms() {
    curl -s -X POST "$ES_URL/hospitals-000001/_refresh" >/dev/null
    echo "--- hospitals-000001 count ---"
    curl -s "$ES_URL/hospitals-000001/_count"; echo
}

verify_dot() {
    curl -s -X POST "$ES_URL/carriers-000001/_refresh" >/dev/null
    echo "--- carriers-000001 sample doc ---"
    curl -s "$ES_URL/carriers-000001/_search?size=1&pretty"
}

match_demo() {
    # Demonstrates entitopia's actual purpose: finding that two records are
    # probably the same real-world entity even when no field matches
    # exactly, using the name_clean/name_phonetic analyzers already defined
    # in CMS-Providers/configuration/hospitals/index-settings.json. Requires
    # hospitals-000001 to be populated (run `fixtures` + `run --project=CMS-Providers
    # --step=hospitals` first, or just run `smoke`).
    local near_dup_id="__match-demo-dup__"

    echo "Indexing a synthetic near-duplicate of Facility ID 010001 (typo'd name/address, same phone/city) as $near_dup_id"
    curl -s -X PUT "$ES_URL/hospitals-000001/_doc/$near_dup_id" -H 'Content-Type: application/json' -d '{
        "Facility ID": "'"$near_dup_id"'",
        "Facility Name": "Sowth East Helth Med Ctr",
        "Addresss": "1108 Ross Clark Cir",
        "City": "DOTHAN",
        "State": "AL",
        "Phone Number": "(334) 793-8701"
    }' >/dev/null
    curl -s -X POST "$ES_URL/hospitals-000001/_refresh" >/dev/null

    echo
    echo "--- Absolute match: term query on Facility Name.keyword for the canonical name ---"
    echo "(the typo'd near-duplicate is invisible to exact matching, by construction)"
    curl -s "$ES_URL/hospitals-000001/_search" -H 'Content-Type: application/json' -d '{
        "query": {"term": {"Facility Name.keyword": "SOUTHEAST HEALTH MEDICAL CENTER"}}
    }' | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('hits:', d['hits']['total']['value'])
for h in d['hits']['hits']:
    print(' ', h['_id'], '->', h['_source']['Facility Name'])
"

    echo
    echo "--- Soft match: blocked on City.keyword=DOTHAN (exact), ranked by name_phonetic + fuzzy name_clean closeness ---"
    echo "(surfaces the near-duplicate with a lower score instead of missing it entirely)"
    curl -s "$ES_URL/hospitals-000001/_search" -H 'Content-Type: application/json' -d '{
        "query": {
            "bool": {
                "filter": [{"term": {"City.keyword": "DOTHAN"}}],
                "should": [
                    {"match": {"Facility Name.phonetic": "SOUTHEAST HEALTH MEDICAL CENTER"}},
                    {"match": {"Facility Name.clean": {"query": "SOUTHEAST HEALTH MEDICAL CENTER", "fuzziness": "AUTO"}}}
                ]
            }
        }
    }' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for h in d['hits']['hits']:
    print(' ', round(h['_score'],3), h['_id'], '->', h['_source']['Facility Name'])
"

    echo
    echo "Removing $near_dup_id"
    curl -s -X DELETE "$ES_URL/hospitals-000001/_doc/$near_dup_id" >/dev/null
    curl -s -X POST "$ES_URL/hospitals-000001/_refresh" >/dev/null
}

smoke() {
    es_up
    venv_setup
    es_config_write
    fixtures_cms
    fixtures_dot

    run_project --project=CMS-Providers --step=hospitals

    # DOT-Commercial's carriers-ingestion-setup step runs enrichment
    # policies against crashes/inspections. Enrich policy execution
    # only sees documents that are already searchable, and ES's default
    # 1s refresh interval means docs indexed moments earlier in the same
    # process are invisible unless force-refreshed first. Running all
    # steps via a single `--project=DOT-Commercial` call (no refresh
    # between steps) reproduces this as a silent, timing-dependent
    # failure: everything logs success but the carriers docs end up
    # with no `crashes`/`inspections` fields. Split the run and refresh
    # in between so enrichment reliably has data to match against.
    run_project --project=DOT-Commercial --step=crashes-ingestion-setup
    run_project --project=DOT-Commercial --step=crashes
    run_project --project=DOT-Commercial --step=inspections
    curl -s -X POST "$ES_URL/crashes-000001/_refresh" >/dev/null
    curl -s -X POST "$ES_URL/inspections-000001/_refresh" >/dev/null
    run_project --project=DOT-Commercial --step=carriers-ingestion-setup
    run_project --project=DOT-Commercial --step=carriers

    verify_cms
    verify_dot
    match_demo
}

cmd="${1:-smoke}"
shift || true
case "$cmd" in
    es-up) es_up ;;
    es-down) es_down ;;
    venv-setup) venv_setup ;;
    es-config) es_config_write ;;
    fixtures) fixtures_cms; fixtures_dot ;;
    run) run_project "$@" ;;
    verify) verify_cms; verify_dot ;;
    match-demo) match_demo ;;
    smoke) smoke ;;
    *)
        echo "Usage: driver.sh {es-up|es-down|venv-setup|es-config|fixtures|run <execute_project.py args>|verify|match-demo|smoke}" >&2
        exit 1
        ;;
esac
