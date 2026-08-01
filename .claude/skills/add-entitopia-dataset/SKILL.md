---
name: add-entitopia-dataset
description: Onboard or evaluate a dataset for entitopia's non-deterministic entity matching — deciding whether a source can support fraud detection at all, choosing field types and document IDs, designing analyzers, wiring enrichment, and configuring matching signals that find entities which terminate and reappear under a new identifier. Use this whenever adding or assessing a dataset for entitopia, designing index mappings or analyzers for fuzzy matching, debugging why an enrichment policy or matching sweep returns nothing, or setting up detection for chameleon/shadow/phoenix entities. Also use when a load silently drops documents, a term query matches zero rows despite the value being visibly present, or a scoring signal never fires.
---

# Adding a dataset to entitopia

## What this project is for

Entitopia finds **the same real-world entity hiding behind different records**, when no field matches exactly.

The fraud pattern: an entity is shut down — licence revoked, ordered out of service, deregistered — and shortly afterward a _new_ entity appears reusing the old one's infrastructure. Same address, same phone, same equipment, a name that is a near-miss. The operator changes just enough to defeat exact-match lookups.

Detection rests on two legs, and **a dataset is only a primary source if it supports both**:

1. **Similarity** — names that sound alike or abbreviate, addresses that normalize to the same string, shared phones, emails, or physical assets.
2. **Lifecycle timing** — a termination on one record and a creation on another, close together. Without dates you can find duplicates but not _succession_, and succession is what separates fraud from a data-quality problem.

A dataset with identity fields but no dates still earns a place as a corroborating source. But if no dataset in the project carries lifecycle events, the project can only do duplicate detection — say so rather than shipping a matcher that cannot tell the difference.

## Where the knowledge lives

This repo documents its own hazards, and those documents are the source of truth — they sit next to the config they describe and get updated when it changes. **Read them rather than working from memory:**

| Document                                                                 | Covers                                                                                                                                                        |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `README.md` § Common data-loading hazards                                | The six failure modes, each with the incident behind it: dynamic typing, legacy dates, inert analyzers, validation caps, missing `id_field`, stale enrichment |
| `README.md` § Local Elasticsearch                                        | The verification commands — `_analyze`, `_simulate`, `_mapping`, enrich counts                                                                                |
| `DOT-Commercial/README.md` § Enriched field mappings                     | Why every enriched field is pinned, with the measured before/after                                                                                            |
| `DOT-Commercial/README.md` § Name and address analyzers                  | The two-encoder decision, suffix stopping, the street subfield split — with measurements                                                                      |
| `docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md` | Signal semantics, scoring, guards, and the Known Limitations that record what is still wrong                                                                  |
| Open work items in each README                                           | What is known-broken right now. Check before assuming a behaviour is correct.                                                                                 |

Your job is to apply that material to a new dataset, not to restate it.

## Workflow

### 1. Measure before configuring

```bash
.venv/bin/python scripts/profile_dataset.py <path.csv>
.venv/bin/python scripts/profile_dataset.py <path.csv> --key col_a --key col_b
```

Act on every WARNING it prints — each corresponds to a real incident and names its consequence. Then use its cardinality split, which decides what a field can be:

- **Fingerprint** (high cardinality) — can carry a match on its own.
- **Filter** (low cardinality) — can select a population or corroborate, never establish.

**This is the judgement most often got wrong, and the profiler exists because of it.** A field can look like a strong fingerprint and be worthless. One dataset's "legal process agent" was documented as harder to fake than a business address; measurement found **89 distinct values across 1.43M rows**, so two unrelated entities share one about 7% of the time by chance. It shipped as a rarity-weighted corroborator at weight 0.04 instead of the strong signal it appeared to be.

If two random records share a value more than roughly a percent of the time, that field cannot carry a match. Weight it by rarity or use it only as a filter.

### 2. Decide the shape

Work through these, consulting the documents above for the specifics:

- **Field types** — pin everything you will query, join on, or score. Identifiers and codes are `keyword` even when numeric.
- **Dates** — decide per field: ISO-mapped with a conversion script, or `keyword` and parsed client-side. Legacy formats and century pivots are covered in the hazards section.
- **Document ID** — a natural key, or a composite. The profiler tests uniqueness and distinguishes real collisions (key is wrong) from byte-identical rows (composite correctly collapses them).
- **Analyzers** — only if this dataset carries identity fields to match on.
- **Enrichment** — only if it decorates another index.
- **Signals** — which fields become scored evidence, and at what weight.

### 3. Verify against a live cluster

Configuration that parses can be entirely inert. The commands are in the top-level README; the discipline is: **after every change, ask the cluster what it actually did** rather than trusting that the config was accepted.

The check that matters most is not "does this produce tokens" or "did the load report success" — it is **"do the specific records I expect to match actually match."** Build a small synthetic pair that should trigger the signal you just configured, run it end to end, and confirm that signal appears in the output with the contribution you expect.

## Judgement calls the documents do not make for you

These are decisions, not lookups. They come up on every dataset.

**A signal that cannot be evaluated must return "unknown", not "zero".** Missing data means the signal drops out and remaining weights renormalize. Scoring it as zero similarity penalizes an entity for sparse records, which is backwards — and a dataset with 23% blanks will produce mostly-unknown signals, correctly. Blank must never match blank.

**Do not weight several signals that read the same underlying field.** Measured consequence: a pair with a byte-identical address and a 45-day succession gap scored _below_ threshold and was dropped, while two unrelated entities sharing one common word scored above it. Name arms reading the same two fields also defeat a `min_signals` guard written to require corroboration.

**Seed candidate retrieval on discriminating fields only.** Seeding on a value shared by 9% of the corpus returns essentially random candidates and buries the real ones.

**Check whether a new dataset can join at all.** Every dataset currently in the project links via a shared identifier. One that has no such key is not a drop-in enrichment source — it needs either a fuzzy pre-join or a parallel sweep whose output corroborates the main one. That distinction changes the size of the work substantially, so resolve it before estimating.

**Flag inconsistencies you notice in passing, even outside your task.** The recurring failure here is a mismatch nobody looked for — a join field typed differently on two sides, an analyzer naming a column that was renamed, an enrichment carrying a payload that gets discarded at the last hop. If something looks off, say so; several real bugs were found exactly that way.

**When a measurement contradicts a document, trust the measurement and update the document.** That is how this material got written.

## Working in this repo

- Everything runs from `.venv/bin/python`, never the system Python.
- `ruff check .` must pass; JSON and Markdown are prettier-formatted.
- Mappings and analyzers are immutable on a live index — edit config, delete the index, re-run `index-create`/`index-map`, reload. Deleting is expected.
- Record what you measured in the project's README: cardinality numbers, the key choice and its evidence, and any signal you rejected and why. The rejected signals are the expensive part to rediscover.
- Conventions are in `CLAUDE.md`.
