# Config-driven analysis portability

Make the `entity-match` analysis reachable by configuration, so a new dataset is
onboarded by writing JSON rather than editing framework code. Validate the
result by giving CMS-Providers a working sweep.

## The problem

Ingestion is already portable. `configuration.json` declares steps and phases,
each step owns its `index-config` / `index-mappings` / `index-settings` /
`pipelines` / `enrichment-policies`, and CMS-Providers proves the claim by being
a complete second project containing no Python at all.

The analysis half is not. Three separate couplings block it, and they fail in
different ways:

1. **The entity key is a hardcoded attribute name.** `matching/documents.py:21`
   declares `CarrierDoc.dot_number`, read directly by `matching/candidates.py`,
   `matching/scorer.py:162`, and `phase_providers/phase_entity_match.py`.
2. **The population definition is hardcoded FMCSA semantics.**
   `matching/predecessors.py` embeds the field paths
   `out_of_service_orders.oos_date` and `auth_history.disp_action_desc`, the
   literal value `"REVOKED"`, a sort on `dot_number`, and four selectors that
   are FMCSA's notions of "shut down" rather than general ones.
3. **The output document shape is hardcoded.**
   `phase_entity_match.py:664` computes `gap_days` from literal
   `out_of_service_orders.oos_date` and `add_date`; `_carrier_summary` at line
   706 emits a fixed six-field summary. A different project would produce pairs
   labelled with fields its records do not have.

Measurement is coupled the same way but in the opposite direction — DOT-specific
code sitting in framework directories. `scripts/measure_crash_lift.py` carries
157 domain references across 37KB; `scripts/measure_chameleon_shape.py` and
`utils/crash_lift.py` are the same shape. Meanwhile
`DOT-Commercial/precision_metrics.py` is correctly placed and says so in its own
docstring.

**And there is no second example of the analysis to design against.**
CMS-Providers is ingestion-only. Any claim that the framework is portable is
unfalsifiable until something other than DOT-Commercial produces a scored pair.

## Decisions taken

| Question                         | Decision                                                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Scope                            | Matcher core, measurement harness, and a working second instance — all three                                 |
| Projects with no lifecycle dates | Add a duplicate-detection mode; the population stage becomes pluggable                                       |
| Framework vs project metrics     | Framework owns runner and primitives; project metrics are config; hypothesis validation stays project Python |
| DOT-Commercial compatibility     | Same pairs, config may change. Behavioral gate, not a config-compatibility gate                              |
| Onboarding deliverable           | Both a scaffold generator and a config validator                                                             |
| Selector definitions             | Named selectors in project config, assembled from a closed clause menu in code                               |

### Approach, and what was rejected

**Chosen: parameterize in place, with config-composed selectors.** The module
layout stays. Dataset knowledge moves out of code and into project config
through a small, schema-validated, closed vocabulary.

_Rejected — selectors stay Python._ Each project ships a `selectors.py`
registering named selectors. Zero DSL and maximum expressiveness, but onboarding
a dataset then requires writing Python, which is the premise this work exists to
remove. It also creates a Python extension surface the framework must keep
stable indefinitely.

_Rejected — extract the framework into an installable package._ The honest end
state once several projects exist, but a large mechanical churn landing
simultaneously with semantic changes would make the zero-delta gate untrustworthy:
a packaging mistake and a matcher regression would be indistinguishable. Revisit
after a third project has shown where the boundary actually belongs.

**The cost of the chosen approach, stated rather than hidden.** The clause menu
in `population.selectors` and the predicate menu in `metrics.json` are small
query DSLs expressed in JSON — the thing the README's open item 5 says to avoid.
The defense is that both are closed, tiny, schema-validated, and admit no
arbitrary Elasticsearch query or scripting. The alternative is worse. This
tension is recorded here so a later reader knows it was a decision and not an
oversight.

## Module boundaries

The organizing rule, already stated in the README and made true by this work:
everything under the repository root is generic; everything a dataset knows
about itself lives in its project directory.

