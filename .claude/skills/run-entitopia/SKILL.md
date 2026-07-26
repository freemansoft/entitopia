---
name: run-entitopia
description: Build, run, and drive entitopia — a Python CLI that loads CSV data into Elasticsearch and demonstrates soft/probabilistic entity matching (finding likely-duplicate real-world entities via phonetic + fuzzy closeness scoring, not just exact-field matches). Use when asked to run entitopia, execute a project (CMS-Providers, DOT-Commercial), stand up its Elasticsearch dependency, test the CSV-to-ES pipeline, verify indexed documents, or demonstrate/evaluate entity resolution and fuzzy matching.
---

entitopia's actual purpose (per README.md) is exploring **soft/probabilistic
relationships between entities** — deciding two records probably describe
the same real-world thing even when no field matches exactly, by blocking
on an exact key (city, state) and ranking candidates by phonetic/fuzzy
closeness on the rest. It's a CLI orchestrator (`execute_project.py`), not
a server or GUI — it connects to a live Elasticsearch cluster and drives
it through config-defined steps/phases. There is no UI to screenshot; the
"driving" is running the CLI against a real cluster and querying the
resulting indices with `curl`. Do this via the driver:
`.claude/skills/run-entitopia/driver.sh smoke` — it brings up disposable
Elasticsearch, builds the venv, writes tiny synthetic fixtures, runs both
example projects end-to-end, verifies documents landed, **and runs a
soft-match demo** (`match-demo`) that proves the entity-resolution
capability actually works, not just that CSVs got indexed.

All paths below are relative to the repo root.

## Prerequisites

