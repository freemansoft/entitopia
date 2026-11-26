# Third dataset: declared successions as known matches (FDIC + GLEIF)

Date: 2026-11-26
Status: draft, awaiting approval

## Why

[Article 3](../../blog/2026-08-30-03-configuration-driven-matching.md) argues
that a general matcher needs more than one dataset. The hospital data exposed
four defects in shared code that the carrier data never triggered. A third
dataset should test something the first two do not.

The gap that matters most is in measurement, not in loading. Every score in
this repo is uncalibrated. The glossary says `total_score` "is uncalibrated
confidence, not probability". The one attempt at external validation, against
crash outcomes
([2026-08-06 design](2026-08-06-crash-outcome-validation-design.md)), came back
null. No project has a single pair known to be correct, so a threshold change
can be judged only by reading pairs.

Two public registries record successions as facts. They are dated, have both
sides named, and were asserted by the registrar:

- **FDIC BankFind** history records each merger or failure of an insured bank as
  an outgoing institution and a surviving or acquiring one, with an effective
  date.
- **GLEIF**, the global LEI register, lets an inactive legal entity name its
  successor LEI.

**The goal is to find undeclared relationships: places where one company
replaced another but no successor was registered.** The registered links are
not the target. They are known-correct matches. The work uses them to tune the
matcher and to measure how much it finds, then sweeps for look-alike
successions that no registered link explains.

## What the research found

The research ran on 2026-11-26 against live pages and API probes. Nothing was
bulk-downloaded.

| Source            | Access                                     | Size                                                                                                       | Truth it carries                                                                                          |
| ----------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| FDIC BankFind API | `api.fdic.gov`, no key, CSV or JSON        | 27,838 institutions; 585,908 history events; 4,117 failures                                                | Dated outgoing-to-surviving pairs with names and addresses on both sides; 18,395 events of code 223 alone |
| GLEIF Golden Copy | CC0, no login, published three times daily | Level 1 3,507,492 LEI records (CSV 488 MB); Level 2 relationships 494,045; 366,332 with a US legal address | `successorEntity` on inactive records; direct and ultimate parents                                        |

**Most registered successions are absorptions, not reappearances.** A sample of
42 inactive GLEIF records naming a successor was fetched with both sides from
the API, taking the first pages rather than a random sample:

- In 4 of 42, the successor was created in or after the year the predecessor
  ended.
- In the other 38, the predecessor was absorbed into an entity that already
  existed.
- 16 of 42 had a name token overlap of at least 0.5.
- 25 of 42 shared a legal-address city.

Every FDIC merger is an absorption, because the survivor is an existing bank.

**Registered links are sparse.** In the same sample, about 13% of inactive
GLEIF records name a successor, and about 6% of US inactive records. That
suggests roughly 30,000 links worldwide and roughly 1,000 in the US. Both
figures are estimates from a non-random sample and must be measured on the
loaded index.

Full research notes are in the shared notes folder for this work, outside the
repo.

## Approaches considered

The source was chosen among three options:

1. **GLEIF + FDIC, declared successions as known matches.** Chosen. It is the
   only option that yields measured recall, and with FDIC also an estimate of
   precision, so it improves every project and not just this one.
2. **Connecticut business registry.** About 1.31M businesses and 1.81M
   principals, public domain. It would test matching sets of people (officers
   of a dissolved company reappearing on a new one). It is deferred as the
   natural follow-on, because it has no known matches and so cannot calibrate
   anything.
3. **SEC investment-adviser data.** IAPD XML feeds, plus Form ADV and ADV-W
   withdrawals. FINRA BrokerCheck itself is unusable: it has no bulk file, and
   its terms require a redistributed copy to be kept current. This option needs
   XML loading and firm history stitched together from three sources. People
   carry exact CRD numbers, so only the firm side would test fuzzy matching.
   Deferred.

Within the chosen option, the kind of succession to cover:

- **Reappearance only.** A new entity is created near the old one's end. This
  fits the existing lifecycle sweep unchanged, but it covers only about 10% of
  the known matches.
- **Both, reported apart.** Chosen. Reappearance runs as it does today. Absorption is
  a second sweep in which the candidate already existed and was still active
  when the predecessor ended. Absorption is where nearly all the known matches
  are. It needs new code, and recall on it is expected to be low, because
  acquirers often have different names and headquarters. Measuring that gap is
  part of the result.

## Goals

