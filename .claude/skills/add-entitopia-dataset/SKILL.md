---
name: add-entitopia-dataset
description: Onboard a new dataset into entitopia for non-deterministic entity matching and fraud detection — surveying a candidate source, choosing field types and document IDs, designing analyzers, wiring enrichment policies, and configuring matching signals that find probable-same entities via name/address similarity plus creation-and-termination timing. Use this whenever adding or evaluating a dataset for entitopia, designing index mappings or analyzers for fuzzy matching, debugging why an enrichment policy or matching sweep returns nothing, or setting up detection for entities that shut down and reappear under a new identifier (chameleon/shadow/phoenix entities). Also use when a load silently drops documents, a term query matches zero rows, or a scoring signal never fires.
---

# Adding a dataset to entitopia

## What this project is for

Entitopia finds **the same real-world entity hiding behind different records**, when no field matches exactly.

The fraud pattern it targets: an entity is shut down — licence revoked, ordered out of service, deregistered — and shortly afterward a _new_ entity appears reusing the old one's infrastructure. Same address, same phone, same equipment, a name that is a near-miss of the old one. The operator changes just enough to defeat exact-match lookups.

Detection therefore rests on two legs, and a dataset is only useful if it supports both:

1. **Similarity** — names that sound alike or abbreviate, addresses that normalize to the same string, shared phones, emails, or physical assets.
2. **Lifecycle timing** — a termination event on one record and a creation event on another, close together in time. Without dates you can find duplicates but not _succession_, and succession is what distinguishes fraud from a data-quality problem.

Everything below exists to get a dataset into a shape where those two signals are actually computable and actually correct.

## The failure mode that governs every decision here

Nearly every bug this project has hit shares one shape: **the run reports success and the data is quietly wrong.** Not a crash — a clean log, an `acknowledged: true`, and an empty or misleading result.

Real examples, all from production data:

- A field's type was inferred from the first document seen, and 36,788 of 5,647,567 rows then failed to index. The run logged failures it counted but did not raise on.
- An enrichment policy matched **zero** documents because a joined field was `float` on one side and `keyword` on the other.
- All four entity selectors returned nothing, because a `terms` query for `"ACTIVE"` cannot match the analyzed token `"active"`.
- Two records at an identical address produced **zero candidates** because one had a comma in it.
- A rarity weighting was mathematically inverted, quietly neutering its own signal.

None of these raise. You find them by **measuring the data before you configure, and verifying against a live cluster after.** That discipline is the actual content of this skill; the specific recipes are downstream of it.

## Workflow

Work in this order. Later steps depend on measurements taken in earlier ones.

### 1. Survey the dataset before writing any config

Ask whether the source can support the two legs of detection at all:

- **Identity fields** — names, addresses, phones, emails. Anything an operator would have to change to hide, but is inconvenient to change.
- **Lifecycle dates** — creation/registration and termination/revocation events. Ideally on separate records so a _succession gap_ can be computed.
- **Shared infrastructure** — equipment identifiers, agents, licence numbers. These are the strongest signals when they exist, because they are expensive to change.
- **A join key** — the identifier that links this dataset to the entity index.

Then **measure, do not assume**. Run the bundled profiler:

```bash
.venv/bin/python .claude/skills/add-entitopia-dataset/scripts/profile_dataset.py <path.csv>
```

It reports per-column cardinality, null rates, detected date formats, leading-zero risk, mixed-type columns that will break dynamic mapping, and candidate unique keys. Read `references/dataset-survey.md` for how to interpret the output and decide whether a signal is worth carrying.

**Cardinality is the thing people get wrong.** A field can look like a strong fingerprint and be worthless. One dataset's "legal process agent" seemed like a hard-to-fake link between entities; measurement showed **89 distinct values across 1.43M rows**, meaning two unrelated entities share one about 7% of the time by chance. It survived as a 0.04-weight corroborating signal instead of the strong signal it appeared to be. Measure the distinct count and the top-value share before you weight anything.

### 2. Choose field types deliberately

The default — letting Elasticsearch infer — is how three separate incidents above happened. Pin every field you will query, join on, or score.

The short version: **identifiers and codes are `keyword` even when they look numeric.** Dates get special handling (below). Anything an enrichment policy writes onto a target document must be mapped explicitly on the _target_, because the enriched value takes the target index's mapping, not the source's.

Read `references/mapping-hazards.md` before writing `index-mappings.json`. It catalogues each failure with its symptom, so you can recognize one you are currently looking at.

### 3. Handle dates as a hazard, not a field

