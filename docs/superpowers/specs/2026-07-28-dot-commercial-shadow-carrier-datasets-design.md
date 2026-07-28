# DOT-Commercial Shadow/Chameleon-Carrier Datasets — Design

## Purpose

`DOT-Commercial` currently indexes 4 FMCSA datasets (`carriers`, `crashes`, `inspections`, `inspections-per-unit`), all keyed on `dot_number`, joined via Elasticsearch enrichment into a single `carriers` document. That's enough to profile a carrier's own safety record, but it has no signal for the specific fraud pattern this project cares about: a **shadow/chameleon carrier** — an entity that gets shut down for safety violations and reappears under a new DOT number/name while reusing the same address, legal-process agent, insurer, or equipment. Detecting that requires data that links _different_ DOT numbers to each other through shared infrastructure, not just each carrier's own history in isolation.

This surveys the FMCSA datasets available on `data.transportation.gov` (same Socrata source `fetch_commercial_carriers.py` already uses) for that purpose, tiers them by how directly they serve chameleon-carrier detection, and specifies the build for Tier 1 — the three datasets with the strongest, most direct reincarnation signal. Tiers 2 and 3 are documented but explicitly not built now.

**Scope constraint (explicit user decision):** DOT/FMCSA Socrata datasets only. Non-DOT sources that came up during research — state Secretary-of-State corporate registration (registered-agent/officer reuse across LLCs) and NMVTIS vehicle title data (VIN re-titled under a new carrier) — are the two datasets industry/OIG chameleon-carrier studies rely on most, but they're out of scope: not on `data.transportation.gov`, not fetchable via the existing Socrata pipeline, and out of scope per instruction.

## The tiers

### Tier 1 — direct reincarnation signals (built by this spec)