| Path               | Owns                                                                                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| `matching/`        | Sweep mechanics: population selection, candidate retrieval, signal evaluation, scoring. No dataset vocabulary. |
| `phase_providers/` | Phase implementations, plus a new `validate` phase                                                             |
| `utils/`           | Config loading, ES client, IDs, fingerprints, field-rarity tables, the generic sweep-diff engine               |
| `schema/`          | New. One JSON Schema per config file kind                                                                      |
| `scripts/`         | Generic tooling only                                                                                           |

### Renames

- `CarrierDoc` → `EntityDoc`; `dot_number` → `entity_key`. **No `dot_number`
  alias property on the dataclass.** An alias would let DOT vocabulary re-enter
  framework code, giving two names for one value with no rule about which to
  use — which is the condition being removed.
- `matching/predecessors.py` → `matching/population.py`;
  `PredecessorSelector` → `PopulationSelector`. "Predecessor" presumes
  succession, which a duplicate-detection project does not have.
- `agent_rarity` and `_normalize_agent_key` in `matching/documents.py` →
  a general `FieldRarityTable` keyed by field path. The BOC-3 prefetch at
  `phase_entity_match.py:575` becomes "prefetch value frequencies for whichever
  field a signal declares rarity-weighted."
- Signal type `vin-overlap` is **deleted**, not renamed.
  `matching/signals.py:547` already registers `type_names = ("vin-overlap",
"shared-token")` and `shared-token` is already in `IDENTITY_SIGNAL_TYPES`, so
  this removes the DOT-flavored half of an alias pair that already exists.
- Signal type `agent` → `rarity-weighted-value`. Its `max_shared_carriers` key
  → `max_shared_entities`.
- `name-phonetic`, `name-token`, `address`, `exact-identifier`, and `temporal`
  keep their names. They are already generic and renaming buys nothing.

### Moves out of the framework

`scripts/measure_crash_lift.py`, `scripts/measure_chameleon_shape.py`, and
`utils/crash_lift.py` relocate to `DOT-Commercial/`. They validate a hypothesis
against an FMCSA outcome variable — whether a flagged pair predicts elevated
crash rates — which no framework can assume exists. `tests/test_crash_lift.py`
moves with them.

### Deliberately unchanged

The scoring arithmetic, weight renormalization, `conclusive` handling,
`min_signals` distinct-source counting, the `_meta` provenance stamping, and
every guard in `PairScorer`. These are already dataset-agnostic, and they are
what the zero-delta gate protects. **If this work changes scoring arithmetic at
all, that is evidence of overreach rather than progress.**

`matching/signals.py` is not split despite being the largest module at 28KB.
Splitting it while also editing its type registry would make the compatibility
diff unreadable, which is the one thing that must stay reviewable.

## Configuration surface

`index-config`, `index-mappings`, `index-settings`, `pipelines`, and
`enrichment-policies` are untouched. They already work.

### `entity-match.json`

Three new top-level blocks replace what is hardcoded today.

**`entity`** — what the framework treats as a record's identity.

```json
"entity": {
  "key": "dot_number",
  "key_label": "dot_number",
  "summary_fields": ["legal_name", "dba_name", "phy_street", "phy_city", "phy_state"]
}
```

`summary_fields` replaces the fixed list in `_carrier_summary`.

Every emitted pair carries **both** `entity_key` and, when `key_label` is set, a
copy under that label:

```json
"predecessor": { "entity_key": "1498477", "dot_number": "1498477", "legal_name": "..." }
```

Both rather than either, for the reason the closed provenance work item already
gives about `source_index`: a pair is routinely read on its own — pulled by
`_id`, exported into a review sample, quoted in a README — and at that point the
project config is not in the reader's hands. `entity_key` is what generic
tooling reads without loading config; the labelled copy keeps
`DOT-Commercial/precision_metrics.py`, `sample_pairs_for_review.py`, every
baseline under `DOT-Commercial/data/precision/`, and every README figure working
untouched.

`_id` is unaffected: `phase_entity_match.py:699` composes it as
`compute_id({"p": ..., "s": ...}, ["p", "s"])` with literal `p`/`s` keys, so no
label ever enters an id.

**`lifecycle`** — present for a succession project, absent for a
duplicate-detection one.

```json
"lifecycle": {
  "shutdown_date": "out_of_service_orders.oos_date",
  "registration_date": "add_date",
  "shutdown_reason": "out_of_service_orders.oos_reason"
}
```

