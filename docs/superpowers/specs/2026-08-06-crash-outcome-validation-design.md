# Validating the chameleon score against crash outcomes

Date: 2026-08-06
Status: proposed, not yet implemented

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

In scope: measuring whether `total_score` predicts severe crashes, and
recording the result so it is re-derivable.

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

The sweep emitted 421,846 pairs covering approximately **249,549 distinct
successors** (measured 2026-08-06). A carrier appearing in forty pairs would
otherwise contribute forty times to a rate, weighting the result by how many
shut-down carriers happened to resemble it rather than by anything about the
carrier.

Each successor is therefore reduced to its **maximum** `total_score` across the
pairs naming it. A crash outcome attaches to a company, not to a hypothesis
about one.

### Outcome variable

A **severe crash** is `fatalities > 0 OR injuries > 0 OR tow_away = 'Y'`.

This is FMCSA's own reportable-crash definition rather than a threshold invented
here, which is what makes the resulting rate comparable to GAO's 18% / 6%
instead of merely resembling it. All three fields are present on the crashes
index and were confirmed populated: of 333,120 crash records, roughly 58,831
distinct carriers have at least one severe crash.

`crashes.dot_number` and `chameleon-candidates.successor.dot_number` are both
mapped `keyword`, so the join needs no coercion.

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

Bin successors by max score; report the share with at least one severe crash per
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