| Dataset                       | Socrata ID  | Rows      | Why it's Tier 1                                                                                                                                                                                                                                                                                          |
| ----------------------------- | ----------- | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AuthHist - All With History` | `9mw4-x3tu` | 4,941,925 | Every authority grant/revocation event per `dot_number`/`docket_number`. The classic chameleon pattern is authority revoked → new DOT# granted within weeks; this is what lets that timing be detected, not just current status.                                                                         |
| `OUT OF SERVICE ORDERS`       | `p2mt-9ige` | 394,963   | `dot_number`, `legal_name`, `dba_name`, `oos_date`, `oos_reason`, `rescind_date`. Flags exactly which carriers were shut down for safety — the prime candidates for "who reappeared nearby afterward."                                                                                                   |
| `BOC3 - All With History`     | `2emp-mxtb` | 1,860,604 | Each carrier's designated legal process agent (name + address) by `dot_number`/`docket_number`. Unrelated-looking carriers sharing the same BOC-3 agent — or acting as their own agent at one address — is a harder signal to fake than a business address, and isn't in the current census data at all. |

(`Revocation - All With History`, `sa6p-acbp`, was also investigated — it's a redundant subset of what `AuthHist` already covers with a narrower reason code, so it's dropped in favor of `AuthHist` alone rather than double-counting the same signal.)

### Tier 2 — insurance churn (deferred)

`Insur`/`ActPendInsur`/`InsHist`/`Rejected - All With History` (`ypjt-5ydn`, `qh9u-swkp`, `6sqe-dvqs`, `96tg-4mhf`) — insurance company, policy dates, cancellation method, per `docket_number`/`dot_number`. Same insurer/agent binding a new DOT# right after an old one's policy lapses or is cancelled is a strong shadow-carrier signature, and insurance gaps are often the actual trigger for a re-registration. Not built now — revisit once Tier 1 is validated and the value of a fourth-plus join is clearer relative to the added pipeline complexity.

### Tier 3 — richer safety/entity history (deferred)

- `Carrier - All With History` (`6eyk-hxee`) — broader than the current `carriers` census snapshot (`kjg3-diqy`); includes inactive/pending authorities and both `dot_number` + `docket_number`, so it could later serve as the crosswalk between Tier 2's licensing tables (keyed by docket) and the crash/inspection tables (keyed by DOT#) if Tier 2 is ever built.
- `SMS Input - Violation` (`8mt8-2mdr`) — violation-level detail behind the existing `inspections` data.
- `SMS AB PassProperty` (`4y6x-dmck`) — FMCSA's official BASIC percentile/alert scores per carrier, a ready-made risk score not currently computed.

Not built now — these enrich carriers' own safety picture rather than linking distinct carriers to each other, so they're lower priority than Tier 1/2 for the specific shadow-carrier use case.

## Confirmed dataset facts (verified live against the real API during design)

All three Tier 1 datasets were queried directly against `data.transportation.gov`'s Socrata catalog and resource APIs (not assumed from documentation):

- **Columns are exhaustive and confirmed:**
  - `AuthHist`: `docket_number`, `dot_number`, `sub_number`, `mod_col_1` (authority-type description), `original_action_desc`, `orig_served_date`, `disp_action_desc`, `disp_decided_date`, `disp_served_date`.
  - `OUT OF SERVICE ORDERS`: `dot_number`, `legal_name`, `dba_name`, `oos_date`, `oos_reason`, `status`, `rescind_date`.
  - `BOC3`: `docket_number`, `dot_number`, `co_name`, `attn_to_or_title`, `street_po`, `city`, `state_code`, `ctry_code`, `zip_code`.
- **All Socrata-declared as `Text` type columns** in all three datasets (not `Number`/`Date`), including `dot_number` itself.
- **`dot_number` has zero nulls and zero empty strings in all three datasets** (confirmed via `$where=dot_number IS NULL` / `= ''` returning 0 for each). This is the key fact that avoids repeating the `crashes.dot_number` bug documented in `docs/superpowers/specs/2026-07-28-dot-commercial-inspections-per-unit-design.md`: that bug was caused by pandas inferring `float64` (and therefore emitting `"2975796.0"` into `_source`, which never keyword-matches carriers' clean integer `dot_number`) specifically _because_ ~19.5% of `crashes.dot_number` values were null. With zero nulls here, pandas' CSV type inference produces a clean `int64` column for all three datasets — confirmed by direct inspection of raw `.csv`-export samples (the same export format `fetch_commercial_carriers.py` pulls):
  - `AuthHist`/`BOC3` export `dot_number` **zero-padded to 8 digits** as literal text (e.g. `"00085526"`, `"00000000"`); `OOS Orders` exports it unpadded (e.g. `"1438"`). Both forms parse to the same clean `int64` value on the DOT-number-bearing rows — the padding is cosmetic.
  - `"00000000"` is a real, common sentinel (247,029 rows in `AuthHist`, 159,141 in `BOC3`) meaning "no USDOT number" — freight-forwarder/broker-only authorities and agents that only have a docket number. It parses to `dot_number: 0`. This is treated as a benign, self-resolving edge case: it will simply never match a real carrier (none has `dot_number` 0), which is exactly the semantically correct outcome — not a bug requiring a fix.
  - **Conclusion: no `crashes`-style Painless-script coercion pipeline is needed for these three datasets.** An explicit `long` mapping on `dot_number` is still added to each, for consistency with the rest of the project's defensive-typing convention, but — per the corrected understanding documented in the inspections-per-unit spec — that mapping alone is cosmetic for search-time behavior, not what makes the enrich-policy match succeed; the null-free `_source` value is what makes it succeed here.
- **No usable date field for incremental windowing:**
  - `AuthHist`'s only date fields (`orig_served_date`, `disp_served_date`, `disp_decided_date`) are exported in **`MM/DD/YYYY` text format** (confirmed via raw sample: `"05/13/1981"`), which is incompatible with `compute_where_clause`'s existing `YYYYMMDD`-cutoff lexicographic string comparison (month-first ordering would badly misorder rows relative to a year-first cutoff — this is a real, load-bearing incompatibility, not a style nit).
  - `BOC3` has no date field at all.
  - `OOS Orders`' `oos_date` **is** in clean ISO `YYYY-MM-DD` format and _would_ mostly work with the existing windowing code (empirically tested: a `YYYYMMDD`-format cutoff against dashed values produces almost-correct results, off by a handful of rows right at the exact boundary date, because the `-` character sorts before digits at that one position). Windowing it anyway was rejected on purpose, not just left out for convenience: **chameleon-carrier detection needs full OOS history, not a rolling recent window** — the whole point is catching a carrier that was shut down years ago and only now reappears. A 24-month window would actively defeat the feature.
  - **Decision: all three datasets are full, unwindowed pulls** (`date_field: null`, `window_months: null`), the same pattern already used for `carriers` (2,084,753 rows, no windowing). Row counts (4.9M / 395K / 1.86M) are each individually smaller than or comparable to `carriers`, so this isn't a new scale problem.
- **No natural single-column unique key exists in any of the three datasets** (no `id_field` candidate). This matches the `crashes` dataset's existing, already-accepted situation (documented as a known quirk in the inspections-per-unit plan: no `id_field` → Elasticsearch auto-generates a random `_id` on every populate, so re-running `index-populate` against the same day's index without deleting it first doubles the document count instead of overwriting). Note this is a genuine constraint of `phase_index_populate.py`, not a design oversight to fix here: `_id` is fixed from `record[id_field]` in the raw CSV row _before_ the ingest pipeline runs, so an ingest-time `fingerprint` processor (like `crashes-ingestion-setup` already uses) **cannot** be used to synthesize an `_id` — only a literal source-CSV column can. None of the three datasets has one. **Same operational consequence as `crashes`: re-running `index-populate` for these three without deleting the current day's index first will double-count.** Accepted, consistent with existing precedent, not fixed here.

## Scope

Adds three new datasets/indexes — `auth-history`, `out-of-service-orders`, `boc3-agents` — each fetched, indexed, and enriched directly onto `carriers` via `dot_number`, exactly like the existing `crashes`/`inspections` → `carriers` enrichment (a single hop, no intermediate `-ingestion-setup` step of their own needed, unlike `inspections-per-unit`'s two-hop design, since all three key directly on `dot_number` against `carriers`). Also updates `DOT-Commercial/README.md`'s dataset/step documentation.

## Architecture

Reuses the existing fetch infrastructure completely unchanged — three new `fetch-config.json` entries, no Python code changes. Each new dataset gets its own index (`index-create`, `index-map`, `index-populate`), populated independently and directly from CSV (no pipeline of its own). The existing `carriers-ingestion-setup` step's `enrichment-policies.json` and `pipelines.json` — which already build `inspections-enrichment-policy` and `crashes-enrichment-policy` and wire both into `carrier-enrichment-pipeline-000001` — gain three more policies and three more `enrich` processors in the same files, appended to the same pipeline. `carriers/index-config.json` needs no change; it already references `carrier-enrichment-pipeline-000001`.

## `fetch-config.json` additions

```json
"auth_history": {
    "dataset_id": "9mw4-x3tu",
    "output": "data/auth-history/auth_history.csv",
    "date_field": null,
    "window_months": null
},
"out_of_service_orders": {
    "dataset_id": "p2mt-9ige",
    "output": "data/out-of-service-orders/out_of_service_orders.csv",
    "date_field": null,
    "window_months": null
},
"boc3_agents": {
    "dataset_id": "2emp-mxtb",
    "output": "data/boc3-agents/boc3_agents.csv",
    "date_field": null,
    "window_months": null
}
```

## New project structure

For each of `auth-history`, `out-of-service-orders`, `boc3-agents`:

- `DOT-Commercial/configuration/<name>/index-config.json` — `source: "<name-underscored>.csv"`, no `id_field` (see "no natural unique key" above), `num_rows: null`.
- `DOT-Commercial/configuration/<name>/index-mappings.json` — explicit `long` for `dot_number`; `keyword` for every other field (all short, exact-match/lookup identifier and code fields — no free-text carrier-name-style fuzzy matching needed here, that's `carriers`' own job).
- `DOT-Commercial/configuration/<name>/index-settings.json` — plain 1 shard / 1 replica, no custom analyzers, matching `inspections-per-unit`'s precedent for exact-match-only supporting datasets.

## Enrichment wiring

`DOT-Commercial/configuration/carriers-ingestion-setup/enrichment-policies.json` gains three entries, appended to the existing two:

```json
{
    "name": "auth-history-enrichment-policy",
    "match": {
        "indices": "auth-history-{now/d}-000001",
        "match_field": "dot_number",
        "enrich_fields": [
            "dot_number",
            "docket_number",
            "sub_number",
            "mod_col_1",
            "original_action_desc",
            "orig_served_date",
            "disp_action_desc",
            "disp_decided_date",
            "disp_served_date"
        ]
    }
},
{
    "name": "out-of-service-orders-enrichment-policy",
    "match": {
        "indices": "out-of-service-orders-{now/d}-000001",
        "match_field": "dot_number",
        "enrich_fields": [
            "dot_number",
            "oos_date",
            "oos_reason",
            "status",
            "rescind_date"
        ]
    }
},
{
    "name": "boc3-agents-enrichment-policy",
    "match": {
        "indices": "boc3-agents-{now/d}-000001",
        "match_field": "dot_number",
        "enrich_fields": [
            "dot_number",
            "docket_number",
            "co_name",
            "attn_to_or_title",
            "street_po",
            "city",
            "state_code",
            "zip_code",
            "ctry_code"
        ]
    }
}
```

`legal_name`/`dba_name` are deliberately dropped from the OOS enrich fields — redundant with `carriers`' own name fields, and the signal here is the event (date/reason/status), not the name.

`DOT-Commercial/configuration/carriers-ingestion-setup/pipelines.json` gains three `enrich` processors, appended to `carrier-enrichment-pipeline-000001`'s existing two:

```json
{
    "enrich": {
        "description": "slipstream 'authority history' data (grants/revocations by docket/DOT number — the reincarnation timing signal)",
        "policy_name": "auth-history-enrichment-policy",
        "field": "dot_number",
        "target_field": "auth_history",
        "max_matches": "50"
    }
},
{
    "enrich": {
        "description": "slipstream 'out of service order' data",
        "policy_name": "out-of-service-orders-enrichment-policy",
        "field": "dot_number",
        "target_field": "out_of_service_orders",
        "max_matches": "20"
    }
},
{
    "enrich": {
        "description": "slipstream 'BOC-3 process agent' data (shared-agent/address fingerprint across otherwise-unrelated carriers)",
        "policy_name": "boc3-agents-enrichment-policy",
        "field": "dot_number",
        "target_field": "boc3_agents",
        "max_matches": "20"
    }
}
```

`max_matches` values are sized to each dataset's realistic per-carrier cardinality (vs. the existing `inspections`/`crashes` enrichments' `100`, and `inspections-per-unit`'s `10`): `auth_history` can accumulate many events per carrier over decades across multiple authority types (common/contract/broker/freight-forwarder), so `50`; `out_of_service_orders` and `boc3_agents` are typically single-digit-to-low-double-digit per carrier even for repeat offenders/multiple agent changes, so `20`.

`DOT-Commercial/configuration.json`'s `steps` list gains three new step entries (`index-create`, `index-map`, `index-populate` phases each — no `-ingestion-setup` step needed for any of them, unlike `inspections-per-unit`), inserted before the existing `carriers-ingestion-setup` step, in this order: `... → inspections → auth-history → out-of-service-orders → boc3-agents → carriers-ingestion-setup → carriers`.

## Validation findings (post-build, against the real cluster)

Full end-to-end validation was run: all three indexes populated (validation-scale samples first, then `out-of-service-orders` and `boc3-agents` at full production scale — 394,963 and 1,860,604 docs respectively; `auth-history`'s ~65-70 minute full pull was deferred, left at a 5,000-row sample), enrichment policies/pipeline rebuilt, and `carriers` re-populated through the updated pipeline. Two findings worth carrying forward into any future detection-query work:

- **The "no `id_field`" limitation is real, not just theoretical.** Populating `out-of-service-orders`/`boc3-agents` a second time (validation sample, then full scale) against the same day's index without deleting it first produced exactly the predicted accumulation: `394,963 + 5,000 = 399,963` and `1,860,604 + 5,000 = 1,865,604` documents. Confirmed the fix is simply deleting the day's index before a same-day rerun (matching the `crashes` precedent) — done here before finalizing the full-scale counts above.
- **Naive "shared BOC-3 address" is a poor standalone chameleon signal — it's dominated by legitimate bulk filing services.** Aggregating `boc3-agents` by `street_po` found the most "shared" addresses used by 700+ distinct `dot_number`s each; inspecting them showed `co_name` values like "CORPORATE CREATIONS NETWORK INC," "FMCA FILINGS LLC," and "STONE MOUNTAIN AGENTS LLC" — commercial registered-agent businesses that legitimately file for hundreds of unrelated carriers, not shadow-carrier clusters. Even narrowing to addresses shared by only 2-3 `dot_number`s still surfaced the same kind of professional filing service in every case checked. **Implication for future detection-query design:** raw address-sharing count/frequency is not usable as a standalone signal. A real query needs to either (a) filter out/deprioritize addresses associated with a recognizable third-party filing service (`co_name` populated with a business-sounding name, high overall cardinality), and instead weight toward carriers acting as their **own** BOC-3 agent (`attn_to_or_title` is an individual person's name, `co_name` null/blank) sharing an address with only one or two other carriers, or (b) require the address-sharing signal to co-occur with a timing signal from `auth_history`/`out_of_service_orders` (e.g. one of the sharing carriers was revoked/OOS'd shortly before the other's authority was granted) rather than relying on address overlap alone. Neither is built here — flagged for whoever designs the actual detection query.
- **A real cross-domain match was confirmed working**, independent of the address-sharing caveat above: DOT# `1007209` ("DAY ONE AUTO TRANSPORT LLC") shows a real revoked → reinstated → revoked authority cycle in its enriched `auth_history` — the kind of instability pattern this dataset exists to surface.

## Edge cases

- **`"00000000"` `dot_number` sentinel** (freight-forwarder/broker-only authorities/agents with no USDOT number): parses to `dot_number: 0`, never matches a real carrier. Correct, not a bug.
- **No `id_field` on any of the three new datasets**: re-running their `index-populate` phase against an already-populated same-day index without deleting it first will double-count documents, identical to the existing accepted `crashes` behavior. Not fixed here — same precedent, same workaround (delete the day's index before a same-day rerun).
- **`AuthHist` can have many rows per `dot_number`/`docket_number` pair** (grants, amendments, revocations, dismissals, reinstatements, across multiple authority types) — `max_matches: 50` is a judgment call, not empirically verified against the live data during design; if real carriers exceed it, the enrichment silently truncates rather than erroring (same behavior as every other enrich processor in this project).
- **`BOC3`'s lack of any date field** means "All With History" rows for the same carrier/agent pair spanning different periods are indistinguishable from each other in the data itself — no way to tell current agent from a past one without cross-referencing `AuthHist` or `carriers`' own current-agent field (if any). Not resolved here; flagged for whoever consumes `boc3_agents` downstream.

## Validation plan

1. Fetch all three CSVs from the real API; confirm row counts are in the expected range (4.9M / 395K / 1.86M) and `dot_number` has no float-suffix values in a raw sample.
2. Run the three new index steps, then re-run `carriers-ingestion-setup` (recreates all five enrichment policies + the pipeline) and `carriers` `index-populate`.
3. Confirm via real `_mapping` calls that `dot_number` is explicitly `long` on all three new indexes.
4. Confirm via real `_count`/`_search` that `carriers` documents now carry non-empty `auth_history`, `out_of_service_orders`, and `boc3_agents` fields for a sample of real DOT numbers known to appear in each source dataset.
5. Spot-check a real chameleon-carrier candidate end-to-end: find a `dot_number` with an `out_of_service_orders` entry, confirm its `boc3_agents`/`auth_history` data is populated, and manually cross-reference (outside this pipeline) whether another `carriers` document shares the same BOC-3 agent address — a manual proof-of-concept for the detection use case this data exists to serve, not an automated check.
6. Confirm existing `carriers`/`crashes`/`inspections`/`inspections-per-unit` ingestion still works unchanged (regression check).

## Explicitly out of scope

- Tier 2 (insurance-churn datasets) and Tier 3 (`Carrier - All With History`, `SMS Input - Violation`, `SMS AB PassProperty`) — documented above, not built.
- Non-DOT data sources (state Secretary-of-State corporate registration, NMVTIS vehicle titles) — explicitly excluded per current scope decision, despite being individually strong signals.
- Any actual cross-carrier matching/clustering logic (e.g. an ES query or job that groups carriers sharing a `boc3_agents.street_po`) — this spec only gets the raw joined data onto `carriers` documents; building the detection query/report itself is future work.
- Fixing the pre-existing "no `id_field` → duplicate on same-day rerun" limitation — accepted, consistent with the existing `crashes` precedent.
- Incremental sync — all three datasets follow the same full-pull-per-run model as `carriers`.
