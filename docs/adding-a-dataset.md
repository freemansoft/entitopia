# Adding a dataset to entitopia

Guidance for onboarding or evaluating a new data source. It complements the reference material rather than repeating it: the [top-level README](../README.md) documents the hazards and the verification commands, and each project's README documents its own datasets. This covers the framing and the judgement calls that sit between them.

## What this project is for

Entitopia finds **the same real-world entity hiding behind different records**, when no field matches exactly.

The fraud pattern: an entity is shut down — licence revoked, ordered out of service, deregistered — and shortly afterward a _new_ entity appears reusing the old one's infrastructure. Same address, same phone, same equipment, a name that is a near-miss. The operator changes just enough to defeat exact-match lookups.

Detection rests on two legs, and **a dataset is only a primary source if it supports both**:

1. **Similarity** — names that sound alike or abbreviate, addresses that normalize to the same string, shared phones, emails, or physical assets.
2. **Lifecycle timing** — a termination on one record and a creation on another, close together. Without dates you can find duplicates but not _succession_, and succession is what separates fraud from a data-quality problem.

A dataset with identity fields but no dates still earns a place as a corroborating source. But if no dataset in the project carries lifecycle events, the project can only do duplicate detection — say so rather than shipping a matcher that cannot tell the difference.

## Where the details live

These documents sit next to the config they describe and get updated when it changes, so they are the source of truth:

| Document                                                                                       | Covers                                                                                                                                                        |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [README](../README.md) § Common data-loading hazards                                           | The seven failure modes, each with the incident behind it: dynamic typing, legacy dates, inert analyzers, validation caps, missing `id_field`, stale enrichment, dropped documents under enrich back-pressure |
| [README](../README.md) § Local Elasticsearch                                                   | The verification commands — `_analyze`, `_simulate`, `_mapping`, enrich counts                                                                                |
| [README](../README.md) § Inside `entity-match`                                                 | How retrieval and scoring divide the work, and the `seed_signals` / `conclusive` / `ignore_values` knobs                                                       |
| [DOT-Commercial](../DOT-Commercial/README.md) § Enriched field mappings                        | Why every enriched field is pinned, with the measured before/after                                                                                            |
| [DOT-Commercial](../DOT-Commercial/README.md) § Name and address analyzers                     | The two-encoder decision, suffix stopping, the street subfield split — with measurements                                                                      |
| [Chameleon matching design](superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md) | Signal semantics, scoring, guards, and the Known Limitations that record what is still wrong                                                                  |
| Open work items in each README                                                                 | What is known-broken right now. Check before assuming a behaviour is correct.                                                                                 |

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
- **Seeding** — which of those signals may _retrieve_ candidates (`candidates.seed_signals`), not merely score them. This is a separate decision from weight and it sets the ceiling on recall: a pair the seed query never returns cannot be scored at any threshold.
- **Junk values** — `ignore_values`, keyed by field path, for placeholders that would otherwise read as evidence.

### 3. Verify against a live cluster

Configuration that parses can be entirely inert. The commands are in the top-level README; the discipline is: **after every change, ask the cluster what it actually did** rather than trusting that the config was accepted.

The check that matters most is not "does this produce tokens" or "did the load report success" — it is **"do the specific records I expect to match actually match."** Build a small synthetic pair that should trigger the signal you just configured, run it end to end, and confirm that signal appears in the output with the contribution you expect.

## Judgement calls the reference docs do not make for you

These are decisions, not lookups. They come up on every dataset.

**A signal that cannot be evaluated must return "unknown", not "zero".** Missing data means the signal drops out and remaining weights renormalize. Scoring it as zero similarity penalizes an entity for sparse records, which is backwards — and a dataset with 23% blanks will produce mostly-unknown signals, correctly. Blank must never match blank.

**Do not weight several signals that read the same underlying field.** Measured consequence: a pair with a byte-identical address and a 45-day succession gap scored _below_ threshold and was dropped, while two unrelated entities sharing one common word scored above it. The `min_signals` half of this is now handled structurally — `PairScorer` counts distinct evidence sources rather than signal instances, so several arms over one field collapse to one — but the weighting itself is still a judgement call.

**Seed candidate retrieval on discriminating fields only.** Seeding on a value shared by 9% of the corpus returns essentially random candidates and buries the real ones.

**But a field too weak to seed on is not the same as a field that should not seed.** These are opposite failures and both are real:

- Seeding on a _common_ value floods the candidate set with noise. That is the rule above.
- **Declining to seed on a rare, decisive one silently caps recall at zero for an entire fraud profile.** A shared vehicle identifier was scored but not seeded, so an entity that changed its name, address and phone while keeping its equipment could never be retrieved — no threshold, weight, or `max_candidates` value would surface it, because the query never returned the record at all. Raising `max_candidates` from 100 to 500 across a full sweep cost 4× the runtime and produced **zero** new top-tier findings, because relevance ranking already had the reachable ones near the top. The unreachable ones stayed unreachable.

The test is not "is this field rare enough to rank well" but **"if two records shared only this field, would I want to see the pair?"** If yes, it must seed, regardless of what else it does.

**A unique identifier is only unique until you measure it.** Fields that look like globally-unique keys are exactly where placeholders hide, because the systems producing them require a value and the humans filling them in do not always have one. Measured in FMCSA crash reports: the literal VIN `GGGG` on 158 carriers, `UNKNOWN` on 79, `99999999999999999` on 51. A binary "shared identifier" signal scores 1.0 on those, so every pair that both filed a placeholder reads as a perfect identity match, and seeding on one retrieves all 158 at once. **Before trusting any identifier field, run a `terms` aggregation on it and look at the head of the distribution.** Then handle what you find in both places — `ignore_values` for what you can name, and a frequency threshold for what you cannot.

**Averaging cannot express "this one fact settles it".** A weighted mean dilutes a decisive signal in proportion to how many other signals exist. A shared vehicle identifier at weight 0.08 contributes ~0.11 to a pair sharing nothing else, well under a 0.35 floor, so the strongest available evidence was discarded while weaker corroborated pairs passed. Raising the weight enough to clear the floor unaided (~0.46 here) makes that signal dominate every _other_ pair it touches, which is a worse outcome. Mark the signal `conclusive` instead: it is a guard, not a weight, so it changes only the pairs where that evidence stands alone.

**Check whether a new dataset can join at all.** Every dataset currently in the project links via a shared identifier. One that has no such key is not a drop-in enrichment source — it needs either a fuzzy pre-join or a parallel sweep whose output corroborates the main one. That distinction changes the size of the work substantially, so resolve it before estimating.

**Flag inconsistencies you notice in passing, even outside your task.** The recurring failure here is a mismatch nobody looked for — a join field typed differently on two sides, an analyzer naming a column that was renamed, an enrichment carrying a payload that gets discarded at the last hop. Several real bugs were found exactly that way, including the `dot_number` type boundary now recorded in the open work items, which surfaced while assessing an unrelated candidate dataset.

**When a measurement contradicts a document, trust the measurement and update the document.** That is how this material got written.

## Working in this repo

- Everything runs from `.venv/bin/python`, never the system Python.
- `ruff check .` must pass; JSON and Markdown are prettier-formatted.
- Mappings and analyzers are immutable on a live index — edit config, delete the index, re-run `index-create`/`index-map`, reload. Deleting is expected.
- Record what you measured in the project's README: cardinality numbers, the key choice and its evidence, and any signal you rejected and why. The rejected signals are the expensive part to rediscover.
- Conventions are in [CLAUDE.md](../CLAUDE.md).