These paths currently exist **twice**: on the `temporal` signal as
`predecessor_date` / `successor_date`, and again as literals at
`phase_entity_match.py:664` where `gap_days` is computed. Nothing checks that
the two agree. The `temporal` signal drops its two path keys and reads
`lifecycle` instead, so the gap a signal scores and the `gap_days` a reviewer
reads become the same computation by construction. This is the same
drift-prevention argument the provenance work already applied elsewhere, and it
is worth making on correctness grounds independent of portability.

With `lifecycle` absent: `gap_days` is emitted as `null`, the `min_gap_days` /
`max_gap_days` scoring guards are skipped, and a configured `temporal` signal is
**rejected at validation** rather than silently scoring nothing.

**`population`** — replaces `predecessors` and carries the selector definitions
that are code today.

```json
"population": {
  "mode": "lifecycle",
  "sort_field": "dot_number",
  "max_records": null,
  "selector": "out-of-service",
  "selectors": {
    "out-of-service": {
      "nested-exists": {
        "path": "out_of_service_orders",
        "require": "oos_date",
        "terms": { "status": ["ACTIVE"] },
        "range": { "oos_date": { "gte": "2020-01-01" } }
      }
    },
    "revoked-authority": { "term": { "auth_history.disp_action_desc": "REVOKED" } },
    "both": { "all": ["out-of-service", "revoked-authority"] },
    "either": { "any": ["out-of-service", "revoked-authority"] }
  }
}
```

Four clause kinds, closed: `nested-exists`, `term`, `all`, `any`.

### Signal provenance: tying a generic type back to a concrete field

Deleting `vin-overlap` costs the output something real. A reader of a pair
infers "this was a shared vehicle identifier" from the type name alone today,
because nothing else in the emitted document says so —
`phase_entity_match.py` writes `signal_type`, `subfield`, `weight`, `score`, and
`contribution` per contribution, and `matching/scorer.py:232` builds
`matched_on` as a set of types. Rename the type and that inference disappears.

Two additions restore it, answering different questions:

**`fields` on each emitted contribution — derived, so it cannot drift.** The
signal already knows which configured field paths produced the overlap.

```json
{
  "signal_type": "shared-token",
  "signal_name": "vin-overlap",
  "fields": ["crashes.vehicle_identification_number"],
  "score": 1.0
}
```

**Field paths only, never field values.** Emitting the matched identifier itself
would place an identifying value belonging to a flagged entity into the pair
document, which the repository's anonymization rule forbids. The path says what
kind of evidence fired; the value would say who.

**`name` on each signal in project config — the operator's shorthand.** Carried
through to the contribution as `signal_name`. The label lives on the signal
_instance_ in project config, so the framework never learns the word "vin", and
DOT-Commercial names its own signals. It also disambiguates the two
`name-phonetic` signals DOT configures, which are indistinguishable in the
output today.

**`matched_on` stays keyed by type.** Names must not enter it:
`IDENTITY_SIGNAL_TYPES`, `matched_on_equals`, and `matched_identity_equals` all
operate on that set, so admitting labels would change metric values and break
the compatibility gate for no gain.

Together these partially deliver the README's open item 6, the `signals[].detail`
field specified in the chameleon matching design and never implemented.

**Open, found while verifying the gate:** a `temporal` contribution emits
`"fields": []`. Its date paths live in the `lifecycle` block, and `fields_read()`
reads only signal-level config keys, so a reader of one pair cannot tell which
dates its `gap_days` was measured between — which is the exact failure this
section exists to prevent, on the one signal whose paths moved. Fixing it means
teaching `fields_read()` about `lifecycle` or emitting those paths at document
level; both change the emitted document, so neither belonged inside the commit
range the gate certified.

`nested-exists` is one primitive rather than three composable ones **because
flattening is a known defect, not a style preference**. The docstring at
`predecessors.py:63` records the incident: under an object mapping, a record with
an ACTIVE 2015 order and an INACTIVE 2022 order satisfied `status=ACTIVE` and
`oos_date >= 2020` from two different orders and was swept even though no single
order qualified — and `TemporalSignal` then reported a shutdown date from an
order the selector never intended to match, so `gap_days` on the emitted pair
described the wrong event. Making nesting the only available shape means no
project can reintroduce that by writing config that looks reasonable.