- Docker (used to run Elasticsearch 8.6.2 with the `analysis-icu` and
  `analysis-phonetic` plugins — the index-settings.json analyzers in
  both example projects require them; without the plugins, index
  creation/mapping still "succeeds" but the custom analyzers silently
  fail with a 400 that's logged and swallowed).
- Python 3.11+ (enforced at the top of `execute_project.py` and by
  `dependencies.sh`; a `.python-version` pins `3.12`). This repo was
  tested with Homebrew's `python3.12`.

```bash
docker --version
python3.12 --version   # or any python3 >= 3.11
```

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
bash dependencies.sh          # fails fast on Python < 3.11
```

`es_config.json` (gitignored) must exist before `execute_project.py`
runs — the driver writes it for you (`http`, no auth, matching the
`xpack.security.enabled=false` dev container below).

## Run (agent path)

```bash
.claude/skills/run-entitopia/driver.sh smoke
```

This is the harness that was actually run to verify this skill. It:
1. Builds `entitopia-es-dev:8.6.2` (base ES image + the two analysis
   plugins) if not already built, and starts it on `localhost:9200`
   with `xpack.security.enabled=false` — no TLS/auth to configure.
2. Creates `.venv` with `python3.12` and runs `dependencies.sh`.
3. Writes `es_config.json`.
4. Writes tiny synthetic CSVs into `CMS-Providers/data/hospitals/` and
   `DOT-Commercial/data/{crashes,inspections,carriers}/` (the real
   `download_*.sh` scripts hit CMS/DOT URLs that go stale — see
   Gotchas).
5. Runs `python3 execute_project.py --project=CMS-Providers --step=hospitals`,
   then DOT-Commercial's 5 steps individually (not as one
   `--project=DOT-Commercial` call — see Gotchas on why), force-refreshing
   `crashes-000001`/`inspections-000001` right before the
   `carriers-ingestion-setup` step so the enrichment policies it builds
   actually have data to match against.
6. Force-refreshes and queries both index aliases to confirm documents
   landed, including the `crashes`/`inspections` fields the enrich
   pipeline should have attached to each carrier doc.
7. Runs the entity-resolution demo (`match-demo`, see below).

### Entity-resolution demo (`driver.sh match-demo`)

This is the part that actually exercises the project's stated purpose.
It requires `hospitals-000001` to already be populated (`smoke` does
this; standalone, run `fixtures` then
`run --project=CMS-Providers --step=hospitals` first). It:

1. Indexes one synthetic near-duplicate of hospital `010001` directly
   (not through the CSV pipeline) — same phone/city, but a
   deliberately typo'd name and address (`"Sowth East Helth Med Ctr"`
   vs. the canonical `"SOUTHEAST HEALTH MEDICAL CENTER"`).
2. Runs an **absolute** match — `term` query on `Facility Name.keyword`
   for the canonical name — and shows it finds exactly 1 hit. The
   near-duplicate is structurally invisible to exact matching.
3. Runs a **soft** match — `bool` query filtered (exact) on
   `City.keyword`, ranked by `should` clauses against
   `Facility Name.phonetic` (metaphone-encoded) and
   `Facility Name.clean` with `fuzziness: AUTO` — and shows it returns
   *both* records, the canonical one scored highest and the
   near-duplicate scored lower but present. Verified output:
   ```
   9.599 10001 -> SOUTHEAST HEALTH MEDICAL CENTER
   2.012 __match-demo-dup__ -> Sowth East Helth Med Ctr
   ```
4. Deletes the synthetic doc, leaving the index as `smoke` left it.

This is the pattern to build on for real entity-resolution work: block
on one or more exact fields to keep candidate sets small (`City`,
`State`, maybe a normalized phone/zip), then score the remaining
fields by phonetic + fuzzy closeness instead of requiring identity.
The `name_clean`/`name_phonetic`/`street_clean`/`phone_clean`
analyzers doing this work live in
`CMS-Providers/configuration/hospitals/index-settings.json` (and the
equivalent in `DOT-Commercial/configuration/carriers/`) — they're
already wired up for `Facility Name`, `Facility Name` again
(phonetic), `Addresss`, and `Phone Number`; extending the approach
means adding more `should` clauses across more fields (address line,
phone) and tuning weights, not touching the plugin/analyzer setup.

Individual subcommands, for finer-grained control:

| command | what it does |
|---|---|
| `driver.sh es-up` | build (if needed) + start the ES dev container, wait for green health |
| `driver.sh es-down` | remove the ES dev container |
| `driver.sh venv-setup` | create `.venv` (if missing) and install deps |
| `driver.sh es-config` | write `es_config.json` for the dev container |
| `driver.sh fixtures` | write the synthetic CMS/DOT sample CSVs |
| `driver.sh run <args>` | `python3 execute_project.py <args>`, e.g. `run --project=CMS-Providers --step=hospitals --phase=index-populate` |
| `driver.sh verify` | refresh + count/query the hospitals and carriers indices |
| `driver.sh match-demo` | inject a synthetic near-duplicate hospital, show absolute match missing it vs. soft match finding it, clean up |
| `driver.sh smoke` | all of the above, in order |

Direct invocation (no fixtures, against whatever data/config already
exists — most useful once you have real project data staged):

```bash
source .venv/bin/activate
python3 execute_project.py --project=<ProjectDir> [--step=<step>] [--phase=<phase>]
```

Query results directly:

```bash
curl -s http://localhost:9200/<alias>/_count
curl -s "http://localhost:9200/<alias>/_search?pretty"
```

## Run (human path)

Same as the agent path minus the driver: bring up Elasticsearch
yourself (README points at
https://github.com/freemansoft/docker-scripts/tree/main/elasticsearch),
populate `es_config.json`, drop real downloaded CSVs under
`<Project>/data/<step>/`, then `python3 execute_project.py --project=<ProjectDir>`.

## Test

No automated test suite exists in this repo (verified: no `tests/`
directory, no test runner configured). Correctness is verified by
actually running the pipeline against a live cluster, which is what
`driver.sh smoke` does.

## Gotchas

- **Custom analyzers need plugins, and failures are silent.** Both
  example projects' `index-settings.json` define `name_clean` /
  `name_phonetic` / `street_clean` / `phone_clean` analyzers built on
  `icu_normalizer`, `icu_folding`, and a `phonetic` token filter. Those
  require the `analysis-icu` and `analysis-phonetic` plugins. Without
  them, `PhaseindexCreate` logs a `BadRequestError` warning and
  continues — the index still gets created, just without the intended
  analyzers, and nothing else in the pipeline errors. Always build from
  `driver.sh`'s Dockerfile (or otherwise confirm both plugins are
  installed) rather than a stock `elasticsearch:8.6.2` image.
- **New documents aren't visible immediately after indexing — and this
  silently breaks enrichment, not just `_count`.** ES's default 1s
  refresh interval means a `_count`/`_search` run right after
  `execute_project.py` returns can read `0` even though `parallel_bulk`
  fully completed (the phase handler blocks on consuming the whole
  generator). Worse: `PhaseEnrichmentPolicies.execute_policy` only sees
  *searchable* source documents, so running
  `python3 execute_project.py --project=DOT-Commercial` as one shot
  (crashes/inspections indexed, then enrichment policies built
  milliseconds later, all well under 1s) reproducibly builds enrichment
  indices with **zero** matches — every phase logs `acknowledged: True`
  and nothing errors, but resulting `carriers` docs are missing their
  `crashes`/`inspections` fields entirely. Verified both ways: one-shot
  run → empty enrichment; same steps split with an explicit
  `_refresh` on `crashes-000001`/`inspections-000001` right before the
  `carriers-ingestion-setup` step → enrichment populated correctly.
  `driver.sh smoke` runs DOT-Commercial's steps individually with that
  refresh inserted for exactly this reason — don't collapse it back
  into a single `--project=DOT-Commercial` call without adding the
  refresh some other way.
- **The `download_*.sh` scripts' URLs go stale.** CMS/DOT rehost
  resources under content-hashed URLs that expire; both
  `CMS-Providers/download_cms_provider.sh` and
  `DOT-Commercial/download_commercial_carriers.sh` had at least one
  dead link at the time this skill was written (verified: the
  `Hospital_General_Information.csv` URL in the CMS script now 404s).
  Don't rely on them for a smoke test — `driver.sh fixtures` writes
  small synthetic CSVs with just the columns each project's
  `index-mappings.json`/`enrichment-policies.json`/`pipelines.json`
  reference, which is enough to exercise every phase.
- **Rerunning against an already-populated cluster is not clean.**
  `index-create` on a date-suffixed index (`{now/d}`) that already
  exists from an earlier run today logs a `resource_already_exists_exception`
  warning and continues (alias/mapping/populate still proceed) — this
  is expected idempotent behavior, not a failure, as long as you're
  fine re-upserting the same `_id`s.
- **`DOT-Commercial/configuration/crashes` and `inspections` have no
  `index-mappings.json` or `index-settings.json`.** Only `index-config.json`
  exists for those two steps; `index-map` silently no-ops (config load
  returns `None`) and `index-create` uses ES's default settings. This
  is how the project is actually configured, not a bug in the driver.
- **`es_config.json` and `*/data/` are gitignored**, and now so is
  `.venv/`. The driver recreates all three from scratch every run —
  never assume they're present, and never commit them.

## Troubleshooting

- **`entitopia requires Python 3.11 or higher (found 3.9.x)`**: you're
  on the system `/usr/bin/python3`. Use `python3.12` (or any 3.11+
  interpreter) to create `.venv`, then `source .venv/bin/activate`
  before running anything.
- **`_count` / `_search` returns 0 hits right after a run that logged
  "Indexing N records"**: refresh-interval timing, not a failed load —
  `curl -X POST http://localhost:9200/<alias>/_refresh` then re-query.
- **Enrichment fields (`inspections`, `crashes`) missing from a
  `carriers` doc that the enrich pipeline should have populated**: you
  ran `--project=DOT-Commercial` (or otherwise ran `carriers-ingestion-setup`)
  without refreshing `crashes-000001`/`inspections-000001` first — see
  the Gotchas entry above. `curl -X POST http://localhost:9200/crashes-000001/_refresh`
  and same for `inspections-000001`, then rerun `--step=carriers-ingestion-setup`
  followed by `--step=carriers`.
