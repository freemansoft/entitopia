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
`.claude/skills/run-entitopia/driver.sh smoke` — it brings up the project's
cluster (refusing to write fixtures into one that holds loaded data), builds
the venv, writes tiny synthetic fixtures, runs both
example projects end-to-end, verifies documents landed, **and runs a
soft-match demo** (`match-demo`) that proves the entity-resolution
capability actually works, not just that CSVs got indexed.

All paths below are relative to the repo root.

## Related reading

This skill is about **running** entitopia. For **adding a new dataset** —
choosing field types, designing analyzers, wiring enrichment, configuring
matching signals — read `docs/adding-a-dataset.md`, which walks those
decisions in order, plus the hazards section of the top-level `README.md`.

## Prerequisites

- Docker (used to run Elasticsearch 9.4.1 with the `analysis-icu` and
  `analysis-phonetic` plugins — the index-settings.json analyzers in
  both example projects require them; without the plugins, index
  creation/mapping still "succeeds" but the custom analyzers silently
  fail with a 400 that's logged and swallowed).
- Python 3.11+ (enforced at the top of `execute_project.py` and by
  `dependencies.sh`; a `.python-version` pins `3.12`). This repo was
  tested with Python 3.11.

```bash
docker --version
python3 --version      # >= 3.11, used only to create .venv
```

## Setup

The cluster is defined by `docker/compose.yml` in the repo (Elasticsearch
pinned to the same version as the client in `requirements.txt`, with
`analysis-icu` and `analysis-phonetic` installed). `driver.sh es-up` delegates
to it rather than building its own image — an earlier version built a second
container on the same port at a different version, so the two could never run
together.

**That cluster is also the one holding the loaded data**, on the same port and
volume. So `smoke`, fixture-mode `run` and `match-demo` refuse when any open
index holds more than 100,000 documents (`LOADED_CLUSTER_DOCS`): a fixture run
would repoint the real aliases at five-row indexes and rebuild the enrich
policies from them. To run them, stop the loaded cluster and start the same
definition under another compose project. That gives it a fresh volume and
leaves the loaded one, `docker_esdata`, untouched:

```bash
docker compose -f docker/compose.yml down                          # loaded data stays in docker_esdata
docker compose -p entitopia-fixture -f docker/compose.yml up -d    # same container name, fresh volume
.claude/skills/run-entitopia/driver.sh smoke
docker compose -p entitopia-fixture -f docker/compose.yml down -v  # discards only entitopia-fixture_esdata
docker compose -f docker/compose.yml up -d                         # loaded cluster back
```

Fixture mode also refuses to overwrite real downloads in `*/data/` (see
`write_fixture`), so on a machine that has them, load the real CSVs on the
fresh volume instead: `DATA_MODE=sample driver.sh run --project=CMS-Providers
--step=hospitals`. `ALLOW_LOADED_CLUSTER=1` overrides the cluster check; use it
only when replacing the loaded indexes is what you want.

Everything runs from `.venv`; `bash dependencies.sh` creates it and installs
pinned dependencies. Never invoke the system Python.

```bash
bash dependencies.sh   # creates .venv and installs pinned deps
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

1. Brings up `entitopia-es` via `docker/compose.yml` (Elasticsearch 9.4.1
   plus the two analysis plugins) on `localhost:9200` with
   `xpack.security.enabled=false` — no TLS/auth to configure.
2. Creates `.venv` and runs `dependencies.sh` to install pinned deps.
3. Writes `es_config.json`.
4. Writes tiny synthetic CSVs into `CMS-Providers/data/hospitals/` and
   `DOT-Commercial/data/{crashes,inspections,carriers}/` (the real
   `download_*.sh` scripts hit CMS/DOT URLs that go stale — see
   Gotchas).
5. Runs `.venv/bin/python execute_project.py --project=CMS-Providers --step=hospitals`,
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
   deliberately typo'd name and address (`"Sowtheast Helth Med Ctr"`
   vs. the canonical `"SOUTHEAST HEALTH MEDICAL CENTER"`). "Sowtheast"
   stays one word because the analyzers stop `HEALTH`, `MEDICAL` and
   `CENTER`, leaving `southeast` as the canonical name's only token; a
   typo that splits it (the old `"Sowth East"`) shares nothing with it
   and scores 0.
2. Runs an **absolute** match — `term` query on `Facility Name.keyword`
   for the canonical name — and shows it finds exactly 1 hit. The
   near-duplicate is structurally invisible to exact matching.
3. Runs a **soft** match — `bool` query filtered (exact) on
   `City/Town.keyword`, ranked by `should` clauses against
   `Facility Name.phonetic` (metaphone-encoded) and
   `Facility Name.clean` with `fuzziness: AUTO`, with
   `minimum_should_match: 1` so a hospital that only shares the city is
   not listed — and shows it returns _both_ records, the canonical one
   scored highest and the near-duplicate scored lower but present.
   Verified 2026-11-05 on a fresh volume loaded with the real hospitals
   CSV:
   ```
   14.436 010001 -> SOUTHEAST HEALTH MEDICAL CENTER
   7.859 __match-demo-dup__ -> Sowtheast Helth Med Ctr
   ```
4. Deletes the synthetic doc, leaving the index as `smoke` left it.

This is the pattern to build on for real entity-resolution work: block
on one or more exact fields to keep candidate sets small (`City/Town`,
`State`, maybe a normalized phone/zip), then score the remaining
fields by phonetic + fuzzy closeness instead of requiring identity.
The `name_clean`/`name_phonetic`/`street_clean`/`phone_clean`
analyzers doing this work live in
`CMS-Providers/configuration/hospitals/index-settings.json` (and the
equivalent in `DOT-Commercial/configuration/carriers/`) — they're
already wired up for `Facility Name`, `Facility Name` again
(phonetic), `Address`, and `Telephone Number`; extending the approach
means adding more `should` clauses across more fields (address line,
phone) and tuning weights, not touching the plugin/analyzer setup.

Individual subcommands, for finer-grained control:

| command                | what it does                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `driver.sh es-up`      | build (if needed) + start the ES dev container, wait for green health                                                    |
| `driver.sh es-down`    | remove the ES dev container                                                                                              |
| `driver.sh venv-setup` | create `.venv` (if missing) and install deps                                                                             |
| `driver.sh es-config`  | write `es_config.json` for the dev container                                                                             |
| `driver.sh fixtures`   | write the synthetic CMS/DOT sample CSVs                                                                                  |
| `driver.sh run <args>` | `.venv/bin/python execute_project.py <args>`, e.g. `run --project=CMS-Providers --step=hospitals --phase=index-populate` |
| `driver.sh verify`     | refresh + count/query the hospitals and carriers indices                                                                 |
| `driver.sh match-demo` | inject a synthetic near-duplicate hospital, show absolute match missing it vs. soft match finding it, clean up           |
| `driver.sh smoke`      | all of the above, in order                                                                                               |

Direct invocation (no fixtures, against whatever data/config already
exists — most useful once you have real project data staged):

```bash
source .venv/bin/activate
.venv/bin/python execute_project.py --project=<ProjectDir> [--step=<step>] [--phase=<phase>]
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
`<Project>/data/<step>/`, then `.venv/bin/python execute_project.py --project=<ProjectDir>`.

## Test

No automated test suite exists in this repo (verified: no `tests/`
directory, no test runner configured). Correctness is verified by
actually running the pipeline against a live cluster, which is what
`driver.sh smoke` does.

## Re-running on a loaded cluster

The local cluster is usually loaded, and every run writes into it. Each
row below is a way a re-run has overwritten, hidden or mixed earlier work
in this repo, or would. Check this list before running a step, a sweep or
a measurement script a second time, and before comparing two runs.

The last column names the test that keeps each guard in place. A row that
says "Nothing" is avoided only by following its safe move. A change to the
code behind a guarded row should keep its test passing, and a new guard
adds its test to this column.

| Re-run                                                                       | What happens                                                                                                                                                                                                                                                                                                                                                                                                                                                | Safe move                                                                                                                                                                                                                                                                              | Guarded by                                                                                                                                                    |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Any step with `index-create`, same day                                       | Takes the next generation (`<step>-<today>-000002`) and moves the alias to it. Later phases write to today's newest generation. Before PR #43 the rerun wrote into the first run's index, leaving a union of both runs.                                                                                                                                                                                                                                     | Expected. Read the concrete name off the alias (`_cat/aliases/<alias>`) rather than assuming `-000001`. Delete earlier generations once the comparison is done.                                                                                                                        | `tests/test_dated_index_generations.py`                                                                                                                       |
| Any step with `index-create`, any day                                        | The alias moves to the new, empty index and detaches from every older one (PR #9; before it, aliases accumulated and reads returned each document once per index). The old index is kept but is no longer reachable through the alias.                                                                                                                                                                                                                      | Expected for data and sweeps. `--retain-aliases` keeps the old indexes attached, which is almost never what you want.                                                                                                                                                                  | `tests/test_alias_swap.py`                                                                                                                                    |
| `chameleon-validation` step, including a full `--project=DOT-Commercial` run | `measure_crash_lift.py` writes its results through the alias `chameleon-validation-000001`. The step's `index-config.json` is marked `"persistent": true`, so when the alias already names an index, `index-create` keeps it and logs `Keeping persistent index ...`, and `index-map` updates that index. Before this guard, re-creating the index moved the alias onto an empty one and every recorded validation run dropped out of view.                 | Nothing to do on a re-run. To rebuild the index on purpose (an incompatible mapping change), reindex the old results somewhere safe, then remove the alias by hand so the next run creates a fresh index. Mark any new index that scripts append to through an alias `persistent` too. | `tests/test_rerun_guards.py`                                                                                                                                  |
| `--phase=<phase>` on its own                                                 | Writes into today's newest generation, not a fresh one. Documents with the same `_id` are overwritten, so stale documents the rerun no longer produces stay behind. On a later day than the step's `index-create`, `index-map`, `index-populate` and `entity-match` refuse with `No index ... exists: this step's index-create has not run today`. Before this guard, Elasticsearch auto-created the index with dynamic mappings and none of the analyzers. | Fine for resuming a failed load on the same day. Otherwise rerun the whole step so `index-create` runs first.                                                                                                                                                                          | `tests/test_rerun_guards.py`                                                                                                                                  |
| `carriers-ingestion-setup` (enrich policies)                                 | A policy can't be replaced while an ingest pipeline uses it. The preflight in `execute_project.py` predicts this and stops before anything is written (exit 2).                                                                                                                                                                                                                                                                                             | Rerun with `--fix-bound-pipelines`, which deletes the step's own pipeline so its `pipelines` phase recreates it.                                                                                                                                                                       | `tests/test_bound_pipelines.py`, `tests/test_phase_enrichment_policies.py`; the same-day prediction in `tests/test_dated_index_generations.py`                |
| `driver.sh fixtures` / `smoke`                                               | Refuses a cluster that holds loaded data, and refuses to overwrite real downloads in `*/data/`. Before that guard, a smoke run replaced about 8M rows of real CSVs with two-row fixtures, and the only recovery was a re-download.                                                                                                                                                                                                                          | Never bypass either refusal on this machine.                                                                                                                                                                                                                                           | Nothing: the refusals live in `driver.sh` and no test runs them.                                                                                              |
| `compare_sweeps.py --write-baseline` / `run_metrics.py --write-baseline`     | Overwrites the baseline file with no backup. The previous baseline's numbers are gone unless they were recorded elsewhere.                                                                                                                                                                                                                                                                                                                                  | Copy the old baseline aside first, or write to a new filename.                                                                                                                                                                                                                         | Nothing.                                                                                                                                                      |
| `sample_pairs_for_review.py` then `review_page.py render`                    | The sampler refuses to overwrite a sample file that already holds verdicts. Verdicts recorded only in the browser are not in that file. The page keys them by a hash of its sample, so re-rendering `review.html` from a new sample leaves the old verdicts stranded in browser storage with no page to show them.                                                                                                                                          | Run `review_page.py summarize` on the labelled sample first, or keep the old sample and page under new names before redrawing.                                                                                                                                                         | Partly: `tests/test_sample_pairs_for_review.py` tests the verdict count the sampler's refusal relies on. Nothing covers verdicts stranded in browser storage. |
| `pytest` against the live cluster                                            | The live tests create and delete their own `test-*` indexes, so they never touch project data. Two test runs at the same moment can delete each other's indexes.                                                                                                                                                                                                                                                                                            | Run one test session against the cluster at a time.                                                                                                                                                                                                                                    | Nothing.                                                                                                                                                      |

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
  installed) rather than a stock `elasticsearch:9.4.1` image.
- **New documents aren't visible immediately after indexing — and this
  silently breaks enrichment, not just `_count`.** ES's default 1s
  refresh interval means a `_count`/`_search` run right after
  `execute_project.py` returns can read `0` even though `parallel_bulk`
  fully completed (the phase handler blocks on consuming the whole
  generator). Worse: `PhaseEnrichmentPolicies.execute_policy` only sees
  _searchable_ source documents, so running
  `.venv/bin/python execute_project.py --project=DOT-Commercial` as one shot
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
- **A same-day rerun creates a new index.** See
  [Re-running on a loaded cluster](#re-running-on-a-loaded-cluster).
- **`DOT-Commercial/configuration/crashes` and `inspections` have no
  `index-mappings.json` or `index-settings.json`.** Only `index-config.json`
  exists for those two steps; `index-map` silently no-ops (config load
  returns `None`) and `index-create` uses ES's default settings. This
  is how the project is actually configured, not a bug in the driver.
- **`es_config.json` and `*/data/` are gitignored**, and now so is
  `.venv/`. The driver recreates all three from scratch every run —
  never assume they're present, and never commit them.
- **The `elasticsearch` Python client's `Elasticsearch(timeout=...)`
  constructor kwarg was removed in 9.0** (deprecated since 8.0,
  replaced by `request_timeout`). `utils/elasticsearch_utils.py`
  already uses `request_timeout` — if you see
  `TypeError: __init__() got an unexpected keyword argument 'timeout'`,
  something reintroduced the old name. `from elasticsearch import client`
  (`IndicesClient`/`IngestClient`/`EnrichClient`) still works against
  the 9.x client too, just with a `DeprecationWarning` pointing at
  importing those directly from `elasticsearch` instead — harmless,
  left as-is.

## Troubleshooting

- **`entitopia requires Python 3.11 or higher (found 3.9.x)`**: you're
  on the system `/usr/bin/python3`. Use `.venv/bin/python` (or any 3.11+
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