`mode: "all-entities"` ignores `selector` and `selectors` and sweeps every
record. Pairs carry no succession claim: `gap_days` is `null` and the emitted
sides are named `left` / `right` rather than `predecessor` / `successor`.

`sort_field` generalizes the `dot_number` sort in `PopulationSelector.iterate`,
which needs a stable total order for `search_after` paging under a
point-in-time.

### `metrics.json`, new, one per project

Two metric kinds cover every metric in `DOT-Commercial/precision_metrics.py`:
count of pairs matching a filter, and distinct count of a field over pairs
matching a filter.

```json
{
  "baseline": "data/precision/baseline-post-reload.json",
  "metrics": [
    { "name": "pairs" },
    { "name": "pairs_ge_070", "filter": { "score_gte": 0.7 } },
    {
      "name": "coherent_ge_070",
      "filter": { "all": [{ "score_gte": 0.7 }, { "gap_between": [-180, 365] }] }
    },
    { "name": "vin_only", "filter": { "matched_on_equals": ["shared-token"] } },
    { "name": "vin_only_identity", "filter": { "matched_identity_equals": ["shared-token"] } },
    {
      "name": "identical_name_triage",
      "filter": {
        "all": [
          { "score_gte": 0.7 },
          { "has_signal_type": ["shared-token", "exact-identifier"] },
          { "gap_lte": 365 },
          { "fields_equal": "legal_name" }
        ]
      }
    },
    { "name": "predecessors_with_pairs", "distinct": "predecessor.entity_key" }
  ]
}
```

Closed predicate menu: `score_gte`, `score_lt`, `gap_between`, `gap_lte`,
`has_signal_type`, `matched_on_equals`, `matched_identity_equals`,
`signal_count_gte`, `fields_equal`, plus `all` / `any` / `not`.

Two semantics are pinned in the schema rather than left to be rediscovered:

- **A null gap never matches `gap_between` or `gap_lte`.** This preserves
  `_is_coherent`'s existing behavior and its reasoning: an unparseable date is
  not evaluable, which is not the same as being outside the window, and counting
  it as coherent inflates the metric this analysis exists to move.
- **`matched_identity_equals` intersects with `IDENTITY_SIGNAL_TYPES` before
  comparing.** `precision_metrics.py` keeps `vin_only` and `vin_only_identity`
  as separate metrics because they disagreed by 156 pairs on the baseline — 519
  against 675 — and collapsing them silently picks a side.

Crash-lift does not move into this file. It joins an outcome variable from
another index and stays project Python.

`metrics.json` sits beside `entity-match.json` in the analysis step's
configuration directory. No new directory.

## Validation and scaffolding

### The `validate` phase

A new phase, runnable standalone and run automatically before `entity-match`.
It exists because the repeated failure in this codebase is **config that parses
and is inert** — analyzers naming columns that were renamed, enrichment policies
pointing at superseded indexes, a `term` query against a `text` field matching
zero documents. Schema validation alone would catch none of those.

Three tiers, cheapest first, each fatal:

1. **Schema.** Every config file validates against `schema/`. Catches typos,
   unknown signal types, unknown clause kinds, and structurally wrong blocks.
2. **Cross-config coherence.** A `temporal` signal without a `lifecycle` block.
   A `seed_signals` entry naming a signal that is not configured. A
   `population.selector` naming a selector that is not defined. A
   `sort_field` or `entity.key` absent from the source index mapping.
3. **Live cluster.** Every field path a signal references exists in the source
   index mapping with a compatible type; every `subfield` a signal names is
   actually declared; the source index's analysis fingerprint matches the
   configured analyzers. The last of these is the existing
   `_check_analysis_fingerprint` check, promoted from a preflight buried inside
   `entity-match` to a phase an operator can run on demand.

Tier 3 requires a loaded cluster. Tiers 1 and 2 do not, and run in CI.

### `scripts/new_project.py`

Reads one or more CSVs and emits a skeleton project directory: `configuration.json`,
per-dataset `index-config` / `index-mappings` / `index-settings`, and a stub
`entity-match.json` and `metrics.json`.

