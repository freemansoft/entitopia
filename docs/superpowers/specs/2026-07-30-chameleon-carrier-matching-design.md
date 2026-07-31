# Chameleon Carrier Matching — Design

**Status: design complete, awaiting implementation plan.** All five sections
reviewed and approved 2026-07-30. No code has been written.

## Purpose

Give entitopia a matching/query layer so the phonetic and ICU analyzers it
configures are actually used, and demonstrate the project's stated purpose:
detecting **chameleon carriers** — commercial carriers shut down for safety,
insurance, or authority reasons that reappear under a new DOT number while
reusing the same addresses, phones, process agents, trucks, and near-identical
names.

## The question that started this

> Are there any AI, ML, vector, or other search features of OpenSearch that
> would replace or do a better job than the `analysis-icu` and
> `analysis-phonetic` Elasticsearch extensions?

**Answer: no, and the premise needs correcting in three ways.** This was
researched before any design work, and the findings shaped everything below.

### 1. Vectors are the wrong tool for this problem

Dense embeddings model _meaning_. Chameleon detection is an _orthographic and
phonetic_ problem. `SMYTH TRUCKNG INC` vs `SMITH TRUCKING LLC` is a
spelling-and-sound match that phonetic encoding is built for and that semantic
embeddings handle incidentally at best.

Worse, embeddings actively introduce false positives here: `ABC FREIGHT` and
`XYZ FREIGHT` are near-identical in embedding space and are unrelated
companies. Even OpenSearch's own hybrid-search guidance concedes BM25
outperforms neural retrieval on named-entity precision.

### 2. What OpenSearch actually adds is scoring, not analysis

