# Validating the chameleon score

Primary: does the score find chameleon-shaped pairs? Secondary: does the
flagged population show the elevated crash rate GAO measured?

The title of this document said "against crash outcomes" for its first day,
which was the framing error corrected in Scope below.

Date: 2026-08-06
Status: implemented 2026-08-08 (plan:
`docs/superpowers/plans/2026-08-06-crash-outcome-validation.md`). Both results
were null; see `DOT-Commercial/README.md`'s calibration open item for the
measured figures and the ranked list of candidate fixes.

## Problem

`DOT-Commercial/README.md` has said from the beginning that `total_score` is
**uncalibrated confidence, not probability**, and that calibrating it needs
labelled FMCSA enforcement outcomes the project does not have. That is still
true. But it has quietly justified a weaker position than it needs to: nothing
in the repo demonstrates the score carries _any_ real-world signal at all.

Every number currently committed is internal. Pair counts, score bands, the
triage set — all of them describe what the matcher did, none of them describe
whether the matcher was right. A scorer that ranked carriers by ZIP code would
produce an equally clean set of figures.

The gap matters because the open items already propose acting on the ranking
("treat the ranking as triage order"). Triage order is a claim about the world.

### There is an external reference, and it does not need labelled data

[GAO-12-364](https://www.gao.gov/products/gao-12-364) examined FMCSA new-applicant
registrations for what it called _chameleon attributes_: registration
information matching a previously registered carrier, where that prior carrier
had motive to evade — a history of safety violations or enforcement. It found
**759 such carriers in 2005 rising to 1,136 in 2010**, and measured the outcome
that matters:

> 18% of applicants with chameleon attributes were involved in severe crashes,
> compared with 6% of new applicants without them.

That is a threefold lift against an independent outcome. It is reproducible in
shape here because **the crash data is already loaded** and the matcher never
sees it: no signal in `entity-match.json` reads crash severity, so a crash
outcome is genuinely external evidence rather than a restatement of the score.

Two other public anchors bound what "correct" means:

- **49 CFR 386.73** (effective 2012-05-29) is the legal definition — operating
  or attempting to operate under a new identity to avoid compliance obligations
  or to avoid being linked to negative compliance history. Worth aligning
  vocabulary to, since it is the standard an operator would be judged against.
- **FMCSA's own vetting tool (ARCHI)** scores shared identifiers across
  registrations: company **and officer** names, physical and mailing addresses,
  phones, **EINs and DUNS numbers**.

The ARCHI field list states this project's accuracy ceiling plainly. The carrier
census loaded here carries no officer name, no EIN and no DUNS — verified
against a live carrier document. Three of the identifiers the government's own
matcher relies on are simply absent from the input. No analyzer tuning recovers
them, and any accuracy result here should be read against that ceiling rather
than as a verdict on soft matching in general.

## Scope

In scope: measuring whether `total_score` finds chameleon-shaped pairs, and
whether it predicts severe crashes, and recording both so they are
re-derivable.

**The two are not the same question, and an earlier revision of this spec
conflated them.** Crash involvement is a proxy GAO used because chameleon
carriers matter to regulators for safety reasons. It is not the definition of
one. A chameleon that never crashes is still a chameleon; a carrier that
crashes constantly and has never changed identity is not one. Treating a crash
lift as a verdict on matching accuracy overstates what it can establish, and
that error is corrected here rather than quietly edited out.

The direct measure is structural and needs no proxy — see "Does the top tier
look like chameleons?" below. The crash lift is retained as a **secondary**
result, because it is a published external yardstick and because the harness
built for it (banding, exposure normalization, standardization, persistence)
applies to any outcome variable.

Out of scope, and each for a reason:

- **Turning the score into a probability.** That needs labelled enforcement
  outcomes. A crash lift shows the ranking is not noise; it does not tell you
  what 0.9 means.
- **Measuring recall.** With no known-positive list, a chameleon the sweep never
  surfaced is invisible to every method here. This measures precision-shaped
  properties only, and must not be described as accuracy without qualification.
- **Acquiring officer/EIN/DUNS data.** Real, and the largest single accuracy
  lever available, but it is a data-sourcing project rather than a validation
  one.
- **Changing any scoring config.** This measures the shipped scorer. Tuning
  weights in the same change would destroy the baseline being established.

## Design

### Unit of analysis: the successor carrier, not the pair

The sweep emitted 421,846 pairs covering exactly **249,778 distinct
successors** (measured 2026-08-06). A carrier appearing in forty pairs would
otherwise contribute forty times to a rate, weighting the result by how many
shut-down carriers happened to resemble it rather than by anything about the
carrier.

Each successor is therefore reduced to its **maximum** `total_score` across the
pairs naming it. A crash outcome attaches to a company, not to a hypothesis
about one.

### Outcome variable

**A carrier's outcome is whether it appears in the crashes index at all**, with
`report_date` after its `add_date`. No severity predicate is applied.

That is not a shortcut. The FMCSA crash file already contains only _reportable_
crashes — those involving a fatality, an injury requiring treatment away from the
scene, or a vehicle towed away. Measured on the loaded data, **333,120 of 333,122
records satisfy `fatalities > 0 OR injuries > 0 OR tow_away = 'Y'`: 99.9994%.**
Applying that predicate would filter nothing while creating the impression of a
severity threshold that was never really applied. Presence in the file _is_ the
severe-crash outcome, and it is what makes this comparable to GAO's 18% / 6%.

The base rate supports the reading. Exactly **122,258 distinct carriers of
2,085,534 have at least one crash — 5.86%** — against the **6%** GAO measured for
new applicants without chameleon attributes. Two different populations over two
different decades landing that close is a strong sign the outcome variable is
the same one.

A stricter tier is available and reported as a secondary column, because
presence-in-file is dominated by tow-aways: **8,541 records involve a fatality
and 125,080 an injury, against 309,628 tow-aways.** An injury-or-fatality
outcome is a materially harder test and is worth reporting beside the headline,
but it is not the headline, because GAO's number is not that number.

### Two field-type traps, both verified

Both would corrupt the result silently rather than erroring, which is this
codebase's recurring failure mode.

1. **`tow_away` is `text`, not `keyword`.** `{"term": {"tow_away": "Y"}}` matches
   **zero** documents, because the analyzer lowercased the indexed term to `y`
   while the query term stays `Y`. Any severity filter must use
   `tow_away.keyword`. This is the same defect as the `insp_carrier_state_id`
   open item and the `debug-zero-hit-selector` skill eval, arriving a third time.
   The `.keyword` form matches 309,628.

2. **`dot_number` types differ across indexes.** It is `long` on `crashes` but
   `keyword` on `carriers` and on `chameleon-candidates.successor.dot_number`.
   Elasticsearch coerces a numeric-looking string in a `term` query, so both
   spellings happen to work today — but any set-membership logic in Python must
   normalize to `str` on both sides or the intersection silently comes back
   empty. Similarly `phy_state` is `text` and needs `phy_state.keyword` to
   aggregate.

`report_date` is a `long` in `YYYYMMDD` form (e.g. `20240812`), not a date, while
`carriers.add_date` is a real `date`. Comparing them means rendering `add_date`
to a `YYYYMMDD` integer, not parsing `report_date` as a date.

### Exposure: the constraint that shapes the whole measurement

`fetch-config.json` pulls crashes on a rolling 24-month window, so crash
coverage runs roughly 2024-08 to 2026-08. Two distortions follow, and both are
silent if unhandled:

1. A crash **predating** a successor's registration says nothing about the
   successor. Only crashes with `report_date > successor.add_date` count.
2. A carrier registered **inside** the window had less time to crash than one
   registered before it. Comparing raw proportions across them measures
   exposure, not risk.

The script reports **both** views rather than choosing:

- **Restricted cohort (headline).** Successors whose `add_date` precedes the
  window start, so every carrier has the full window of exposure and the
  proportion is directly comparable to GAO's. This is the number to quote.
- **Full set, exposure-normalized (companion).** All successors, with crashes
  per observed month since `add_date`.

Both are reported because the restriction is **not random**: it removes the most
recently registered successors, who are precisely the freshest chameleon
candidates. A headline that silently excluded them would be answering an easier
question than the one being asked. The companion column keeps them visible; the
two must never be quoted as if interchangeable.

The window boundaries are computed from the crashes index at run time, never
hardcoded — the fetch window moves every time the data is refreshed, and a
hardcoded date would keep printing plausible numbers after it drifted.

### Primary measurement: dose–response

Bin successors by max score; report the share appearing in the crash file per
band. A monotonic rise is the evidence sought.

The bin edges are **fixed here, before any run**, and deliberately reuse
thresholds the project already committed to rather than inventing new ones:

```
[0.35, 0.50)   at/above the emit floor, inside the band the README calls noise
[0.50, 0.60)
[0.60, 0.70)
[0.70, 0.80)   at/above the triage threshold
[0.80, 0.90)
[0.90, 1.00]
```

Edges chosen after seeing the outcome are the standard way this analysis
fools its author, so moving them later invalidates the result rather than
refining it. If they must change, the run is a new measurement and says so.

This is the primary measure because it is **internally controlled**: every
carrier in it is already a candidate successor of a shut-down carrier, selected
by the same seed query. No control-group construction is required, so there is
no matching decision for a skeptic to attack.

### The direct measure: does the top tier look like chameleons?

`DOT-Commercial/README.md` defines the target as carriers "shut down for safety
or insurance reasons that reopen under a new DOT number." That is a **temporal
structure**: the successor registers _after_ the predecessor is shut down. It is
checkable directly against the emitted pairs, with no proxy outcome, no labels,
and no external dataset.

Three measurements, all over `gap_days` (successor `add_date` minus predecessor
`shutdown_date`), which the sweep already emits:

1. **Temporal plausibility of the actionable tier.** The `gap_days`
   distribution among pairs scoring ≥ 0.70. A pair whose successor registered
   years before the predecessor's shutdown is not a reincarnation of it.
2. **Score separation.** Mean `total_score` for pre-shutdown against
   post-shutdown pairs. If the scorer captures the chameleon pattern, these
   must differ.
3. **Whether `temporal` earns its weight.** It is a scored signal in
   `entity-match.json`. If plausible and implausible pairs score the same, it is
   either weak or computed in a way that does not penalize a negative gap.

**Measured 2026-08-07, before any change** — these are the baseline the work has
to improve on:

| Successor registered                            | Pairs ≥ 0.70 | Share |
| ----------------------------------------------- | -----------: | ----: |
| more than 180d _before_ shutdown                |          728 | 42.1% |
| 0-180d before shutdown (pre-positioning window) |          161 |  9.3% |
| 0-365d after shutdown (classic reincarnation)   |          435 | 25.2% |
| more than 1 year after shutdown                 |          405 | 23.4% |

**Only 34.5% of the tier an operator would act on is temporally coherent** by
the scorer's own definition. Scores barely separate the populations either:
mean 0.4425 across 306,401 pre-shutdown pairs against 0.4520 across 115,445
post-shutdown pairs, a difference of 0.0095.

**Registering before the shutdown is not disqualifying, and the bands above are
cut on the model's own window rather than an invented one.** An operator who
knows a shutdown is coming can stand up the successor in advance, so a short
pre-window is a real chameleon tactic. `TemporalSignal` already encodes exactly
that: `BACKWARD_WINDOW_DAYS = 180` with `BACKWARD_SCALE = 0.5`, giving a
pre-positioned successor partial credit at half weight. An earlier revision of
this spec called pre-shutdown pairs structurally impossible and treated the
signal's behaviour as a bug hypothesis. Both were wrong, and the 180-day
boundary above replaces an arbitrary three-year one so the measurement is
judged against the design's own claim.

**The real diagnosis is weighting, not the temporal signal.** `temporal` carries
0.05 of the 0.94 total — 5.3%, so it can move a score by at most ~0.053, which
bounds the 0.0095 separation actually observed. The three name signals together
carry 47.8%. Temporal plausibility is therefore a rounding error in a ranking
dominated by name similarity, which is why 42.1% of the top tier sits outside
the modelled window: those pairs score **zero** on temporal and still clear 0.70
on name and address alone. This is independent corroboration of the existing
`DOT-Commercial/README.md` open item that name similarity is effectively
triple-weighted and ranks the wrong pairs highest — a second, sharper piece of
evidence for a defect already recorded, not a new one.

**A caveat that must travel with this result.** 49 CFR 386.73 covers operating
as an _affiliated entity_, not only under a new identity. A high-scoring pair
naming a pre-existing company is therefore not automatically a false positive —
it may be a genuine affiliate, which is a different and still useful finding.
What it is not is _reincarnation_, which is what this project says it hunts.
Beyond 180 days before the shutdown is where that defence runs out.

This measurement is cheap, needs no new data, and is the one that answers "are
we finding chameleon carriers." It is primary; the crash lift is secondary.

### Splitting the dose–response by registration recency

Added after the first run returned a null result, because the headline is
structurally unable to see the signal it most needs to find.

The restricted cohort admits only successors registered **before** the crash
window — which excludes the freshest registrations, and an active chameleon
carrier _is_ a recent registration by definition. A carrier that re-registered
in 2015 and has run quietly since is not what this project hunts. Averaged
against a decade of such carriers, a signal confined to fresh registrations
disappears.

So the score bands are additionally cut by how recently the successor
registered, over the **full** row set rather than the restricted cohort:

```
under-1y    age < 12 months
1-3y        12 <= age < 36 months
3y-plus     age >= 36 months
```

Four properties, each load-bearing:

- **Measured back from the crash-window end, not from today.** The fetch window
  rolls forward every refresh; anchoring to "now" would move a carrier between
  columns between two runs that analyzed identical data.
- **Half-open boundaries**, so the columns partition the population exactly and
  no carrier is counted twice.
- **Crashes per 1,000 exposure-months, not a raw proportion.** Recent cohorts
  have had less time to crash by construction, so comparing raw proportions
  across them measures exposure rather than risk.
- **Every cell prints its carrier count.** The recent high-score cells are the
  small ones — measured at n=148-229 — and a rate over 200 carriers is not the
  claim a rate over 200,000 is. Without the denominator beside it, a 0.00 in
  those cells reads as a finding rather than as an absence of data.

Cohort edges are fixed here, before any run, for the same reason the score band
edges are.

**A known wrinkle in the boundary.** `months_between` divides days by 30.4375,
the average month length, so a non-leap calendar year measures as 11.99 months
rather than 12. A carrier registered exactly one year before the window end
therefore lands in `under-1y` by a single day. This surfaced twice during
implementation and is recorded rather than silently corrected: switching to
exact calendar-month arithmetic is a deliberate change that would move carriers
between cohorts, and it must not be made as a side effect of some other edit.

### Secondary measurement: matched control

To state an absolute lift comparable to GAO's, compare against carriers
**absent from the pair set entirely**, over three strata:

- registration cohort (`add_date` year),
- fleet size band (`nbr_power_unit`: 1, 2-5, 6-20, 21-100, 100+),
- state (`phy_state`).

Combine by **direct standardization** — compute the control rate within each
stratum, then reweight by the flagged population's stratum distribution — rather
than by drawing a matched sample. Standardization is deterministic, so the
number is reproducible without recording a random seed, and it uses every
control carrier instead of discarding most of them. Sampling would add run-to-run
noise to a figure whose whole purpose is to be quoted and re-derived.

Strata containing no control carriers are reported as such and excluded from the
weighted total. Silently dropping them would quietly redefine the comparison
population.

**Do not select controls by paging an ordered field.** This was implemented
once and produced a confidently wrong answer, so it is recorded here rather
than left for the next person to rediscover. Paging carriers by `dot_number`
and stopping at N returns the N _oldest_ registrations, because FMCSA assigns
DOT numbers chronologically — verified: `dot=1` registered 1974-06-01,
`dot=1000000` registered 2002-01-23. Old carriers are established, larger and
higher-mileage, so they crash more. The measured result was a **0.70x lift**,
control 9.50% against flagged 6.64% — the score appearing to _anti_-predict
crashes, when what was actually being measured was company age.

The whole-population form has no such failure mode and needs no sampling at
all, because both sides are reachable by subtraction:

```
control_total[stratum]   = all_carriers_total[stratum] - flagged_total[stratum]
control_crashed[stratum] = all_crashed_total[stratum]  - flagged_crashed[stratum]
```

The crashed side is cheap despite covering every carrier: only carriers that
appear in the crash file can contribute, and there are 122,258 of those out of
2,085,534.

**Sanity check with teeth.** The unflagged control rate before standardization
should land near the measured 5.86% base rate. A control rate far from it means
the control population is wrong, and that must be reported rather than
published — the biased run above would have passed every test in this spec
while inverting its conclusion.

### Placebo

Re-run the banding with scores randomly permuted across the same successors. The
result must be flat. Without it, a monotonic trend cannot be distinguished from
an artifact of where the bin edges were cut, and bin edges chosen after seeing
the data are exactly how this kind of analysis fools its author.

### The confounder that must be reported either way

**Fleet size drives crashes**, because more trucks mean more miles. If
high-scoring successors skew larger, the lift is confounded and means nothing.
Two guards: the control matches on size band, and every band reports crashes per
power unit alongside the raw proportion.

If the effect appears only in the unnormalized proportion, the script says so in
its output. A validation that hides its own confounder is worse than none,
because it converts an open question into false confidence.

## Testing

- **Unit, no Elasticsearch:** banding, max-score-per-successor reduction,
  exposure-month arithmetic and rate computation, against synthetic inputs.
  Includes a successor appearing in several pairs (must count once, at its
  highest score) and a crash predating registration (must not count).
- **Integration, skipped when the cluster is unreachable**, following
  `tests/test_street_analysis.py`: the join returns non-empty against the live
  indexes, and the restricted cohort is a strict subset of the full set.
- **The placebo is itself a test of the method.** If a permuted-score run does
  not come out flat, the banding is wrong and no result from it is publishable.
- `.venv/bin/python -m ruff check .` prints `All checks passed!`.

### Persisting each run

Each run writes its rows to a date-stamped `chameleon-validation-{now/d}-000001`
behind the alias `chameleon-validation-000001`, the same shape every other step
in this project uses.

Printing to stdout alone would reproduce the failure this spec's Documentation
section warns about. The "roughly 195 pairs" figure in `DOT-Commercial/README.md`
became unreproducible precisely because the run behind it was gone, and the
controlled before/after comparison that resolved it was only possible because
three earlier sweeps happened to still exist as indexes — luck, not design.

Every row carries the `analysis_fingerprint` of the carriers index it measured,
so a stored result is tied to the token universe that produced it instead of
being matched to a run by timestamp afterwards. A run is retrievable as a unit
by `run_id`.

Every field on that index is **pinned**, never left to dynamic inference. A
`rate` field that inferred `long` from a first value of `0` would silently
truncate every subsequent rate to an integer — the same class of defect as the
`tow_away` trap above, which is what dynamic inference already cost this
project once.

`placebo_is_flat` is stored but never set by the script. Whether the placebo
passed is a judgment made by reading the table; code that asserted its own
placebo had passed would defeat the reason for having one.

## Documentation

`scripts/measure_crash_lift.py`, alongside `measure_address_analyzers.py`, which
established the pattern of a committed measurement script whose output is quoted
in a README.

Its measured output goes into the `DOT-Commercial/README.md` calibration open
item **with the exact filters that produced it**. This session established why
that matters: figures recorded without their query cost hours to reconstruct,
and three separately-quoted numbers turned out to be two filters over two runs.

The result is recorded whichever way it comes out. A flat dose–response curve is
a publishable finding — it would mean the shipped weighting does not rank real
risk, which is more actionable than any of the counts currently committed.

## Risks

- **A null result is a real possibility**, and the design must not be revised
  after seeing the numbers to hunt for a positive one. Bin edges, the severe
  crash definition and the control-matching variables are fixed by this spec
  before the first run for that reason.
- **Crash reporting varies by state**, so state is a control-matching variable
  rather than an assumed-neutral one.
- **Survivorship.** A successor shut down again early accrues little exposure
  and few crashes, which biases _against_ finding a lift. Worth stating if the
  measured effect is weak, and not worth correcting for speculatively.
- **The 24-month window will move.** Anything quoted from this is
  point-in-time, in the same way every other figure in `DOT-Commercial` is, and
  carries its measurement date.
- **No officer, EIN or DUNS data**, per the ceiling described above. A weak lift
  is at least as likely to reflect missing identifiers as a flawed scorer, and
  the write-up should not attribute it to the scorer alone.