**Generated config is deliberately not runnable.** Every field the profiler
cannot decide is emitted as an explicit `"TODO"` marker that fails schema
validation, and generation runs `profile_dataset.py` and writes its cardinality
findings into the skeleton as comments-adjacent metadata. The hazard being
designed around is the one `docs/adding-a-dataset.md` names directly: a field can
look like a strong fingerprint and be worthless, and generated config invites
trusting output nobody measured. The generator's job is to remove typing, not to
remove judgement — so a scaffolded project cannot sweep until a human has
resolved every marker.

## CMS-Providers as the second instance

This is what makes the portability claim falsifiable.

### What CMS-Providers was originally for, and why it cannot do it

The project was started to find the healthcare version of the chameleon
pattern: a medical clinic shut down for fraud reopening at the same address, or
under the same ownership, with a near-miss name. That is structurally the same
hunt DOT-Commercial runs, and it is **not** what this spec has CMS doing.

The reason is the data, not the domain. All three downloaded extracts are
**directories** — who practices where, which facilities they affiliate with.
A directory describes a present state. None of the three records an _event_:
there is no enrollment date, termination date, exclusion date, or reinstatement
date in any of them. Per `docs/adding-a-dataset.md`, similarity without lifecycle
timing yields duplicate detection but not succession, and succession is the
entire fraud pattern.

So the original goal is deferred rather than abandoned, and the follow-on that
restores it is recorded below. What CMS validates _here_ is narrower and should
be described that way in its README: that the framework runs on a second dataset
by configuration alone, and that `mode: "all-entities"` works.

**What CMS already has.** `CMS-Providers/configuration/hospitals/index-mappings.json`
already declares exactly the subfields the signal vocabulary references:
`Facility Name.clean` and `.phonetic`, `Address.clean` and `.tokens`,
`Telephone Number.clean`, backed by the full street-suffix synonym set in
`index-settings.json`. No mapping or analyzer work is required.

**What gets added:** a `hospital-duplicates` step with `index-create`,
`index-map`, `validate`, and `entity-match` phases, plus an `entity-match.json`
using `entity.key: "Facility ID"`, `population.mode: "all-entities"`, and
signals over name, address, and telephone. No `lifecycle` block, no `temporal`
signal, no gap guards.

**Scope boundary:** hospitals only. `doctors-clinicians` is 32 columns with a
three-part composite key and would be a second design conversation. One working
duplicate-detection sweep is what validates the framework; two is scope creep.

**Prerequisite — the local hospitals CSV is a stub and must be replaced.**
Measured 2026-08-16: `CMS-Providers/data/hospitals/Hospital_General_Information.csv`
holds a header and five rows. The other two CMS extracts are genuine at 3,387,943
and 2,260,194 lines. Five records cannot validate anything, and the file is
gitignored under `*/data/`, so this is a local-checkout problem rather than a
committed one.

`CMS-Providers/README.md` records the genuine extract at **5,432 rows**, so the
local file is a corruption rather than a smaller republication.

It would not have self-corrected. `download_cms_provider.sh` guarded each
download with `if [ -s "$dest" ]` — non-empty — and a five-row file is
non-empty, so the script would have skipped it on every future run: precisely
the failure its own header comment warns about, one layer down. The guard was
hardened against a 404 HTML page being written into the CSV, and a
short-but-valid file defeated it the same way.

Fixed ahead of this work rather than inside it, since it blocks the download
regardless: the guard now tests plausibility rather than existence, and a file
below a 50-line floor is re-downloaded. Recorded as validation finding 6 in
`CMS-Providers/README.md`. **The operator still has to re-run
`bash download_cms_provider.sh`** — the fix prevents recurrence, it does not
repair the file already on disk.

Until that file is replaced, no claim about what a CMS sweep finds — including
its runtime or candidate-space behavior — is measurable.

**What CMS is expected to find, and the honest caveat.** Hospital records
sharing a name and address are far more likely to be legitimate multi-record
facilities than fraud. The value here is proving the framework runs, not
producing an accusation — and CMS should carry no `precision_metrics` analogue
implying otherwise. Its `metrics.json` records population shape only: pair
counts by score band and signal mix.

### Follow-on: the healthcare succession project

Recorded here so the deferred goal is not rediscovered from scratch, and
explicitly **out of scope for this work**.