| Feature                                              | Relevance here                                                                                            |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `normalization-processor` / `score-ranker-processor` | Real, but it is score fusion (Apache 2.0; Elastic's RRF is paid-tier) — not a replacement for an analyzer |
| Anomaly detection (RCF)                              | Relevant to the _temporal_ reincarnation signal, not to name matching                                     |
| Learning to Rank                                     | Only useful once labeled chameleon pairs exist                                                            |
| k-NN / neural / neural-sparse / semantic field       | Wrong tool, per above                                                                                     |

None of these replace `analysis-phonetic`. All of them presuppose a scoring
layer that entitopia does not yet have.

### 3. Correction to the abandoned OpenSearch migration spec

`2026-07-29-elasticsearch-to-opensearch-migration-design.md` states that AWS
managed OpenSearch is disqualifying because it cannot install
`analysis-phonetic`. **That is wrong.** Amazon OpenSearch Service ships
**Phonetic Analysis** (min OpenSearch 1.0) and **ICU Analysis** ("included on
all domains") as prepackaged plugins.
See <https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-plugins.html>.
That spec has been corrected.

### The finding that actually mattered

**Entitopia has no query layer at all.** `phase_providers/` implements
`index-map`, `enrichment-policies`, `pipelines`, and `index-populate`. Nothing
searches, scores, or emits matches. The phonetic and ICU analyzers are
configured on every name field and never queried.

No claim that model X beats `metaphone` is measurable until a baseline exists.
So the work is to build the matching layer — and, along the way, to upgrade the
encoder, because that is where the real accuracy gain is.

## Decisions taken

1. **Build the matching/query layer.** Not an analyzer bake-off, not an
   OpenSearch migration, not a research memo.
2. **Batch sweep over shut-down carriers**, not interactive lookup. Iterate
   carriers with an out-of-service order or revoked authority, find candidate
   successors, score, and write ranked suspect pairs.
3. **Config-driven signals over a fixed menu of signal types.** A generic
   `entity-match` phase. Config names which signals to use with weights and
   thresholds; each signal type is implemented in Python. Explicitly _not_ a
   raw-query-DSL-in-config mini-language.
4. **All four signal families:** name similarity, address/phone/email, BOC-3
   process agent, and temporal proximity + VIN overlap. Address matching and
   BOC-3 agent-name matching must be **fuzzy, not just exact**.
5. **Config-driven sweep scope with a small default**, so the example stays
   runnable. The full sweep is a config change.
6. **Output to a new Elasticsearch index**, reusing the existing index-map and
   alias machinery.
7. **Replace `metaphone` outright** (rather than keeping it alongside) and add
   a second Beider-Morse subfield.

## Data facts established during design

Measured against the local `DOT-Commercial/data` extracts, not assumed:

- `carriers.csv` — 2,085,535 rows.
- `out_of_service_orders.csv` — 395,124 rows; **340,352 distinct
  `dot_number`s**; status splits `INACTIVE` 302,253 / `ACTIVE` 92,870. This is
  the predecessor population, and therefore the sweep size.
- `boc3_agents.csv` — 1,860,604 rows, `co_name` populated on 1,426,508
  (**76.7%**). Usable, but blank-vs-blank must never score as a match.
- The `carriers` index is **already fully denormalized**. The five enrich
  policies land `inspections`, `crashes`, `auth_history`,
  `out_of_service_orders`, and `boc3_agents` onto each carrier document, so the
  sweep needs no joins.

### Gaps and defects found

These were discovered while grounding the design and are fixed by it:

- **Inspection VINs are unreachable from `carriers`.**
  `inspections-enrichment-policy` carries only `dot_number` and `inspection_id`.
  The VINs live in `inspections-per-unit.insp_unit_vehicle_id_number`, are
  enriched onto `inspections`, and stop there. Crash VINs
  (`crashes.vehicle_identification_number`) _are_ available, but crashes are
  rare (333K) next to inspections (5.6M). Reaching the volume VIN signal is one
  `enrich_fields` edit.
- **`street_suffix_map` is dead config, and broken.** Defined in
  `carriers/index-settings.json`, referenced by no analyzer. Its pattern is
  `(st)` → `street`, unanchored, so it would map `stone` → `streetone` and
  `street` → `streetreet` if ever wired up. Same category as the dead
  fingerprint processor removed in `719b350`.
- **`street_clean` cannot do fuzzy matching.** It uses `"tokenizer": "keyword"`,
  collapsing the whole street to a single token. `123 MAIN ST` and
  `123 MAIN STREET STE 4` are unrelated strings to it. Fuzzy address matching
  needs a second, token-based subfield.
- **`add_date` is silently indexed as text.** Its format is `01-JUN-74` —
  Oracle-style with a two-digit year — which Elasticsearch's dynamic date
  detection does not parse. Mapping it naively as `dd-MMM-yy` is worse than
  leaving it: Java's `yy` pattern pivots to 2000–2099, so a 1974 registration
  becomes **2074**. The entire temporal signal rests on this field.
- **`boc3_agents.co_name` is dynamically mapped** as plain text with no
  phonetic or cleaned subfield, so fuzzy agent-name matching is impossible
  without an explicit mapping.

**Not a defect:** `phy_zip` was initially suspected of losing leading zeros.
Verified false — `pd.read_csv` infers it as `object` (some values are ZIP+4),
so it reaches Elasticsearch as a string and maps as text/keyword. It is pinned
below for consistency, not as a fix.

## Section 1 — Analyzer and mapping changes

All changes are in `DOT-Commercial/configuration/carriers/`.

### Phonetic filters

`metaphone` is replaced outright; Beider-Morse is added alongside.

```json
"phonetic_dm": {
  "type": "phonetic",
  "encoder": "double_metaphone",
  "max_code_len": 6
},
"phonetic_bm": {
  "type": "phonetic",
  "encoder": "beider_morse",
  "rule_type": "approx",
  "name_type": "generic",
  "languageset": ["english", "spanish"]
}
```

`double_metaphone` replaces `metaphone` because it emits a primary _and_ an
alternate encoding, so `Schmidt` / `Schmitt` / `Smith` variants collide
correctly. `max_code_len` is raised from its default of 4 to 6; four characters
over-collide on company-name tokens.

`beider_morse` is pinned to `["english", "spanish"]` rather than left to guess.
Language guessing on short corporate tokens is unstable and would make output
non-reproducible between runs. The `replace` setting is not supported by this
encoder — it always adds tokens and emits several per input, so this will be
the largest subfield in the index.

### Corporate-suffix stop filter

Applied **only** in the phonetic analyzers, never in `.clean`.

Nearly every carrier name ends in `LLC`, `INC`, `TRUCKING`, `TRANSPORT`,
`EXPRESS`, or `LOGISTICS`. Left in place, phonetic overlap is dominated by
noise every carrier shares. Because scoring happens in Python (Section 3),
there is no BM25 IDF to discount them automatically.

Edge case that must be handled: a carrier named literally `TRUCKING LLC`
reduces to zero tokens. The scorer must treat an empty phonetic token stream as
**"no signal"**, never as a match.

### Street analyzers

Delete `street_suffix_map`. Replace it with a `synonym` filter operating on
terms rather than unanchored regex:

```
st → street, ave → avenue, rd → road, blvd → boulevard, ste → suite
```

Keep `street_clean` (keyword tokenizer) for exact-after-normalization, and add
`street_tokens` (standard tokenizer + synonyms) for fuzzy matching.

### Mapping additions

| Field                          | Change                                                               |
| ------------------------------ | -------------------------------------------------------------------- |
| `legal_name`, `dba_name`       | add `.phonetic` (double_metaphone) and `.phonetic_bm` (beider_morse) |
| `phy_street`, `mailing_street` | add `.tokens` (street_tokens) alongside existing `.clean`            |
| `boc3_agents.co_name`          | explicitly map with `.keyword`, `.clean`, `.phonetic`                |
| `boc3_agents.street_po`        | explicitly map with `.clean`, `.tokens`                              |
| `add_date`                     | pin as `date` + pipeline century fix (below)                         |
| `phy_zip`, `fax`               | pin as `keyword` / `text` + `phone_clean`                            |

### The `add_date` century fix

A new Painless `script` processor on the carriers pipeline parses `dd-MMM-yy`
and maps year > 30 to `19xx`, else `20xx`. Precedent exists:
`crashes-pipeline-000001` already performs exactly this class of Painless
coercion for `dot_number`, and the README documents why a `convert` processor
fails silently in that case.

### Cost

Adding subfields requires rebuilding the `carriers` index — a full ~2M-document
reload through the enrichment pipeline. That is the expensive part of this
section; the config edits themselves are trivial.

## Section 2 — The `entity-match` phase and config schema

Approved 2026-07-30.

A new step in `DOT-Commercial/configuration.json`:

```json
{ "name": "chameleon-detection", "phases": ["index-create", "index-map", "entity-match"] }
```

The output index is built by the **existing** `index-create` and `index-map`
phases, so the new phase only performs matching. `entity-match` is added to
`all_phases` and gets one branch in `phase_dispatcher.py` alongside the existing
five. New file `phase_providers/phase_entity_match.py` reads
`configuration/chameleon-detection/entity-match.json`.

### Schema

Config _selects and weights_. It never expresses query logic.

```json
{
  "source_index": "carriers-000001",
  "predecessors": {
    "selector": "out-of-service",
    "oos_status": ["ACTIVE"],
    "oos_date_from": "2020-01-01",
    "max_predecessors": 2000
  },
  "candidates": {
    "max_candidates": 100,
    "seed_signals": ["name-phonetic", "address", "exact-identifier"]
  },
  "signals": [
    {
      "type": "name-phonetic",
      "weight": 0.22,
      "fields": ["legal_name", "dba_name"],
      "subfield": "phonetic",
      "cross_field": true
    },
    {
      "type": "name-phonetic",
      "weight": 0.13,
      "fields": ["legal_name", "dba_name"],
      "subfield": "phonetic_bm",
      "cross_field": true
    },
    {
      "type": "name-token",
      "weight": 0.1,
      "fields": ["legal_name", "dba_name"],
      "subfield": "clean"
    },
    {
      "type": "address",
      "weight": 0.2,
      "fields": ["phy_street", "mailing_street"],
      "exact_subfield": "clean",
      "fuzzy_subfield": "tokens",
      "fuzzy_scale": 0.7
    },
    {
      "type": "exact-identifier",
      "weight": 0.12,
      "phone_fields": ["telephone", "fax"],
      "text_fields": ["email_address"]
    },
    {
      "type": "agent",
      "weight": 0.04,
      "name_field": "boc3_agents.co_name",
      "address_field": "boc3_agents.street_po",
      "idf_weighted": true
    },
    {
      "type": "temporal",
      "weight": 0.05,
      "predecessor_date": "out_of_service_orders.oos_date",
      "successor_date": "add_date",
      "max_gap_days": 365
    },
    { "type": "vin-overlap", "weight": 0.08, "fields": ["crashes.vehicle_identification_number"] }
  ],
  "scoring": {
    "min_total_score": 0.35,
    "min_signals": 2,
    "require_identity_signal": true,
    "max_pairs_per_predecessor": 10
  }
}
```

**Refined during planning:** `exact-identifier` takes `phone_fields` and
`text_fields` rather than one `fields` list. The signal reads raw `_source`
values rather than analyzed tokens, so it needs no `.clean` subfield paths, and
splitting the keys makes normalization explicit instead of inferred from field
names.

**Weights need not sum to 1.0** (as written they sum to 0.94). Because
unevaluable signals return `None` and drop out, the total is always renormalized
over the signals actually present — so only the _ratios_ between weights matter.
This is not an error to be "fixed."

`selector` is a **closed enum** — `out-of-service`, `revoked-authority`,
`both`, or `either` — each backed by a Python-built query. This is the line that stops
config from becoming a DSL: a population can be picked and bounded, but not
newly defined in JSON.

Seven signal types, each a Python class returning `0.0–1.0` or `None`.
`name-phonetic` appears **twice** with different `subfield` values; that is how
the double-metaphone and Beider-Morse arms are weighted independently, and how
"which encoder performs better" gets answered empirically.

### The three scoring guards

- **`min_signals`** — unevaluable signals return `None`, drop out, and weights
  renormalize over what remains. Otherwise a carrier with no BOC-3 record is
  punished for _missing data_ rather than judged neutrally. But renormalizing
  means one lucky signal could score 1.0, so a floor is required.
- **`require_identity_signal`** — at least one of name / address / identifier
  must fire. Prevents pairs matching on temporal proximity alone, which is
  meaningless when 340K carriers have been shut down.
- **`max_pairs_per_predecessor`** — caps output volume. Without it a common name
  like `A&A TRUCKING` emits hundreds of pairs.

Deliberately excluded: no `index-populate` reuse (that phase is CSV-to-index;
this is index-to-index), and no new alias machinery (`index-create` covers it).

## Section 3 — Scoring model and signal math

Approved 2026-07-30.

### Getting analyzed tokens back out

Python-side scoring needs _analyzed tokens_, but Elasticsearch keeps them in
the inverted index and does not return them in `_source`. Calling `_analyze`
per string is far too many round trips.

The design uses **`_mtermvectors`**, batched once per predecessor across its
candidate set, requesting only the scored subfields. This returns exactly the
tokens Elasticsearch produced — same analyzer, no drift — without
reimplementing double-metaphone or Beider-Morse in Python. Term vectors are
generated on the fly, so no `term_vector` setting in the mapping and no index
bloat (which would be severe on the Beider-Morse subfield).

### Per-signal math

Every signal returns `0.0–1.0` or `None`. `None` means "not evaluable".

| Signal             | Math                                                                                                                                                                                                         |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name-phonetic`    | `0.5·jaccard + 0.5·containment` over token sets, where containment is `\|A∩B\| / min(\|A\|,\|B\|)`. With `cross_field`, computed over all four legal×dba pairings, max taken. `None` if either set is empty. |
| `name-token`       | Same math on the `.clean` subfield.                                                                                                                                                                          |
| `address`          | Exact `.clean` equality → 1.0; otherwise token containment on `.tokens` × `fuzzy_scale`. Max over phy/mailing pairings.                                                                                      |
| `exact-identifier` | 1.0 if any of phone / email / fax matches and is non-blank. Binary.                                                                                                                                          |
| `agent`            | IDF-weighted; see below.                                                                                                                                                                                     |
| `temporal`         | Linear decay from 1.0 at `gap = 0` to 0.0 at `max_gap_days`.                                                                                                                                                 |
| `vin-overlap`      | 1.0 if at least one VIN is shared.                                                                                                                                                                           |

### Why containment, not pure Jaccard

Abbreviation is one of the named evasion tactics. `SMITH TRUCKING LLC` →
`SMITH LLC` scores Jaccard 0.5 but containment 1.0. Pure Jaccard would punish
exactly the behavior being hunted. Blending the two keeps both properties —
full overlap still outranks a subset match.

### Three traps the math must dodge

- **Placeholder identifiers.** `telephone` values like `0000000000` would
  cluster thousands of unrelated carriers. Reject repeated-digit and all-zero
  values before comparing; treat them as absent.
- **`100 MAIN ST` exists in every state.** When `phy_state` differs _and_ the
  street matched only fuzzily, scale the address score by 0.5. An exact street
  match across different states stays strong — that is genuinely suspicious.
- **BOC-3 fires constantly without IDF.** Score is normalized inverse document
  frequency — `log(N / agent_carrier_count) / log(N)`, where N is the total
  carriers with an agent — computed once at sweep start with a `terms`
  aggregation. Sharing the largest filer scores ~0.17; sharing a rare agent
  scores ~0.95; an unseen agent scores 1.0.

  **Corrected 2026-07-30 during implementation.** This section originally
  specified `1.0 − (count / N)` while simultaneously claiming a big filer would
  score ~0.07. Those contradict, and the formula was the wrong one: with only
  89 agents the largest share is 9.4%, so `1 − share` compresses every agent
  into `[0.906, 1.0]` and the signal carries no discriminating power at all —
  the exact opposite of this bullet's purpose. Normalized IDF spreads the same
  population across `[0.167, 1.0]`. Caught when an implementer found the
  plan's formula and its test expectations could not both hold.

### Temporal is asymmetric

Pre-registered shell companies are a real tactic, so a successor whose
`add_date` precedes the shutdown is not disqualified — but it is weaker
evidence than one registered days after. Negative gaps are scored on `|gap|`
against a shorter window (180 days) at half scale.

### Total

`Σ(weight_i × score_i) / Σ(weight_i)` over non-`None` signals, then the Section
2 guards applied.

### The BOC-3 finding that changed this section

Measured, not assumed: BOC-3 contains only **89 distinct agent names**, **85
distinct addresses**, and **85 distinct `attn_to_or_title` values** across 1.43M
rows. Process agents are a commercial filing industry; the top agent covers
9.4% of filings and the top 100 cover 100%. Two unrelated carriers share an
agent roughly 7% of the time by chance.

`DOT-Commercial/README.md` describes BOC-3 as "a harder signal to fake than a
business address." **That claim does not hold** — the dataset carries no
per-carrier information at all, only which of ~89 filing companies a carrier
paid. The README should be corrected.

Consequences: `agent` weight drops from 0.10 to **0.04**, and `agent` is
removed from `seed_signals` — using it for candidate generation would pull 100
essentially random carriers per predecessor.

Additionally, `dot_number = 00000000` appears on **159,141** BOC-3 rows as a
placeholder and must be filtered.

### Date typing in the shadow datasets

`oos_date`, `rescind_date`, `orig_served_date`, and `disp_served_date` are all
mapped as **`keyword`, not `date`**. Values are ISO (`2022-07-09`), so parsing
is trivial, but the temporal signal must parse client-side rather than rely on
Elasticsearch date math.

**Correction recorded for future readers:** during design, `boc3-agents` and
`auth-history` were briefly suspected of producing zero enrichment matches
because their CSV `dot_number` values are zero-padded to 8 digits
(`'00085526'`) while `carriers` uses unpadded values (`'85526'`). This is a
non-issue: `dot_number` is pinned as `long` in all three shadow datasets, and
`pd.read_csv` infers the column as `int64` regardless, so padding is gone before
Elasticsearch sees it. The enrichments work.

## Section 4 — Candidate generation and the sweep loop

Approved 2026-07-30.

### Predecessor populations, measured

| `selector`            | Distinct predecessor carriers |
| --------------------- | ----------------------------- |
| `out-of-service`      | 340,352                       |
| `revoked-authority`   | 1,008,619                     |
| `both` (intersection) | 182,774                       |
| `either` (union)      | 1,166,197                     |

`revoked-authority` covers roughly half of every carrier ever registered.
Involuntary revocation for lapsed insurance is routine and is _not_ by itself
evidence of a chameleon.

### Selectors, and the trap in the vocabulary

`auth_history` has a clean closed vocabulary — 10 `original_action_desc` values,
16 `disp_action_desc` values — and it contains a trap.

`original_action_desc = 'INVOLUNTARY REVOCATION'` occurs **2,215,957** times,
but **2,208,586** dispositions are `'DISCONTINUED REVOCATION'`, meaning the
revocation was _reversed_. Selecting on the filing would gather millions of
carriers that were never shut down. Selectors must key on the **disposition**:

- `out-of-service` — has an `out_of_service_orders` entry, optionally filtered
  by `status` and an `oos_date` window. **This is the default.**
- `revoked-authority` — `auth_history.disp_action_desc == 'REVOKED'`.
  Documented as a broad net requiring a date window and a cap.
- `both` — the 182,774 intersection. Highest-confidence population.
- `either` — the union.

### Iteration

A point-in-time plus `search_after`. Not `from`/`size`, which breaks past
10,000 results; not `scroll`. The PIT gives a consistent snapshot across a sweep
that may run for hours.

### Candidate query

One per predecessor, a `bool` with `minimum_should_match: 1`:

- `match` on `legal_name.phonetic` / `dba_name.phonetic` using the
  predecessor's raw name text (Elasticsearch analyzes the query with the same
  analyzer)
- `match` on `phy_street.clean` / `mailing_street.clean`
- `term` on `telephone.clean`, `email_address.keyword`
- `must_not` on the predecessor's own `dot_number`
- `size: max_candidates`

`seed_signals` controls which clauses appear. `agent` is excluded per Section 3
— seeding on it would return 100 essentially arbitrary carriers.

Then one `_mtermvectors` call for the candidate ids plus the predecessor,
fetching the scored subfields. **Two round trips per predecessor.**

### Cost

The 2,000-predecessor default is ~4,000 round trips — minutes. A full 340K
`out-of-service` sweep is ~680,000 round trips and runs for hours. That is the
design working as intended, and it is why the default is small.

### Concurrency

The predecessor loop is sequential by default with a configurable worker count,
kept conservative deliberately: `README.md` documents `parallel_bulk`'s 8
threads already saturating the enrich-coordinator queue (1024 slots) during
carrier loads. Output writes go through `parallel_bulk`.

### Pair direction and dedupe

Pairs are **directed** (`predecessor → successor`) because direction carries
meaning. When both carriers are shut down, both directions can legitimately
appear — chameleon chains are real. Dedupe is on the ordered
`(predecessor_dot, successor_dot)` tuple, which also yields a deterministic
`_id` for the output index.

### Truncation warning

If a predecessor's candidate query returns exactly `max_candidates`, real
matches may have been cut off. This is logged per-predecessor and counted in the
run summary — the same "make silent wrong output loud" principle the OpenSearch
spec identified as independently worth doing.

## Section 5 — Output index, error handling, and testing

Approved 2026-07-30.

### Output document

Index `chameleon-candidates-{now/d}-000001`, alias `chameleon-candidates-000001`,
built by the existing `index-create` and `index-map` phases.

```json
{
  "predecessor": {
    "dot_number", "legal_name", "dba_name",
    "phy_street", "phy_city", "phy_state",
    "shutdown_date", "shutdown_reason"
  },
  "successor": {
    "dot_number", "legal_name", "dba_name",
    "phy_street", "phy_city", "phy_state", "add_date"
  },
  "total_score": 0.78,
  "gap_days": 34,
  "signals_present": 5,
  "matched_on": ["name-phonetic", "address", "exact-identifier"],
  "signals": [
    { "type", "subfield", "weight", "score", "contribution", "detail" }
  ],
  "generated_at": "...",
  "run_id": "..."
}
```

`matched_on` is a keyword array and is what makes the output triageable — it
supports faceting ("show only pairs sharing a VIN") in Kibana without parsing
the nested breakdown. `signals[].detail` carries human-readable evidence, e.g.
`"shared tokens: SM0, TRKN"`.

`_id` is the composite `predecessor_dot|successor_dot`, so reruns are
idempotent.

**Small refactor:** `compute_id()` currently lives inside
`phase_index_populate.py`. Lift it into `utils/` so both phases share one
implementation instead of duplicating the join logic.

### Error handling

The theme is converting silent wrong output into loud failure, because every
bug documented in this repo's README is of that shape.

1. **Mapping precondition check at startup.** Query `_mapping` and verify every
   subfield the config scores actually exists. Running against an older
   `carriers` index missing `.phonetic_bm` would otherwise make
   `_mtermvectors` return nothing for that field, turn every phonetic score
   into `None`, and emit a silently empty or garbage result set — the exact
   failure mode of the enrich bug. Fail with a message naming the missing
   subfield.
2. **Refresh the source index and confirm a non-zero count** before sweeping.
3. **Validate config at load.** Unknown signal `type`, weights summing to zero,
   or an unknown `selector` fail immediately rather than mid-sweep.
4. **Per-predecessor failures are logged and counted**; the sweep continues.
5. **Run summary** reports predecessors processed, candidates examined, pairs
   emitted, truncations hit, and errors. Empty output is a warning, never a
   success.

### Testing

**Scope decision: unit tests only.** No integration fixture, no CI.

The signal classes are pure functions over token sets and test without a
cluster. `pytest` is introduced to the repo, which currently has no tests and
no `.github/workflows` (note: `2026-07-25-github-actions-verification-design.md`
designed CI that was never implemented).

Cases worth covering are the ones the data forced:

- abbreviation — `SMITH TRUCKING LLC` vs `SMITH LLC`
- empty token set — a carrier named literally `TRUCKING LLC`
- placeholder phone rejection — `0000000000`
- cross-state fuzzy street suppression
- IDF agent weighting across common vs rare agents
- negative temporal gap (pre-registered shell)
- `None` renormalization, `min_signals`, and `require_identity_signal` guards

## Known limitations

Recorded deliberately, not discovered later.

1. **Default thresholds are unvalidated.** With unit tests only and no
   ground-truth pairs, `min_total_score: 0.35` and every signal weight are
   informed guesses. The first real sweep is also the calibration run. All
   weights and thresholds live in config specifically so they can be retuned
   without code changes.
2. **Recall is bounded by the candidate query.** A successor sharing nothing
   with its predecessor across name, address, phone, or email is invisible to
   the sweep. This is inherent to the two-stage design chosen over full pairwise
   blocking.
3. **VIN coverage is thin.** Only crash VINs are reachable today (333K crashes
   against 2M carriers), so `vin-overlap` returns `None` for most pairs.
   Reaching the 5.6M-row inspection VIN signal requires adding
   `insp_unit_vehicle_id_number` to `inspections-enrichment-policy`'s
   `enrich_fields` — worth doing, and out of scope here.
4. **BOC-3 contributes almost nothing** (89 distinct agents). It is retained at
   weight 0.04 as weak corroboration only.
5. **A full sweep takes hours.** ~680,000 round trips for the 340K
   `out-of-service` population.

## Follow-on work identified but not included

- Correct `DOT-Commercial/README.md`'s claim that BOC-3 process agents are "a
  harder signal to fake than a business address" — the 89-agent measurement
  contradicts it.
- Add `insp_unit_vehicle_id_number` to `inspections-enrichment-policy` to make
  the VIN signal usable at volume.
- Filter `dot_number = 00000000` (159,141 rows) from `boc3-agents` ingestion.
- Consider pinning `oos_date` and the `auth_history` date fields as `date`
  rather than `keyword`.
