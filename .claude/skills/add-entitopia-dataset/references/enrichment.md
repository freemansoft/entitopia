# Enrichment

Enrichment denormalizes related datasets onto the entity document so matching needs no joins at query time. It is the right tool here, and it has three sharp edges that all fail quietly.

## The mental model that prevents most mistakes

**An enrich policy is a point-in-time snapshot, not a live view.** Executing a policy copies the source index into a hidden `.enrich-*` index. Later changes to the source do not propagate. Every enrichment problem in this project traces back to a snapshot being older, smaller, or emptier than assumed.

## Edge 1: a policy bound to a live pipeline cannot be rebuilt

Deleting a policy fails with a conflict while any pipeline references it. `phase_enrichment_policies.py` catches that conflict and **logs a warning**, so the run continues — against the _old_ snapshot.

The consequence, observed in production: a policy stayed pinned to a **5,000-row validation sample** through a full **5.6M-row** load. Enrichment coverage silently fell from ~572K matches to ~4K. Every phase logged success.

**Always delete the pipeline before rebuilding its policies:**

```bash
curl -s -XDELETE "http://localhost:9200/_ingest/pipeline/<pipeline-name>"
.venv/bin/python execute_project.py --project=<project> --step=<setup-step>
```

Watch the log for `Failed to delete enrichment policy due to conflict`. If it appears, stop — the policy is stale and everything downstream is wrong.

**Verify the snapshot rather than trusting `acknowledged: true`:**

```bash
curl -s "http://localhost:9200/.enrich-<policy-name>*/_count"
```

A count far below the source index count means the snapshot is stale or the source was truncated.

## Edge 2: a missing source index aborts the whole rebuild loop

`execute_policy` sits in a `try` that catches only `BadRequestError`, while the `delete_policy` above it catches `NotFoundError` too. So an absent dated source index throws past the handler and **every policy after it in the list is never rebuilt** — each silently keeping its previous snapshot.

This triggers when a run crosses midnight and earlier steps created indexes under yesterday's date. It fails loudly with a traceback rather than silently, but an unattended overnight run can still finish with several policies quietly out of date. Tracked in the repo's open work items.

## Edge 3: `max_matches` is capped at 128, so truncation is structural

The enrich processor's `max_matches` bounds how many source documents attach to one target document. Elasticsearch caps it at **128** — "in order to avoid documents getting too large" — so it is not a lever you can turn far enough to avoid truncation on a genuinely one-to-many relationship.

Measured on one dataset: at `max_matches: 100`, **1,563,914 of 5,638,961** inspection rows (27.7%) never reach a carrier document. The worst-affected entity has 21,947 inspections and keeps 100.

**Decide whether that truncation matters for your signal, and record the answer.** In the reference project it does not, and the reasoning is worth copying:

```text
distinct VINs per entity:  p50=2  p90=12  p95=23  p99=95
99.06% of entities have <= 100
```

The loss is concentrated in ~5,300 megafleets — which do not shut down and reappear under a new identifier. Truncation lands precisely on the population the analysis does not care about. That is a _measured_ justification, not an assumption; make the equivalent measurement for your dataset rather than inheriting the conclusion.

If truncation would fall on the population you _do_ care about, denormalizing is the wrong shape. Query the relationship at match time instead.

## Multi-level chains

A dataset can be enriched onto an intermediate index that is itself enriched onto the entity index. One reference project does this for per-unit vehicle data:

```text
inspections-per-unit  --(inspection_id)-->  inspections  --(dot_number)-->  carriers
```

This is sometimes forced rather than chosen. Here, `inspections-per-unit` has no entity key at all — only `inspection_id` — so `inspections` is the only place the entity key and the unit key coexist. It is a genuine join table and cannot be skipped.

**Elasticsearch has no primitive for joining two source indexes to each other.** An enrich processor decorates a document flowing through a pipeline; it cannot merge two tables. So a chain is the minimal expression of that schema, not accidental complexity.

Two things to get right in a chain:

1. **Carry the payload all the way.** The most common failure is building the chain and then discarding its output at the last hop. In the reference project the final policy carried only `dot_number` and `inspection_id`, so the VIN data assembled at level one never reached the entity index — the signal reading it was dead while looking configured. Add the nested path explicitly to the final policy's `enrich_fields`.

2. **Pin the nested path's mapping on the target.** `inspections.units.insp_unit_vehicle_id_number` must be mapped `keyword` on the entity index. Enriched fields take the _target_ index's mapping, so leaving it dynamic reproduces the `text`-instead-of-`keyword` failure in `mapping-hazards.md`.

Carry only the subfields a signal actually reads. Each entity holds up to `max_matches` intermediate documents, each holding up to its own `max_matches` — pulling unused fields multiplies document size for nothing.

## Reading enriched data in code

Enriched fields arrive as **arrays** whenever `max_matches > 1`, and a chain produces **arrays of arrays**. A path walker must flatten at each step, or `inspections.units.vin` yields a list of lists at the middle step, finds no objects at the final step, and silently returns nothing.

Filter values also do not behave the way they read. An object array is not `nested`, so sibling filter clauses can be satisfied by **different elements**: an entity with an ACTIVE 2015 order and an INACTIVE 2022 order matches `status: ACTIVE AND date >= 2020` even though no single order matches both. Map the object as `nested` and use a `nested` query if that distinction matters. The reference project accepts the over-selection deliberately — it preserves recall and a cap bounds the cost — but records it.

## Checklist

- Delete referencing pipelines before rebuilding policies.
- Watch for the conflict warning; treat it as a failure.
- Check `.enrich-*` counts against source counts after every rebuild.
- Confirm `num_rows: null` on the source — a truncated source silently truncates the snapshot.
- Measure whether `max_matches` truncation falls on a population you care about.
- Carry the full path through every hop of a chain.
- Map every enriched field explicitly on the target index.