1. **Two new projects, one per source.**

   - **`FDIC-Banks`** (call; the name is the builder's to change).
   - **`GLEIF-Entities`** (call; likewise).

   They share no key, and a fuzzy join between them would be a separate
   problem, so each stays self-contained. FDIC is built first: it is small, all
   US, and fits the existing English and US analyzers, so the new machinery is
   tuned there before facing GLEIF.

2. **Two sweeps per project, reported apart, split by a settable overlap
   window.**

   - **Reappearance:** the candidate registered within a configured window
     around the predecessor's shutdown. The window runs from `overlap_days`
     before the shutdown to `max_gap_days` after it.
   - The overlap is the point: an operator who expects trouble may form the
     new company first and move the assets across, so there is no downtime
     between the two. A successor created during the overlap is a
     reappearance, not an absorption. It counts as full evidence, not
     discounted evidence.
   - **Absorption:** the candidate registered before the overlap window began
     and had not itself ended by the shutdown. In other words, an established
     company took the business over.
   - The two windows meet at `overlap_days` and do not overlap each other, so
     every pair belongs to exactly one sweep.

   Each sweep writes its own pair index and its own metrics. `overlap_days` is
   chosen on the tuning half from the measured gaps of truth links (see
   Framework change 1), not guessed.

3. **Every pair is tagged by what explains it, never dropped for being
   explained.** A registered link plays one of two roles, and the two never
   mix:
   - **Truth:** a registered succession.
     - FDIC: the candidate is a recorded survivor or acquirer of the
       predecessor.
     - GLEIF: the candidate is the predecessor's registered successor.
     - Truth links alone form the known-match set. They are the recall
       denominator, the targets of retrieval accounting, and the source of the
       gap distribution.
   - **Explains:** a declared relationship that is not a succession.
     - GLEIF: parent, child, or a shared ultimate parent.
     - FDIC: the same holding company, or reaching the candidate by following
       recorded successions through more than one step (A into B, then B
       into C, so A pairs with C).
     - An "explains" pair is not a finding. It is not counted toward recall,
       and it is not counted as a false match in FDIC precision. It is
       reported as its own band.
     - Parents and siblings can number hundreds per entity. Counting them as
       truth would make recall mostly a measure of sibling retrieval.
   - Each pair carries `declared: truth | explains | none` and the name of the
     link that fired. Truth pairs are how recall is measured, so dropping them
     would destroy the measurement.
   - Pairs tagged `none` are the findings.
4. **Measured recall for each project and mode, with the denominator stated.**
   - The denominator is the set of truth links whose predecessor is in the
     swept population and whose target exists in the index.
   - Each truth link the sweep missed is classified as either not retrieved
     as a candidate or retrieved but not emitted. Retrieval and scoring are
     separate failures in this codebase, and one recall number would hide
     which one is at fault.
5. **Estimated precision for FDIC.** FDIC's history is treated as complete for
   successions between insured institutions, so an undeclared FDIC pair is
   presumed to be a false match.
   - This is an assumption. The builder verifies it by hand-checking a sample
     of high-scoring undeclared FDIC pairs before the precision figure is
     written down.
   - The known exception is a holding-company acquisition in which the bank
     keeps its own charter. That is not a succession, and the matcher may
     still pair it. Same-holding-company pairs and multi-step chains are
     tagged `explains` (Goal 3), so they leave the precision figure rather
     than counting as false matches.
6. **A tuning set kept apart from the report set.**
   - Truth links are split in half by a stable hash of the predecessor key.
   - Weights and thresholds are tuned on one half, and recall and precision
     are reported on the other.
   - Without this split, the published figures would describe how well the
     matcher was fitted, not how well it works.
7. **A first list of undeclared GLEIF successions** among US inactive entities,
   reported by score band rather than above one threshold (call). It is kept
   local. Only anonymized aggregates and examples reach the repo (see Naming).
   - GLEIF has no precision measure, and recall only rises as a threshold
     drops, so any single cut-off would be arbitrary.
   - Instead each band reports its count of undeclared pairs, its share of
     truth pairs, and a hand-reviewed sample with how many look real.
   - The project README names the lowest band worth a reviewer's time,
     argued from those figures. That plays the role the carrier project's
     "strong band" plays.

## Non-goals

- **A worldwide GLEIF population.** The first cut sweeps entities with a US
  legal address and draws candidates from the whole index. Multilingual names,
  transliteration and foreign legal-form suffixes come later, as a population
  change plus analyzer work.
- **Matching sets of people**, as in officers or principals. That is the
  Connecticut follow-on.
- **Fitting weights automatically.** Tuning stays manual; this work gives it a
  number to tune against.
- **FDIC branch locations.** An acquirer operating a branch at the
  predecessor's old head-office address is strong absorption evidence, but it
  is a separate dataset and a separate signal. It is recorded as the first
  follow-up if absorption recall is poor.
- **Joining FDIC to GLEIF.**
- **A blog article.** One may follow from the results; this work does not
  write it.

## Anti-goals

These would count as failure even if everything ran:

- **A recall figure computed over truth links the sweep could never reach,
  without saying so.** Examples are a successor missing from the index, a
  predecessor outside the population, or a successor filtered out by the
  absorption window. The denominator and its exclusions are always printed
  with the figure.
- **A declared tag that silently never fires.** A wrong field path would make
  every pair read as undeclared and recall read as zero. That looks like a bad
  matcher when it is really bad configuration: this repo's recurring "logged
  success, quietly wrong" failure. The declared-link paths must be checked
  against the live mapping by `validate`, and a sweep in which no pair is
  tagged truth while truth links exist must say so loudly.
- **Publishing an undeclared match with a real name.**
- **Tuning and reporting on the same links.**

## Constraints

- Everything in `CLAUDE.md` applies:
  - `.venv` only.
  - `ruff` and the full `pytest` suite pass before each commit.
  - Elasticsearch calls use explicit keyword arguments.
  - Configuration is accessed as `SimpleNamespace`.
  - Each new guard gets a test whose module docstring names the failure, plus
    an entry in the README hazard or re-run table.
- Pinned Elasticsearch 9.4.1 on the local Colima cluster. Read the
  `run-entitopia` skill's re-run table before any sweep against a loaded
  cluster.
- Downloads land in `<project>/data/<step>/`, which `.gitignore` already
  excludes. Fetch scripts live with their project, as
  `DOT-Commercial/fetch_commercial_carriers.py` does.
- Profile before configuring (`scripts/profile_dataset.py`,
  `scripts/check_mapping_coverage.py`).
  - Column names below are from API probes and memory, not from the files.
    The CMS project shipped three inert analyzers by configuring against
    remembered column names.
  - Every column named in configuration comes from the profiled file.
- FDIC terms: none found; it is a US government work. GLEIF: CC0.

## Design

### Data

**FDIC-Banks**

| Step                 | Source                                     | Role                                                                                                                                                                                |
| -------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `history`            | BankFind history events                    | Known links. One row per event, with the outgoing certificate number, the surviving or acquiring certificate number, the change code and the effective date.                        |
| `institutions-setup` | —                                          | Enrich policy keyed on the outgoing certificate number, carrying each institution's recorded successors onto it.                                                                    |
| `institutions`       | BankFind institutions, active and inactive | The swept corpus. Key: certificate number. Fields: name, address, city, state, ZIP, established date, end date, holding-company identifiers. Enriched with its recorded successors. |
| `bank-reappearance`  | —                                          | Sweep, reappearance mode                                                                                                                                                            |
| `bank-absorption`    | —                                          | Sweep, absorption mode                                                                                                                                                              |

Which FDIC change codes count as a succession (merger, failure with acquirer,
and so on) is decided from FDIC's code list. The list goes in the project
README, with how many events each code contributed.

**GLEIF-Entities**

| Step               | Source                       | Role                                                                                                                                                                                                                 |
| ------------------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `relationships`    | Level 2 relationship CSV     | Parent links: direct and ultimate                                                                                                                                                                                    |
| `entities-setup`   | —                            | Enrich policy carrying each LEI's direct and ultimate parent onto it                                                                                                                                                 |
| `entities`         | Level 1 LEI CSV, all records | The swept corpus. Key: LEI. Fields: legal name, other names, legal and headquarters addresses, category, legal form, status, creation date, end or expiration date, registered successor LEI. Enriched with parents. |
| `lei-reappearance` | —                            | Sweep, reappearance mode                                                                                                                                                                                             |
| `lei-absorption`   | —                            | Sweep, absorption mode                                                                                                                                                                                               |

**Dates.**

- `registration_date` is the entity's legal creation date, not its LEI
  registration date. An LEI is often issued years after the entity was formed,
  so the LEI registration date would put old entities in a reappearance
  window.
- `shutdown_date` is the end or expiration date, or the effective date of the
  dissolution, merger or liquidation event, whichever the profiled file
  supplies.
- If creation dates are mostly blank, the builder stops and reports back
  rather than substituting the LEI registration date.

**Population (call).**

- GLEIF: inactive entities with a US legal address, excluding the `FUND` and
  `BRANCH` categories.
- FDIC: every institution with an end date.
- Funds are excluded because umbrella funds carry near-identical numbered
  names ("… Fund I", "… Fund II"). That is inferred, not measured. Profile the
  category distribution first. If funds are not a large share, drop the
  exclusion and say so in the README.

### Framework changes

Each change is generic, carries no vocabulary from either dataset, and is
driven from `entity-match.json`.

1. **A settable overlap, and the absorption window.**
   - **Today the overlap is half hard-coded.**
     - The scorer's gate already takes a settable lower bound:
       `scoring.min_gap_days`, set to `-180` for carriers.
     - The `temporal` signal, however, scores a successor registered before
       the shutdown against a separate constant, `BACKWARD_WINDOW_DAYS = 180`
       in `matching/signals.py`. It also halves that score with
       `BACKWARD_SCALE = 0.5`, on the view that a pre-positioned company is
       weaker evidence.
     - Nothing checks that the constant and the configured bound agree.
       Changing the window in configuration would leave the signal scoring
       against the old 180 days.
   - **Change:** the signal reads its backward window from the same
     configured lower bound the gate and the candidate filter use, and the
     constant goes. The discount for registering before the shutdown becomes
     a setting on the `temporal` signal, defaulting to today's 0.5, so
     DOT-Commercial's sweep is unchanged. These projects set it to 1.0, the
     no-downtime case.
   - **A guard test** pins that changing the configured window moves the
     signal's scoring with it. This is the "derived once, used everywhere"
     pattern the candidate window already follows.
   - **Choosing `overlap_days` (call):**
     - The measurement script reports the distribution of gaps between truth
       links' predecessor end and successor creation dates on the tuning half.
     - The distribution is expected to be bimodal. Absorptions have
       successors created years or decades earlier; reappearances sit near
       the shutdown.
     - `overlap_days` goes in the valley between the two modes, not at a
       coverage percentile. A rule like "cover 90% of links" would stretch the
       overlap across decades and fold absorptions into the reappearance
       sweep.
     - It is set per project. If a project has too few reappearance-shaped
       truth links to show a valley (FDIC may have only de novo or bridge-bank
       acquirers), it uses the carrier project's 180 days, and the README says
       so.
     - The value is recorded in the project README with the figures behind
       it, and stays a setting the operator can change.
   - **The absorption window:**
     - The candidate must have registered before the overlap window began
       (`max_gap_days` set to the negative of `overlap_days`, minus one day,
       with no `min_gap_days`). The existing window filter already expresses
       this. The builder may replace the minus-one arithmetic with a clearer
       derived setting, as long as the two sweeps' windows are derived from
       one `overlap_days` value and cannot drift apart.
     - The candidate must also not have ended before the predecessor's
       shutdown. That requires a new optional `lifecycle.end_date` path, the
       candidate's own end, and a candidate filter that keeps candidates whose
       end date is blank or later than the shutdown.
     - The same "not ended" rule applies in the reappearance sweep, since a
       successor formed during the overlap that closed before the predecessor
       did took nothing over.
     - The scorer applies the same rule as a gate, so retrieval and scoring
       cannot disagree.
   - **Missing dates (call).** The existing windows keep a candidate whose
     registration date is missing, because "not evaluable" is not "outside".
     Reused unchanged, both sweeps would emit every undated candidate, which
     breaks "every pair belongs to exactly one sweep" and double-counts
     recall.
     - So the window gains a setting that decides whether undated candidates
       are kept.
     - The reappearance sweep excludes them: a reappearance is a claim about
       timing, and there is no timing to claim.
     - The absorption sweep keeps them, tagged `registration_date: unknown`,
       and reports them as their own band.
     - A truth link whose successor has no creation date counts in the
       absorption denominator only.
     - The measurement script prints how many of each there were.
     - DOT-Commercial's sweep keeps today's behaviour by default.
   - **The absorption sweep has no `temporal` signal.** That an established
     company was alive at the time proves little.
2. **Candidate filters (call).**
   - An optional `candidates.filter` reuses the population's closed clause
     menu (`nested-exists`, `term`, `all`, `any`) and applies it to the
     candidate query. Its first use is excluding funds and branches from GLEIF
     candidates.
   - It is the same vocabulary, not a free query, for the reason
     `matching/population.py` gives.
   - If profiling shows funds are not a problem, this change is dropped.
3. **Declared links.** A new optional `declared_links` block in
   `entity-match.json`. Each rule names its role, `truth` or `explains`
   (Goal 3). The rules come in two kinds:

   - **Key links:** predecessor field paths whose values name a candidate's
     entity key, such as recorded successor certificate numbers, the successor
     LEI, the parent LEIs and, for children, the candidate's own parent
     fields.
   - **Shared-value links:** field paths whose values, when shared, declare
     the pair related, such as a shared ultimate parent.

   Each emitted pair carries `declared: truth | explains | none` and the name
   of the link that fired. When a pair matches several rules, `truth` wins.
   FDIC's multi-step chains need the recorded successions followed transitively
   before tagging. Doing that at load time (an enriched list of every eventual
   successor) or at sweep time is left to the builder. The `validate` phase checks every declared-link path against
   the live mapping, as it does for signal fields.

4. **Retrieval accounting for declared links.**
   - For each predecessor with a truth link, the sweep records whether each
     truth target was among its candidates, and whether it was emitted.
   - This goes into the run's summary output; whether that is a document in
     the pair index or a log line plus a sidecar is left to the builder.
   - If truth links exist in the population but none were retrieved, the sweep
     fails. The tag is either misconfigured or the search cannot reach the
     targets, and either way the run's numbers mean nothing.
5. **A measurement script**, `scripts/measure_declared_links.py` (call). From a
   pair index and the project configuration it reports:
   - Recall: truth pairs emitted over reachable truth links, overall and by
     score band, on the report half.
   - Misses split into not retrieved and retrieved but not emitted.
   - For a project marked complete-truth (FDIC only), precision by score band
     against undeclared pairs.
   - It prints the denominator and every exclusion.
   - Its output is plain numbers safe to commit, with no entity names.
6. **The tuning and report split.** A stable hash of the predecessor key
   assigns each truth link to `tune` or `report`. The script takes the half as
   an argument. Tuning reads `tune` only.

Each of the six gets a test written first, with a module docstring naming the
silent failure it prevents:

- an overlap window the `temporal` signal ignores;
- reappearance and absorption windows that overlap or leave a gap;
- a window that admits ended candidates;
- a filter clause that is ignored;
- a declared path that never fires;
- recall over an unreachable denominator;
- hash instability between runs.

### Signals and analyzers

The starting point is DOT-Commercial's name and address analyzers and signal
set, minus what these datasets lack:

- No phones on FDIC.
- GLEIF has no phones or email.
- Neither has VINs or agents.

Name matching follows the measured process in
[adding a dataset](../../adding-a-dataset.md):

1. Choose the encoder with `compare_phonetic_encoders.py`.
2. Stop generic words by measured frequency (`BANK`, `NATIONAL`, `TRUST`,
   `SAVINGS` for FDIC; legal-form suffixes for GLEIF).
3. Leave `fuzzy_edits` off unless a pair-id comparison justifies it.

`other names` and FDIC's former names, if the history carries them, belong in
the name fields.

### Naming (an addition to `CLAUDE.md`)

`CLAUDE.md` forbids naming a flagged entity. This work adds one sanctioned
case, recorded in the same change that first commits an example:

- **Entities in a declared succession may be named.** An FDIC-recorded merger
  or a GLEIF-registered successor is the registrar's own public record of a
  lawful event, not a matcher's suspicion.
- **Every undeclared pair is anonymized**, in both projects. An undeclared
  GLEIF match is at most a reporting gap. Still, saying a company quietly
  replaced another reads as an allegation. An undeclared FDIC pair is
  presumed a matcher error, and naming it serves nothing.

The `anonymization-check` skill is updated to know the declared/undeclared
distinction.

## Success looks like

- Both projects load. `validate` passes. The README for each records the
  profiled cardinalities, the chosen key, the change codes or columns used,
  and any signal rejected with the reason.
- For each project and each mode, the report half has:
  - a recall figure with its denominator;
  - the split of misses into not retrieved and not emitted;
  - for FDIC, a precision estimate with the hand-checked sample size behind
    it.
- An anonymized count of undeclared GLEIF US successions above the tuned
  threshold, with a hand-reviewed sample count of how many look real.
- Every new guard has its test and its README entry. `ruff` and `pytest` are
  clean.

## Left to the builder

- Exact column names and date fields, chosen after profiling.
- FDIC API paging and fetch-script shape, and the GLEIF download URL (resolved
  at run time, as the CMS download script does, not hardcoded).
- Analyzer details, signal weights, thresholds, `max_candidates`, and
  `ignore_values`, all tuned on the `tune` half.
- Where retrieval accounting is written.
- The final project and step names.
- Whether `declared` should also feed `scripts/run_metrics.py` predicates, so
  metrics can count declared and undeclared pairs from `metrics.json`.
