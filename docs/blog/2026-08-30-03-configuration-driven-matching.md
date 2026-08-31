# Configuration-driven matching across datasets

## The problem is not fraud, it is identity

Two records, two source systems, no shared key. One says a business is at a
street address with a phone number; the other, filed by a different agency on a
different schedule, says something similar with a different spelling. Deciding
whether those describe the same real-world thing is entity resolution, and it
turns up in customer master data, supplier deduplication, provider directories,
sanctions screening, and fraud.

The fraud case is not a different machine. It is one **selector** over the same
machinery: instead of comparing every record to every other, you walk a
population of interest — businesses shut down by a regulator — and look for
records that appear afterward reusing the same infrastructure. Swap the
selector and the same signals, guards and exclusion logic answer "which of
these customer records are the same customer?"

That framing is the argument for building it as configuration rather than as a
script. If the domain lives in JSON, the next dataset is a config file.

_Every figure below is a point-in-time measurement against one extract of one
public dataset — evidence that a problem was real and roughly how big it was,
not a value to expect on your own data._

## A project is a directory, a step is a folder, a phase is a unit of work

The shape is deliberately boring. A **project** is a directory holding
configuration and data. Running it executes **steps** in order, and each step
runs one or more **phases**. There are six phases, and everything project-specific
about a dataset is supposed to live in the JSON beside them, not in the phases
themselves:

- `index-create` — create an index and its alias
- `index-map` — apply field types and analyzers
- `enrichment-policies` — build enrich indexes from other indexes
- `pipelines` — register ingest processors
- `index-populate` — bulk load a CSV, optionally through a pipeline
- `entity-match` — score pairs and write ranked candidates

Configuration lives on disk next to the step that consumes it:
`index-config.json`, `index-settings.json`, `index-mappings.json`,
`pipelines.json`, `entity-match.json`. One driver reads a project's
`configuration.json`, filters to the requested steps and phases, and dispatches.

Indexes are created date-stamped behind a stable alias — `carriers-2026.08.30-000001`
behind `carriers-000001` — so a reload builds a new index and swaps, which
matters because Elasticsearch cannot change an analyzer or a field mapping on a
live index. The development loop deletes and rebuilds constantly.

## Two datasets is the minimum that keeps a framework honest

The repository carries two reference projects, and the second one is what
turned the framework/project split from an intention into a fact.

| Project             | Rows  | What it exercises                                                       |
| ------------------- | ----- | ----------------------------------------------------------------------- |
| Healthcare provider | ~5.6M | Scale, composite document IDs, phonetic analyzers, no enrichment        |
| Commercial trucking | ~9.6M | Multi-level enrichment chains, ingest pipelines, and the matching sweep |

They stress different things, which is the point. One dataset lets every
domain assumption hide inside "the framework" without anyone noticing. Two
force a question every time something is written: does this belong to the
engine, or to this dataset? The trucking project needs a two-level enrichment
chain — per-unit vehicle records into inspections, inspections into carriers —
and the provider project needs none at all. Anything that only one of them
needs is a candidate for configuration rather than code.

## A signal registry, not a whitelist

A **signal** is one kind of evidence that two records are the same entity:
name similarity, a shared address, a shared phone, timing between a shutdown
and a re-registration. Config selects them by a `type` string, as shipped
today:

```json
{
  "type": "vin-overlap",
  "weight": 0.08,
  "conclusive": true,
  "max_shared_carriers": 5,
  "fields": [
    "crashes.vehicle_identification_number",
    "inspections.units.insp_unit_vehicle_id_number"
  ]
}
```

Signal classes register their own type names, and `build_signal` looks up the
string. Adding a signal means registering a class, not editing the code that
builds a signal list from config.

The retrieval side is the part that had to be fixed. `CandidateFinder` used to
carry a hardcoded whitelist of which signal types were allowed to seed a query,
plus a clause-builder per type — which meant candidate-generation code knew
about phone numbers and vehicle identifiers. Now the signals answer questions
about themselves: `seed_clauses()` for what to query, `token_subfields()` for
what analyzed tokens to fetch, `exact_evidence_fields()` for what to scan for
over-shared values. The retriever asks and assembles. It knows nothing about
what any of it means.

## Rename it once you notice what it is

One signal shipped as `vin-overlap` and was doing something more general than
its name. The logic is not vehicular: it is _these two records name the same
physical thing, and that name is unique worldwide._ A vehicle identifier is one
instance; a container number, an aircraft tail number, a serial number, or a
national provider identifier behaves identically.

So the class became `SharedTokenSignal`, registered under both `shared-token`
and the original `vin-overlap` so existing configuration keeps working. Only
the `fields` list is domain-specific, which is where the domain knowledge
belongs.

That rename is the whole test for whether something belongs in the engine.
If the logic survives having its domain noun removed, it is framework. If it
does not, it is configuration.

## The deliberate non-goal

Signals come from a fixed, code-backed menu. Config picks from that menu and
tunes it. It is explicitly **not** a query-DSL-in-JSON mini-language.