The healthcare succession sweep needs two sources the project does not currently
download, filling the two roles DOT-Commercial already has filled:

| Role                               | DOT-Commercial          | Healthcare candidate                                                                                                |
| ---------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Shutdown event, dated              | `out-of-service-orders` | OIG **LEIE** — excluded individuals and entities, with an exclusion date, plus the separate reinstatement file      |
| Entity record with a creation date | `carriers`              | NPPES NPI registry (enumeration and deactivation dates), or CMS Medicare fee-for-service public provider enrollment |
| Corroborating join                 | `boc3-agents`           | `facillity-affiliations`, already downloaded                                                                        |

**This mapping is from recollection and has not been measured.** Before any of it
is planned, each candidate must be run through `scripts/profile_dataset.py` and
checked for the three things that decide whether it works at all: whether it
carries a usable date, whether it carries identity fields worth matching on, and
whether it shares a key with the others — `docs/adding-a-dataset.md` flags a
dataset with no shared key as needing a fuzzy pre-join, which is substantially
larger work. Treat the table as a starting point for that investigation, not as
a finding.

**Why this follow-on matters beyond CMS.** It is the only planned work that
would combine a `lifecycle` block with a non-FMCSA field vocabulary. Until it
exists, the lifecycle path is configured by exactly one dataset — the one it was
extracted from — which is the sharpest limitation of this design.

## Testing and the compatibility gate

### Unit tests

Existing `tests/test_predecessors.py`, `test_scorer.py`, and `test_signals.py`
are updated to the new names and config shapes. They are the regression net for
everything below the sweep.

New tests, each pinning a decision made above rather than a line of code:

| Test                        | Pins                                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `test_population.py`        | Each clause kind builds the query the old code built; `all-entities` mode emits no succession fields                           |
| `test_selector_equivalence` | The four config-defined DOT selectors produce queries byte-identical to today's four hardcoded ones                            |
| `test_metric_predicates.py` | Each predicate, and specifically that a null gap fails `gap_between` and `gap_lte`                                             |
| `test_config_validation.py` | Each tier-1 and tier-2 failure is caught and names the offending file and key                                                  |
| `test_entity_key.py`        | A pair carries both `entity_key` and the labelled copy, and `_id` is unchanged by labelling                                    |
| `test_signal_provenance.py` | A contribution carries `signal_name` and the field _paths_ that fired, never field values; `matched_on` still holds types only |

`test_selector_equivalence` is the highest-value test in the set: it compares
generated queries against the current hardcoded output directly, so the
riskiest part of the refactor is verified without a cluster.

### The compatibility gate

**Ran 2026-08-17 and passed on both checks** — all eleven metrics exactly equal
and the pair id sets identical at 75,537 each, against the source index stamped
`0595ca890d9ec6fb` and with no reload in between. Full record, including the
sweep summary and one gap found while verifying, in
[the runbook](../plans/2026-08-16-compatibility-gate-runbook.md).

The gate is behavioral, not structural. Config churn is free; result churn is
not.

1. Re-run the DOT-Commercial sweep against the same source index — the one
   stamped `0595ca890d9ec6fb`, which every figure in `DOT-Commercial/README.md`
   cites.
2. **All eleven metrics in
   `DOT-Commercial/data/precision/baseline-post-reload.json` must be exactly
   equal**, including `pairs: 75537` and `predecessors_with_pairs: 23040`. Not
   "small", not "explained." Renaming a registry key and moving a field path
   into config cannot move a pair, so any movement means the refactor broke
   something real.
3. **The set of pair `_id` values must be identical.** Compared by scrolling
   both populations and hashing the sorted id set. This is a separate check
   because eleven aggregate counts can coincide across a population that has
   genuinely changed — a pair lost and a pair gained in the same band cancel
   out. Composite ids are label-independent by construction
   (`compute_id({"p": ..., "s": ...}, ["p", "s"])`), so this check is valid
   across the rename.
4. Every metric in the config-driven `metrics.json` matches the value
   `DOT-Commercial/precision_metrics.py` produces on the same pair population.
   Run both; they must agree before the Python one is retired.

