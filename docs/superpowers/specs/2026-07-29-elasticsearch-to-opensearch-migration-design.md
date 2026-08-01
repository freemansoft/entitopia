# Elasticsearch → OpenSearch Migration — Design (Abandoned)

**Status: abandoned 2026-07-29, before any code was written.** No implementation
plan was produced and no repo files were changed. This document exists so the
research behind the decision — particularly the enrich finding, which took live
verification against OpenSearch's documentation to confirm — does not have to be
redone if the question comes back.

## Purpose

Evaluate replacing Elasticsearch with OpenSearch as entitopia's backing store,
and specify the change if it turned out to be tractable.

The motivation was not stated during the design discussion, and that matters:
the answer differs sharply depending on whether the driver is licensing, an AWS
target, cost, or preference. See "If this is revisited" below.

## The blocking finding

**OpenSearch has no enrich processor and no enrich policy API.**

Both are Elastic-licensed (x-pack), so they never made it into the 7.10 fork,
and OpenSearch has not added them since. Verified two ways:

- OpenSearch's [ingest processors reference](https://docs.opensearch.org/latest/ingest-pipelines/processors/index-processors/)
  lists ~35 processors; `enrich` is not among them, and no enrich policy API is
  documented.
- OpenSearch users attempting `PUT /_enrich/policy/...` report
  `no handler found for uri [/_enrich/policy/...] and method [PUT]`
  ([forum thread](https://forum.opensearch.org/t/alternative-for-enrich-processor/11201)),
  with open feature requests still tracking it.

This is not a syntax difference that a port papers over. **No OpenSearch ingest
processor can query another index**, so there is no server-side substitute at
all. Enrichment has to move into the client or be dropped.

### How much of the project this affects

`DOT-Commercial` depends on it heavily — 6 enrichment policies and 6 `enrich`
processors:

| Config                         | Policies                                                                             | Enrich processors            | Effect                                                  |
| ------------------------------ | ------------------------------------------------------------------------------------ | ---------------------------- | ------------------------------------------------------- |
| `carriers-ingestion-setup/`    | 5 (`inspections`, `crashes`, `auth-history`, `out-of-service-orders`, `boc3-agents`) | 5, all keyed on `dot_number` | Denormalizes 5 datasets into each carrier document      |
| `inspections-ingestion-setup/` | 1 (`inspections-per-unit`)                                                           | 1, keyed on `inspection_id`  | Attaches unit-level VIN/vehicle data to each inspection |

`CMS-Providers` uses **no** enrichment at all. Neither does
`crashes-ingestion-setup/`, whose pipeline holds only a Painless `script`
processor.

This is the crux of the whole evaluation: the phonetic/fuzzy soft-matching that
the README and the `run-entitopia` skill both describe as the project's actual
purpose lives entirely in `CMS-Providers`, and it does not touch enrich.

## What ports cleanly

Everything else. Verified, not assumed:

- **`analysis-icu` and `analysis-phonetic` both exist as OpenSearch plugins.**
  The `name_clean` / `name_phonetic` / `street_clean` / `phone_clean` analyzers
  in both projects' `index-settings.json` port unchanged.
- **`script` / Painless ingest processors exist.** `crashes-pipeline-000001`'s
  `(long)` coercion survives as-is.
- **`parallel_bulk` exists** in `opensearchpy.helpers` with the same signature.
- **`{now/d}` expansion is already client-side** in `utils/elasticsearch_utils.py`,
  so it is unaffected by the backend swap.

Client API differences are shallow and mechanical:

| elasticsearch-py 9.x                            | opensearch-py 3.x                                               |
| ----------------------------------------------- | --------------------------------------------------------------- |
| `basic_auth=[user, pass]`                       | `http_auth=(user, pass)`                                        |
| `scheme` key inside the hosts dict              | `use_ssl=` client kwarg                                         |
| `request_timeout=`                              | `timeout=`                                                      |
| `BadRequestError`                               | `RequestError` (400; `NotFoundError`/`ConflictError` unchanged) |
| `client.IndicesClient(es)` / `IngestClient(es)` | `client.indices` / `client.ingest` attributes                   |
| kwarg-based (`create(index=…, settings=…)`)     | body-based (`create(index=…, body={"settings": …})`)            |
| `elastic_transport.transport` logger            | `opensearchpy` logger                                           |

The attribute form also drops the `DeprecationWarning` that the `run-entitopia`
skill currently documents as a harmless known wart.

## Decisions taken during design

Each was an explicit choice between presented alternatives:

1. **Replace Elasticsearch entirely** rather than supporting both backends behind
   an abstraction layer. Git history preserves the ES version.
2. **Reimplement enrichment client-side, sourced from the index** (not from the
   CSVs). Index-sourced matters because it reads _post-pipeline_ values — the
   `crashes.dot_number` Painless cast is applied before the enricher sees it,
   whereas a pandas-level CSV join would bypass it and let the two paths
   disagree.
3. **Self-managed Docker only.** Explicitly _not_ AWS managed OpenSearch — see
   below.
4. **Batched `terms` queries per bulk chunk**, not a prebuilt in-memory lookup.
   This was forced by a constraint discovered mid-design: steps run as separate
   processes (`driver.sh` runs DOT-Commercial's steps individually, and that is
   the documented workaround for the refresh race), so an in-memory lookup built
   during `carriers-ingestion-setup` cannot survive into `carriers`. A lookup
   built inside `index-populate` instead would have to hold every enrich field
   of every source document in memory — `inspections-per-unit` at full scale is
   a plausible OOM.
5. **Rename code and config files, keep phase names.** `es_config.json` →
   `opensearch_config.json`, `elasticsearch_utils.py` → `opensearch_utils.py`,
   `es` → `client`; but `enrichment-policies` stays as the phase name and
   `enrichment-policies.json` keeps its filename and schema, so no per-project
   config files need editing. The concept survives; only the mechanism changes.

## The design that was specified

Parts 1 and 2 were reviewed and accepted. Part 3 was never presented.

### Part 1 — client layer and mechanical port

`connect_to_es` → `connect_to_opensearch` returning an `opensearchpy.OpenSearch`,
applying the API mapping table above. `opensearch_config.json` keeps its existing
five keys **including `scheme`** — the translation to `use_ssl` happens inside
the connect function, so nothing that writes the config file changes shape.
`verify_certs=False` stays, plus `ssl_show_warn=False`.

`indices.create` omits `body` entirely when a step has no `index-settings.json`
(`DOT-Commercial/configuration/crashes` and `inspections` are in this state).
`put_alias` and `delete_pipeline` are unchanged. `requirements.txt` swaps
`elasticsearch==9.4.1` for `opensearch-py~=3.0`.

### Part 2 — replacing enrich

Three pieces.

**`pipelines` phase splits its processor list.** It partitions `processors` into
`enrich` and everything else, and PUTs only the non-enrich ones.
`crashes-pipeline-000001` survives as a real server-side pipeline.
`inspections-pipeline-000001` and `carrier-enrichment-pipeline-000001` are
enrich-only, so nothing remains, no pipeline is created, and `index-populate`
omits the `pipeline` key from those bulk actions rather than referencing
something that does not exist. The `enrich` blocks stay in `pipelines.json` as
the declaration of _what_ to enrich — one source of truth, no config edits.

**Resolving what to enrich.** `carriers/index-config.json` names
`carrier-enrichment-pipeline-000001`, but that pipeline is defined under a
sibling step directory. `index-populate` scans the project's
`configurationDir` for the `pipelines.json` whose `name` matches, then reads
`enrichment-policies.json` from that same directory — the two files are already
colocated in both ingestion-setup steps. A duplicate name logs a warning.

**New `utils/enrich_utils.py`.** A `ClientSideEnricher` holds the resolved
(processor, policy) pairs. `record_action` batches records, calls
`enrich_batch(records)` to mutate them in place, then yields. Per policy per
batch, one search against `policy.match.indices`:

- a `terms` filter on `match_field` over the batch's distinct keys,
- a `terms` aggregation on `match_field` with a `top_hits` sub-aggregation of
  `size: max_matches`, `_source` limited to `enrich_fields`.

The aggregation is what preserves Elasticsearch's **per-document** `max_matches`
cap; a flat query with a large `size` would let one hot `dot_number` consume the
whole budget. Two constraints on this: `max_matches` is a string in the existing
configs and needs `int()`, and OpenSearch's default
`index.max_inner_result_window` is 100 — the largest value in use is exactly
100, so it fits, but that is the ceiling.

Results are grouped by key and written to `target_field` as a list. Records with
no match get no key at all, matching ES enrich and the "missing `crashes` field"
behavior the README documents. All current specs use `max_matches > 1`, so the
ES rule where `max_matches: 1` yields a bare object was deliberately not
implemented.

**Key normalization — a real bug fix.** `carriers.dot_number` is `keyword` while
`crashes.dot_number` and `inspections.dot_number` are `long`. Today that
cross-type match works only through implicit Elasticsearch coercion, and the
README records a Painless `(long)` cast added specifically because
`'3240797.0'` would never match. The enricher would normalize both sides through
one function (int-valued floats rendered without the decimal) so terms values
and hit grouping always agree — removing that entire class of float-formatting
bug rather than relying on coercion. The crashes pipeline cast still stays; it
is needed for the stored `_source`.

**`enrichment-policies` phase becomes validate-and-refresh.** No `EnrichClient`.
It resolves `{now/d}`, refreshes each source index, counts it, and logs an error
if the index is missing or empty. This is a direct fix for the silent zero-match
race documented at length in both `README.md` and
`.claude/skills/run-entitopia/SKILL.md`, where every phase logs
`acknowledged: True`, nothing errors, and carrier documents silently come out
missing their `crashes`/`inspections` fields. It converts an invisible
wrong-output failure into a loud one at the point where it is caused.

**Threading.** `parallel_bulk`'s thread pool consumes the actions generator from
a single thread, so the enricher is only ever called serially and needs no
locking.

**Known cost.** Materially slower than server-side enrich at full scale.
Carriers is 5 policies over ~2M documents; at a 100-record enrich batch that is
~100k searches. Batch size becomes the tuning knob, trading round trips against
`max_matches × batch` response size. Smoke-test volumes would not notice; a full
`DOT-Commercial` load would.

### Part 3 — infrastructure and docs (never designed)

Scope was identified but never specified or reviewed: `driver.sh`'s Dockerfile
and container setup (`opensearchproject/opensearch` + the two analysis plugins,
security demo config disabled, replacing the `xpack.security.enabled=false`
dev container), the `run-entitopia` skill's prerequisites/gotchas/troubleshooting
sections, and `README.md`.

## Why it was abandoned

Called on 2026-07-29 after Part 2 was reviewed. The trade as it stood:

- **Gained:** independence from Elasticsearch, plus the validation phase closing
  the silent zero-match bug.
- **Lost:** a vendor-maintained feature, replaced by roughly 200 lines of
  enrichment code the project would then own and maintain.
- **Degraded:** full-scale `DOT-Commercial` carrier loads get meaningfully
  slower.

With no stated driver forcing the move, the enrichment rewrite was not worth
owning.

## If this is revisited

- **The decision hinges only on the enrichment rewrite.** Everything else is a
  boring mechanical port. Do not re-research the rest.
- **A partial migration is a real option** that was raised and not taken:
  port `CMS-Providers` and `DOT-Commercial`'s non-enriched steps, and leave
  carriers/inspections denormalization as a documented gap. `CMS-Providers`
  ports with zero capability loss, and it is the project that actually
  demonstrates the stated soft/probabilistic entity-matching purpose.
- ~~**AWS managed OpenSearch is disqualifying.** It does not permit installing
  `analysis-phonetic`, so the metaphone-encoded `name_phonetic` analyzer — the
  core of the entity-matching demo — cannot exist there. If the motive for
  migrating is ever "move to AWS managed OpenSearch," the answer is no, and the
  reason is the plugin, not the enrich gap.~~

  **CORRECTED 2026-07-30 — this was wrong.** Amazon OpenSearch Service ships
  **Phonetic Analysis** (minimum OpenSearch version 1.0) and **ICU Analysis**
  ("Included on all domains") as prepackaged plugins, per
  [Plugins by engine version in Amazon OpenSearch Service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-plugins.html).
  Both `name_phonetic` and the ICU-based analyzers run on AWS managed
  OpenSearch unchanged. AWS is therefore **not** disqualifying, and the enrich
  gap above is the only real blocker — which is what the rest of this document
  already concluded.
- **The validation-and-refresh fix is independently worth doing.** Refreshing
  and counting each enrichment policy's source index, and erroring on empty,
  would close the documented silent zero-match failure on the current
  Elasticsearch stack with no migration at all.