That ceiling is a choice, and it is the right one. A configuration language
that can express anything is a programming language with worse tooling: no type
checking, no tests, no debugger, and a JSON file that has quietly become the
most complex code in the repository. A closed menu means every signal is a
Python class with unit tests, and the config surface stays small enough to
review. The cost is real — a genuinely new kind of evidence needs a new class,
not a new config block — and it is worth paying.

## Measure before you configure

The most common configuration error here is treating a **filter** as a
**fingerprint**: a low-cardinality field that can only narrow a population,
used as if it could identify an entity. A profiler runs the measurements that
catch it before a line of config is written — distinct counts, blank rates,
detected date formats, columns mixing numeric and non-numeric values,
identifiers carrying leading zeros, and whether a candidate key is actually
unique.

The split it reports comes with a collision estimate rather than a bare count,
because an absolute distinct count means nothing without the row count beside
it. When a field's most common value covers more than about 1% of populated
rows, it prints the probability that two unrelated records share a value by
chance and says plainly that the field is a filter, not a fingerprint.

The measurement that justifies the whole tool: one field had been documented as
harder to fake than a business address, and was therefore assumed to be strong
evidence. Measured, it holds **89 distinct values across 1.43M rows**, so two
unrelated records share one about 7% of the time by chance. It shipped as a
rarity-weighted corroborator at weight 0.04 instead of the strong signal it
looked like.

The rule of thumb that falls out: if two random records share a value more than
roughly a percent of the time, that field cannot carry a match. Weight it by
rarity or use it only as a filter.

## Every failure here looks like success

The hazards worth writing down share one signature: **the run reports success
and the data is quietly wrong.** None of them raises an error.

- Dynamic type inference made a numeric-looking identifier column a `float`,
  and rows carrying non-numeric values dropped **36,788 of 5,647,567**
  documents. Elasticsearch rejects the whole document, not the field.
- An enrich policy is a point-in-time snapshot. One that could not be replaced
  stayed pinned to a validation sample across a full production run, cutting
  enrichment coverage from roughly **572K matches to about 4K**. The step
  completed in about a second and reported success.
- Bulk-load back-pressure dropped **91,439 of 2,085,534 documents (4.4%)** —
  and because every dataset uses a deterministic document ID, an index holding
  fewer documents than its CSV has rows is _normal_. The shortfall looks
  exactly like legitimate deduplication.
- That last one was tuned against the wrong queue for a long time. The setting
  being adjusted governed a different thread pool, which is why it reported
  zero rejections while documents were being discarded.

The countermeasure is not more care. It is asking the cluster what it actually
did: compare the enrich index count against its source, read the loader's own
"N of M documents failed" line, and refuse to start a sweep whose source index
is empty or missing a subfield the scoring depends on. A phase that verifies
its own output is the difference between a run that reports success and a run
you can believe.

## Where the abstraction leaks

The honest part. Loading generalizes today; matching does not.

The phase implementations, the config loading, the Elasticsearch client
handling and the driver are generic. The matching library is generic in
_structure_ and still carries the trucking dataset's vocabulary. Its document
wrapper hardcodes the entity key name. Its predecessor selector hardcodes
source-specific field paths, a literal status value, and a sort on that same
key — and its four notions of "shut down" are one agency's notions, not general
ones.

So pointing `entity-match` at the healthcare dataset today requires editing
framework code, which contradicts the premise the project is built on. It is
recorded as open work rather than described as finished, and the intended fix
has a shape: make the entity key name configurable, and move selector
definitions into project configuration — while keeping selectors a closed,
code-backed menu rather than reopening the query-DSL question that was
deliberately settled.

An article claiming a framework generalizes has to say where it does not, in
the same detail as the parts that work. This is that place.

## Pointing it at something else

For a reader with their own two datasets, the order of decisions matters more
than any single setting.

**Pick the population.** What are you sweeping — every record against every
other, or a subset with a reason to be suspicious? The subset is cheaper and
sharper, and choosing it is a domain decision no framework makes for you.

**Measure every candidate field before weighting it.** Run the cardinality and
collision numbers first. A field that looks like a strong identifier is exactly
where placeholders hide.

**Decide what may seed, separately from what it weighs.** These are different
questions, and conflating them is the most consequential mistake available.
A field can be worth scoring and too common to retrieve on, or rare enough to
retrieve on and too weak to weigh heavily.

**Find the junk before it finds you.** Run a terms aggregation on every field
you plan to treat as identity evidence and read the head of the distribution.

**Set guards that count independent evidence.** Several signals reading one
field are one source, not several.

## What generalizes, and how to tell early

Loading, mapping, enrichment and ingest generalize cleanly — they are about
Elasticsearch, and Elasticsearch does not know what your data means. Scoring
math generalizes: set overlap, rarity weighting, renormalization over evaluable
signals. What does not generalize is any notion of _which population is
interesting_ and _what counts as an identifier_, and those are exactly the
places domain knowledge should live.

The test is the rename. Strip the domain noun out of a component's name and see
whether the description still makes sense. `vin-overlap` did not survive it and
became `shared-token`. The predecessor selector does not survive it either, and
still has the old name.