**Use direct equality, not `utils.sweep_compare.compare()`.** That engine exists
to judge whether an _intentional_ change moved the right metrics in the right
direction, and its expectation vocabulary — `must_not_fall`, `must_not_rise`,
`within_10pct`, `informational` — has no way to say "must not change at all."
Passing this refactor through it would let a real regression clear the gate
wearing `informational`.

**New emitted fields are invisible to steps 2 and 4 by design.** `signal_name`
and per-contribution `fields` add keys to the pair document without changing any
count, and the metric record is a dict of name to number. That is the intended
behavior, not a gap — step 3 is what confirms the population itself is untouched.

Step 4 is the only check that the metric DSL faithfully reproduces hand-written
Python. It runs against the existing implementation, not against a remembered
number.

### Sequencing note

The gate depends on a loaded cluster whose indexes match the baseline. The
README's two open reload items are outstanding, so **the baseline sweep must be
taken before any reload**, or the delta will conflate this refactor with the
`dot_number` and composite-`_id` re-keying already queued.

## Rollout order

Each step leaves the tree green and the DOT sweep runnable.

1. Move DOT-specific scripts out of `scripts/` and `utils/`. Pure relocation, no
   behavior change, smallest possible first commit.
2. `EntityDoc` and `entity_key`, with `key_label` emitted. Config gains
   `entity`; behavior unchanged.
3. `PopulationSelector`, the clause menu, and DOT's four selectors expressed as
   config. Guarded by `test_selector_equivalence`.
4. `lifecycle` block; `temporal` drops its path keys; `gap_days` and
   `_carrier_summary` become config-driven.
5. Signal renames — delete `vin-overlap`, rename `agent` — together with
   `signal_name` and emitted `fields`, since the provenance addition is what
   makes the rename safe to read.
6. **Compatibility gate runs here** — the last point at which the DOT sweep is
   expected to be bit-identical.
7. `schema/` and the `validate` phase.
8. `metrics.json`, the metrics runner, and DOT's metrics expressed as config.
   Verified against `precision_metrics.py`, which is retired only after they
   agree.
9. `scripts/new_project.py`.
10. CMS-Providers `hospital-duplicates` step, and the first sweep on a project
    that never had one.
11. Documentation: `docs/adding-a-dataset.md` gains the analysis half; the
    README's framework-vs-project section is rewritten from intent to fact; open
    item 5 closes.

## Out of scope

- Splitting `matching/signals.py`.
- Extracting the framework as an installable package.
- A CMS `doctors-clinicians` sweep.
- The two outstanding reloads in the README's open items. This work must be
  measured before them, not bundled with them.
- Any change to scoring arithmetic, weights, or thresholds.
- Fuzzy joins for datasets with no shared key. `docs/adding-a-dataset.md`
  already flags this as a separate and substantially larger piece of work.
- The healthcare succession project. Its source datasets are not downloaded, its
  field mapping is unmeasured, and it needs its own design conversation. It is
  recorded above so the original CMS intent survives, not so it can be attempted
  here.
- Hardening `download_cms_provider.sh`'s non-empty guard against implausibly
  small downloads.

## Known limitations

- **The clause and predicate menus are DSLs.** Closed and schema-validated, but
  DSLs. A project needing a population shape outside the four clause kinds must
  extend framework code — which is the correct outcome, but it means "onboarding
  is pure configuration" holds for datasets resembling the two examples and is
  an untested claim beyond them.
- **The lifecycle path is configured by exactly one dataset — the one it was
  extracted from.** This is the sharpest limitation here. CMS exercises the
  no-lifecycle path and DOT exercises everything else, so nothing combines a
  `lifecycle` block with a non-FMCSA field vocabulary, and an abstraction with a
  single instance is indistinguishable from a rename. Extracting `lifecycle`
  from `phase_entity_match.py` is still worth doing on its own merits — it
  removes a genuine two-sources-of-truth defect — but the _portability_ claim
  for that block is unproven until the healthcare succession follow-on, or
  something like it, configures it differently.
- **`all-entities` mode has no recall story.** It sweeps every record and relies
  entirely on `seed_signals` to bound the candidate space. On a large corpus with
  a weak seed that is an unanswered performance question. The CMS hospitals
  extract is small enough that it will not surface the problem, which means this
  work validates portability without validating scale.
