#!/usr/bin/env bash
# Driver for entitopia: brings up a disposable Elasticsearch cluster with the
# required analysis plugins, builds a Python 3.11+ venv, writes tiny synthetic
# fixture data (the real CMS/DOT download URLs go stale, see SKILL.md
# Gotchas), runs the CSV -> Elasticsearch pipeline, and verifies documents
# landed. Run from the repo root: .claude/skills/run-entitopia/driver.sh <cmd>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

# Must match the container name and port in docker/compose.yml, which is the
# single definition of this project's cluster.
ES_CONTAINER=entitopia-es
COMPOSE_FILE="$ROOT/docker/compose.yml"
ES_URL=http://localhost:9200

# Bootstrap interpreter, used only to create .venv. Once .venv exists every
# command runs through it, so the host Python cannot affect the project.
PY=python3
VENV_PY="$ROOT/.venv/bin/python"

es_up() {
    # Delegate to the repo's own cluster definition rather than building a
    # second one here. This script used to build an inline image pinned to a
    # different Elasticsearch version than requirements.txt pins the client to,
    # on the same port 9200 — so the two could never run together and could
    # drift apart silently. docker/compose.yml is now the single definition.
    if docker ps --format '{{.Names}}' | grep -qx "$ES_CONTAINER"; then
        echo "Elasticsearch already running"
    else
        echo "Starting Elasticsearch via docker/compose.yml"
        docker compose -f "$COMPOSE_FILE" up -d --build
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
    docker compose -f "$COMPOSE_FILE" down
    echo "Stopped $ES_CONTAINER (volume kept; add -v to discard indexed data)"
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
    # Column names must match CMS-Providers/configuration/hospitals/index-mappings.json
    # exactly. A mapping naming a column that does not exist is accepted silently
    # and applies nothing — which is how all three CMS analyzers were inert for
    # months after CMS renamed its columns. Verify with _analyze, not by reading
    # the mapping file.
    mkdir -p CMS-Providers/data/hospitals
    cat > CMS-Providers/data/hospitals/Hospital_General_Information.csv <<'EOF'
Facility ID,Facility Name,Address,City/Town,State,ZIP Code,Telephone Number
010001,SOUTHEAST HEALTH MEDICAL CENTER,1108 ROSS CLARK CIRCLE,DOTHAN,AL,36301,(334) 793-8701
010005,MARSHALL MEDICAL CENTER SOUTH,2505 U S HIGHWAY 431 NORTH,BOAZ,AL,35957,(256) 593-8310
010006,ELIZA COFFEE MEMORIAL HOSPITAL,205 MARENGO STREET,FLORENCE,AL,35630,(256) 768-9191
010007,MIZELL MEMORIAL HOSPITAL,702 N MAIN ST,OPP,AL,36467,(334) 493-3541
010008,CRENSHAW COMMUNITY HOSPITAL,101 HOSPITAL CIRCLE,LUVERNE,AL,36049,(334) 335-3374
EOF
}

fixtures_dot() {
    # Filenames and column names come from the Socrata API and are lowercase.
    # The pre-Socrata uppercase .txt fixtures this script used to write no
    # longer match any index-config source, so every DOT step silently loaded
    # nothing.
    mkdir -p DOT-Commercial/data/crashes DOT-Commercial/data/inspections DOT-Commercial/data/carriers
    cat > DOT-Commercial/data/crashes/crashes.csv <<'EOF'
crash_id,dot_number,report_number,report_seq_no,vehicle_identification_number
C0001,1000001,RPT001,1,1FDXE4FS0AA000001
C0002,1000002,RPT002,1,1FDXE4FS0AA000002
EOF
    cat > DOT-Commercial/data/inspections/inspections.csv <<'EOF'
inspection_id,dot_number,insp_date
INS001,1000001,2023-02-01
INS002,1000002,2023-02-02
EOF
    # add_date is deliberately in the legacy Oracle format the carriers
    # pipeline converts, so a smoke run exercises the century pivot rather
    # than only the happy ISO path.
    cat > DOT-Commercial/data/carriers/carriers.csv <<'EOF'
dot_number,legal_name,dba_name,phy_street,phy_city,phy_state,phy_zip,mailing_street,mailing_city,mailing_state,telephone,fax,email_address,add_date
1000001,ACME TRUCKING LLC,ACME,123 MAIN ST,SPRINGFIELD,IL,62701,123 MAIN ST,SPRINGFIELD,IL,(217) 555-1234,,dispatch@acme.example,01-JUN-74
1000002,BOLT FREIGHT INC,BOLT,456 OAK AVE,DECATUR,IL,62521,456 OAK AVE,DECATUR,IL,(217) 555-5678,,ops@boltfreight.example,23-JAN-02
EOF
}

run_project() {
    # shellcheck disable=SC1091
    source .venv/bin/activate
    "$VENV_PY" execute_project.py "$@"
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
    }' | "$VENV_PY" -c "
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
    }' | "$VENV_PY" -c "
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
