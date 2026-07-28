# DOT-Commercial Inspections-Per-Unit (VIN Data) — Design

## Purpose

The current Vehicle Inspection File dataset (`fx4q-ay7w`) has no VIN field at all — `VIN`/`VIN2` from the old FMCSA convention have no equivalent in the new schema. FMCSA publishes VIN and other unit-level detail (make, company, license plate, CVSA decal) in a separate child dataset, "Inspections Per Unit" (`wt8s-2hbx`, linked from the [FMCSA Data Dissemination Program](https://www.fmcsa.dot.gov/registration/fmcsa-data-dissemination-program) page). This adds that dataset as a fourth fetch/index target, joined onto `inspections` via Elasticsearch enrichment.

Separately, this applies the same fix — explicit Elasticsearch field-type mapping for enrich join keys, instead of relying on dynamic mapping — to close out the already-known, deferred `crashes.dot_number` float/long mismatch bug that currently makes `crashes-enrichment-into-carriers` return zero matches.

## Confirmed dataset facts (verified live against the real API during design)

- Dataset ID `wt8s-2hbx`, 13,659,659 rows — a child table: one inspection can have multiple units (e.g. tractor + trailer), confirmed via a live sample (`inspection_id=79262518` has 2 unit rows).
- Columns: `change_date`, `inspection_id`, `insp_unit_id`, `insp_unit_type_id`, `insp_unit_number`, `insp_unit_make`, `insp_unit_company`, `insp_unit_license`, `insp_unit_license_state`, `insp_unit_vehicle_id_number` (VIN), `insp_unit_decal`, `insp_unit_decal_number`.
- `inspection_id`: 0 nulls in both this table and the main inspections table — lower type-mismatch risk than the `crashes.dot_number` case (~19.5% nulls), but the explicit-mapping fix is applied anyway, defensively and consistently.
- `insp_unit_vehicle_id_number` (VIN): 30,527 nulls out of 13,659,659 (~0.22%) — mostly populated.
- No inspection-event date field exists on this table. `change_date` (record-modification timestamp, format `YYYYMMDD HHMM`, 0 nulls) is used as an approximate recency proxy.
- Confirmed the join works end-to-end: a real `inspection_id` (`79707964`) from the main inspections table returns a matching unit row with a real VIN (`1FDWE3FL2GDC20057`).
- Confirmed `compute_where_clause`'s existing `%Y%m%d`-formatted cutoff string comparison works correctly against `change_date`'s `"YYYYMMDD HHMM"` format with no code change: lexicographic string comparison correctly orders records regardless of the trailing time component, since a shorter cutoff string that's a prefix of a longer value compares as "less than" in Python — no function changes needed.

## Scope

Adds one new dataset/index (`inspections-per-unit`) and one new enrich hop (`inspections-per-unit` → `inspections`). Also fixes the existing `crashes.dot_number` mapping bug. Does **not** chain enrichment through to `carriers` — that's documented as a future backlog item (see below), not built now.

## Architecture

Reuses the existing fetch infrastructure completely unchanged: `fetch_dataset`/`compute_where_clause` in `fetch_commercial_carriers.py` require no code changes, only a new `fetch-config.json` entry. The new dataset gets its own index (`inspections-per-unit`), populated independently, then enriches directly onto `inspections` via a new Elasticsearch enrich policy + pipeline — mirroring the existing `crashes`/`inspections` → `carriers` pattern, just one level shallower (this is the first time `inspections` itself becomes an enrichment *target* rather than only ever a *source*).

## `fetch-config.json` addition

```json
"inspections_per_unit": {
    "dataset_id": "wt8s-2hbx",
    "output": "data/inspections-per-unit/inspections_per_unit.csv",
    "date_field": "change_date",
    "window_months": 24
}
```

## New project structure

- `DOT-Commercial/configuration/inspections-per-unit/index-config.json` — `source: "inspections_per_unit.csv"`, `id_field: "insp_unit_id"`, `num_rows: null`.
- `DOT-Commercial/configuration/inspections-per-unit/index-mappings.json` (new) — explicit types for the fields that matter: `inspection_id: long`, `insp_unit_vehicle_id_number: keyword` (VIN is an exact-match lookup field, not full-text), other unit fields as plain `keyword`/`text` as appropriate.
- `DOT-Commercial/configuration/inspections-per-unit/index-settings.json` — plain settings; no custom analyzers needed (VIN/plate/company lookups are exact-match, not fuzzy — unlike carriers' name/address fields).

## Enrichment pipeline

New step `inspections-ingestion-setup` (mirrors `crashes-ingestion-setup`'s shape), added to `DOT-Commercial/configuration.json`'s `steps` list, running *after* `inspections-per-unit` is fully populated but *before* `inspections`'s own `index-populate` phase — a new inter-dataset ordering dependency, alongside the existing crashes/inspections-before-carriers one.

`DOT-Commercial/configuration/inspections-ingestion-setup/enrichment-policies.json`:
```json
[
    {
        "name": "inspections-per-unit-enrichment-policy",
        "match": {
            "indices": "inspections-per-unit-{now/d}-000001",
            "match_field": "inspection_id",
            "enrich_fields": [
                "inspection_id",
                "insp_unit_vehicle_id_number",
                "insp_unit_make",
                "insp_unit_company",
                "insp_unit_license",
                "insp_unit_license_state",
                "insp_unit_type_id"
            ]
        }
    }
]
```

`DOT-Commercial/configuration/inspections-ingestion-setup/pipelines.json`:
```json
{
    "name": "inspections-pipeline-000001",
    "processors": [
        {
            "enrich": {
                "description": "slipstream unit-level VIN/vehicle data",
                "policy_name": "inspections-per-unit-enrichment-policy",
                "field": "inspection_id",
                "target_field": "units",
                "max_matches": "10"
            }
        }
    ]
}
```
`max_matches: 10` (vs. the carriers enrichments' `100`) since a real inspection typically covers 1-2 units, not dozens.

`DOT-Commercial/configuration/inspections/index-config.json` gains `"pipeline": "inspections-pipeline-000001"` — it currently has none.

## The type-mismatch fix, applied in both places

- `DOT-Commercial/configuration/crashes/index-mappings.json` (**new file** — crashes currently has none): `{"properties": {"dot_number": {"type": "long"}}}`. This alone is expected to fix the existing deferred bug — no Python code changes needed, since Elasticsearch's numeric coercion (enabled by default) accepts the whole-number floats pandas produces for a nulls-containing numeric column (e.g. `4023446.0` is coerced to the long value `4023446`).
- `DOT-Commercial/configuration/inspections/index-mappings.json` (**new file**): explicit `long` mapping for `dot_number` and `inspection_id` — defensive and consistent, since `inspections` is now also an enrich match target for the new unit data.
- `DOT-Commercial/configuration/inspections-per-unit/index-mappings.json`: `inspection_id: long` (already listed above) closes the loop on the new join's own key too.

## Future direction (documented, not built): chaining to `carriers`

Reaching `carriers` with VIN/unit data would need a second enrich hop. The `carriers` enrich pipeline (`carriers-ingestion-setup/pipelines.json`) would gain a third processor matching on a field populated by the *existing* `inspections`-enrichment processor (which already writes an `inspections` field onto carrier documents) — Elasticsearch enrich processors within one pipeline execute in order and a later processor can match on a field an earlier one just populated. This is a real, supported capability, but was not confirmed as needed for this design and adds real pipeline-ordering complexity (the enrich policy's `match_field` would need to reference a nested value inside the already-enriched `inspections` array, which requires either a `foreach` processor or restructuring the match field, not just a straightforward field reference). Flagged in the backlog with this much detail so a future pass doesn't have to re-derive it.

## Edge cases

- **`change_date` windowing is an approximation** of `insp_date`-based windowing on the parent table — some units may be included/excluded slightly differently than their parent inspection's own window. Accepted, not treated as a bug.
- **VIN nulls** (~0.22% of unit rows) simply produce a null `insp_unit_vehicle_id_number` on the enriched document.
- **A unit whose parent `inspection_id` falls outside the (independently windowed) `inspections` fetch, or vice versa**: the enrich policy finds no match; the `units` field stays empty on that inspection document. Not an error — same behavior as existing crashes-enrichment-into-carriers when there's genuinely no matching crash.
- **Ordering**: `inspections-per-unit` must be fully populated before `inspections-ingestion-setup` runs, which must run before `inspections`'s own `index-populate`.

## Validation plan

1. Fetch `inspections_per_unit.csv` from the real API; confirm the `change_date` window is respected (spot-check the oldest date present).
2. Run the full new pipeline in order: `inspections-per-unit` (index-create, index-map, index-populate) → `inspections-ingestion-setup` (enrichment-policies, pipelines) → `inspections` index-populate.
3. Confirm via a real `_mapping` API call that `crashes.dot_number`, `inspections.dot_number`/`inspection_id`, and `inspections-per-unit.inspection_id` are all explicitly `long` — not dynamically inferred.
4. Confirm via real `_count`/`_search` that inspection documents now carry a real, non-empty `units` field with actual VIN data for a sample of real documents.
5. **Re-verify the crashes-enrichment fix specifically**: confirm `crashes-enrichment-into-carriers` (currently zero matches — the pre-existing deferred bug) now returns real matches once `crashes.dot_number` has an explicit `long` mapping, closing out that backlog item.
6. Confirm existing `carriers`/`crashes`/`inspections` ingestion still works unchanged (regression check) after these additions.

## Explicitly out of scope

- Chaining VIN/unit enrichment through to `carriers` documents (documented above as a future direction, not built).
- Any change to the carriers-ingestion-setup pipeline/policies.
- Fixing the separate, already-deferred ES enrich-coordinator queue-capacity issue (carriers ingestion converging to ~99.96%) — unrelated to this work.
- Incremental sync for the new dataset — it follows the same full-pull-per-run model as the rest of DOT-Commercial.