Legacy sources carry non-ISO dates, and the obvious fix is often worse than the bug. A two-digit year mapped with Java's `yy` pattern pivots to 2000–2099, turning a 1974 registration into 2074 — silently, and in the exact field your timing signal depends on.

The pattern that works: map the field ISO-only, convert in an ingest script with an explicit century pivot, **validate the result is a real calendar date by constructing it** rather than pattern-matching digits, and attach a failure handler that drops the _field_ rather than the _document_.

`references/mapping-hazards.md` has the full recipe and the reasoning for each part.

### 4. Design analyzers for the similarity signals

Name and address matching is where most of the accuracy lives. The decisions that matter — two complementary phonetic encoders rather than one, stripping corporate suffixes before encoding, and why streets need both a keyword and a token subfield — are in `references/analyzers.md`, with measured evidence for each.

The headline: **use two phonetic encoders, weighted separately.** `double_metaphone` and `beider_morse` catch different classes of evasion, and dropping either loses one. That file shows the measurements.

### 5. Give every dataset a deterministic document ID

Without `id_field`, re-running a load appends instead of overwriting, so every rerun duplicates the dataset. Use a natural key when one exists, or a JSON list of columns joined into a composite.

**Verify uniqueness against the full dataset**, not a sample — the profiler does this. Prefer the _minimal_ key that is actually unique; a larger key works but hides which columns carry the identity. Where a source contains genuine full-row duplicates, a composite key correctly collapses them, which is usually what you want.

### 6. Wire enrichment, if this dataset decorates another

Enrichment denormalizes related datasets onto the entity document so matching needs no joins. It has sharp edges — snapshot semantics, a hard cap on matches per document, and an ordering requirement when rebuilding. Read `references/enrichment.md` before configuring a policy.

The two that bite hardest: an enrich policy is a **point-in-time snapshot** that silently keeps serving stale data if its rebuild fails, and `max_matches` is **capped at 128 by Elasticsearch**, so it is not a lever you can turn far enough to avoid truncation.

### 7. Configure the matching signals

`entity-match` scores candidate pairs across a fixed menu of signal types with configured weights. Read `references/matching-signals.md` for the menu, what each signal reads, how weights and guards interact, and the calibration traps.

Two principles carry most of the value:

- **A signal that cannot be evaluated returns "unknown", not "zero".** Unknown signals drop out and the remaining weights renormalize. Scoring a missing record as zero similarity penalizes an entity for having sparse data, which is backwards.
- **Weighting several signals that read the same underlying field triple-counts it.** Measured consequence: a pair with a byte-identical address and a 45-day succession gap scored _below_ threshold and was dropped, while two unrelated entities sharing a single common word scored above it and were emitted.

### 8. Verify against a live cluster — always

Configuration that parses can still be entirely inert. Verification is not optional polish; it is how you find out whether any of the above actually took effect.

`references/verification.md` has the specific checks in the order worth running them. The habit to internalize: **after every mapping, analyzer, pipeline, or policy change, ask the cluster what it actually did** rather than trusting that the config was accepted.

## Working in this repo

- Everything runs from `.venv/bin/python`, never the system Python.
- `ruff check .` must pass; JSON and Markdown are prettier-formatted.
- Mappings and analyzers are immutable on a live index — the normal loop is edit config, delete the index, re-run `index-create`/`index-map`, reload. Deleting is expected, not a failure.
- Project-specific data acquisition lives in the project directory. Framework code stays generic. See the repo's `CLAUDE.md` and top-level `README.md`.

## Reference files

Read these when you reach the step that needs them, not upfront:

| File                             | Read it when                                                                       |
| -------------------------------- | ---------------------------------------------------------------------------------- |
| `references/dataset-survey.md`   | Deciding whether a dataset is worth adding, and which fields become signals        |
| `references/mapping-hazards.md`  | Writing `index-mappings.json`, or debugging dropped documents / zero-match queries |
| `references/analyzers.md`        | Configuring name, address, or phone analysis for similarity matching               |
| `references/enrichment.md`       | Denormalizing one dataset onto another, or debugging empty enrichment              |
| `references/matching-signals.md` | Configuring `entity-match`, choosing weights, or calibrating thresholds            |
| `references/verification.md`     | After any config change — the checks that catch silent wrongness                   |

`scripts/profile_dataset.py` measures a CSV before you configure it. Run it first.

## A note on judgement

The recipes here are compressed from real incidents, but they are not a substitute for looking at the data. Every dataset breaks a rule somewhere. When a measurement contradicts something in these files, trust the measurement and update the file — that is how this material got written in the first place.
