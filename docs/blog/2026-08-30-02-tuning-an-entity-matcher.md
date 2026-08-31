# Tuning an entity matcher: the knobs that actually moved recall

## Retrieval and scoring are different problems

An entity matcher has two stages, and only one of them sets your ceiling.

**Retrieval** — classically called blocking — narrows the corpus to the handful of
records worth comparing against a given one. You cannot compare everything to
everything: two million records is two trillion pairs.

**Scoring** takes one retrieved pair and decides whether it is a match.

Every knob you have sits in one stage or the other, and the asymmetry matters: **a pair
that retrieval never returns cannot be scored at any threshold.** Lowering the floor,
reweighting a signal, adding a signal — none of it reaches a record the candidate query
did not return.

Most tuning effort goes into scoring, because that is where the numbers are and where
the output is legible. In this project the changes that moved results were nearly all
in retrieval and in exclusion. The weights moved almost nothing.

_Every figure below is a point-in-time measurement against one extract of one public
dataset — evidence that a problem was real and roughly how big it was, not a value to
expect on your own data._

## `None` is not `0.0`

A signal compares one attribute across a pair and returns a number in [0, 1] — or
`None`. `None` means _not evaluable_: the data is missing on one side or both. `0.0`
means _compared, and they disagree_.

Conflate them and you penalise a record for having sparse data, which is backwards.
Blank must never match blank, and blank must not count against either side.

The mechanism is renormalization. The total is the weighted mean over the signals that
were actually evaluable, divided by _their_ summed weight rather than by the weight of
everything configured. A pair with three of eight signals evaluable is scored on those
three, and missing data neither helps nor hurts.

## Blend the set math; do not pick one

Names become token sets, so you need a similarity measure over sets. The two obvious
ones are both wrong, in opposite directions.

Jaccard — intersection over union — punishes a subset. Containment — intersection over
the smaller set — treats any subset as perfect.

Abbreviation is a deliberate evasion: dropping a word is the cheapest way to look
different while staying recognisable. Jaccard penalises exactly that. But containment
scores a single shared token as a total match.

The blend is half of each. `{SM0, LLC}` against `{SM0, TRKN, LLC}` gives jaccard 0.67
and containment 1.0, so 0.83 blended — above a partial match, below identity. Full
overlap still ranks above a subset, and abbreviation still scores highly.

## A formula with no discriminating power

One signal weights a shared filing agent by how rare that agent is. The original
specification computed rarity as `1 - count/N`. That formula cannot work on this data,
and the reason generalises.

The corpus holds 89 distinct agents across 1.43M filings, and the largest covers 9.4%
of them. So `1 - share` maps every agent into `[0.906, 1.0]`. The most common agent in
the corpus and the rarest differ by less than a tenth. The signal returns "very rare"
for everything and discriminates nothing.

Normalized inverse document frequency — `log(N/count) / log(N)` — spreads the same
population across `[0.167, 1.0]`.

The bug surfaced because the specification's formula and its own test expectations
could not both be true: it asserted that a dominant filer would score about 0.07, while
`1 - 0.094` is 0.906. A spec precise enough to contradict itself is worth more than one
vague enough to stay consistent.

## Seeding sets the recall ceiling

Retrieval here is not strict blocking but a relevance-ranked query. Each signal
contributes clauses built from the record you are searching from, they combine into one
`bool.should`, and the result is capped. Which signals may contribute clauses is a
config list, held separately from their weights — and it is the most consequential
setting in the project.

It fails in two opposite directions, and both failures are real.

**Seed on a common value and you flood the candidate set.** The filing-agent field is
shared by roughly 7% of random pairs. Seeding on it returns essentially arbitrary
records and buries the real ones beneath the candidate cap. It is deliberately excluded
from the seed list while still scoring pairs.

**Decline to seed on a rare, decisive one and you cap recall at zero for an entire
class of match.** Shared vehicle identifiers were scored but not seeded. A record that
changed its name, its address and its phone while keeping the same physical equipment
was therefore unreachable — not low-scoring but _unretrievable_, at any threshold, any
weight, any candidate cap, because the query never returned it. Measured after adding
it to the seed list: 257 records per 400 searched were reachable only that way.

Because those two failures pull in opposite directions, the test is not "is this field
rare enough to rank well." It is: **if two records shared only this field, would you
want to see the pair?** If yes, it has to seed, whatever else it does.

## The null result worth publishing

Raising the candidate cap from 100 to 500 cost four times the runtime and produced
**zero** new top-tier findings.

Relevance ranking had already placed those in the first hundred. What the extra 400
recovered was mid-tier: about 13% more pairs sharing a vehicle identifier, which was
worth keeping but is not what anyone expected to buy.

89% of searches still hit the cap at 500. The truncation is structural — common name
tokens guarantee it — so no reachable cap fixes it. That is an argument for better
seeding, not for a bigger number.

## Averaging cannot say "this one fact settles it"

A weighted mean dilutes any single signal in proportion to how many others exist. That
is usually what you want, and occasionally it is catastrophic.

A shared vehicle identifier is close to decisive: two records naming the same physical
vehicle are operating the same equipment. It carries weight 0.08. A pair sharing _only_
that scores about 0.11 against a floor of 0.35, so every such pair was thrown away.
Measured before the fix: 0 survived out of 3,000 records searched.

The obvious repair is to raise the weight. To clear the floor unaided it would need
roughly 0.46 — at which point it dominates every _other_ pair it touches, and pairs
with genuine corroborating evidence get swamped by one shared vehicle. That trades a
narrow problem for a broad one.

So the fix is not a weight at all. The signal is marked `conclusive`: when it fires,
the pair is reported even if the blended total falls below the floor. It changes only
the pairs where that evidence stands alone.

Note what it does not bypass. The corroboration and identity guards still apply, so a
conclusive signal cannot manufacture a match out of a pair with no other evidence.

## A guard that counted the wrong thing

The corroboration guard requires a pair to fire at least two signals before it is
reported. It was being satisfied by pairs that matched on nothing but a name.

```json
"scoring": {
  "min_total_score": 0.35,
  "min_signals": 2,
  "require_identity_signal": true
}
```

The config lists three name signals — two phonetic encoders and a cleaned-token form —
all reading the same two fields. Three signals, one piece of evidence. Counting
instances let a name-only pair clear a floor whose entire purpose is to demand a
second, independent source.

The fix is structural rather than numeric. Each signal declares an _evidence key_
derived from the fields it reads, and the guard counts distinct keys. Against the real
config, 8 configured signals resolve to 6 evidence sources.

If you deliberately read one field through several signals — and there are good reasons
to — the guard has to know they are one source.

## Values that are shared and still not identifying

Every signal that scores a shared value 1.0 asserts that the value picks out one thing
in the world. Real data breaks that promise two different ways, and only one of them is
a data-quality problem.

**Placeholders.** A vehicle identifier of `GGGG` on 158 records, `UNKNOWN` on 79, a run
of nines on 51. The phone `(000) 000-0000` on 664. Two records that both filed
`UNKNOWN` read as a perfect identity match.

**Values that are entirely correct and still not identifying.** A permit-filing
service, an insurance agency, or a corporate parent puts its own phone or email on
every record it files for. Measured: 200 email addresses each sit on more than 20
records, and the largest sits on 755. Nothing is wrong with the data — the address
really is that record's contact address. It simply cannot establish that two records
are the same operation.

That second kind is why this is a query-time exclusion rather than a data-cleaning
pass. There is nothing to repair. Deleting the value would destroy correct data and the
audit trail with it, and the next extract would bring it straight back.

## Declare what you know, scan for what you don't

Two mechanisms feed one merged set.

A **declared list** in config, keyed by field path, holds the placeholders an operator
can name in advance. Keying by field matters: `0` is not a vehicle identifier, but it
is a perfectly good street number.

A **frequency scan** at the start of every run covers what nobody knew to declare — a
terms aggregation per field asking which values are shared by more than N records. N is
per field, because the right number belongs to the attribute: siblings legitimately
share a phone, while two records sharing a vehicle is already odd. Here it is 5 for
vehicle identifiers and 20 for phone, fax and email.

One implementation detail with teeth. The aggregation's result cap was 200, and exactly
200 email addresses qualified — the scan was sitting precisely at its own ceiling, and
one more shared address would have been dropped silently. Raised to 2,000, it surfaced
661 over-shared values on a single vehicle field, meaning an earlier run had scored 459
non-unique identifiers as identity evidence.

## Retrieval and exclusion must read the same values

If a value can seed retrieval but not score, the sweep fetches candidates it then
refuses to credit. If it can score but not seed, matches exist that are never fetched.
Both read as a quiet recall loss rather than an error, so wire them to the same values
on purpose.

A concrete instance of that class: the seeding code normalised its values — casefolded
them — before querying, while the target fields are mapped `keyword` and store the raw
text. An uppercase identifier queried in lowercase matched nothing. The signal appeared
to seed while contributing zero candidates, which is indistinguishable from "no record
shares this identifier."

Suppression has to reach both halves too. Without it, 94% of the apparent recall gain
from seeding on shared vehicles was placeholder noise.

## What the output actually looks like

A full sweep emitted 429,505 pairs. **81% of them score below 0.50, and that band is
noise.** The reviewable set — re-registered within a year of a shutdown, scoring 0.70
or better, sharing a vehicle or a phone or an email — is roughly 195 pairs.

Be blunt about what the number is: **uncalibrated confidence, not probability.** Nothing
has been fitted against known outcomes, so 0.9 does not mean 90% likely. Converting it
into a probability needs labelled results this project does not have, and until then the
threshold is an analysis convention rather than a decision boundary.

Which is also why the output is a ranked queue for a human, not a verdict. A high score
routinely reflects a shared filing agent, a corporate parent, or a placeholder that
slipped the filter.

## What is still wrong, and why it ships anyway

Names are effectively triple-weighted: three signals over the same two fields carry
0.45 of a 0.94 total.

The measured consequence is backwards. A pair with a byte-identical street, in the same
state, re-registered 45 days after a shutdown scored **0.3483 and was dropped** by the
0.35 floor. A pair sharing one common token in their names, in different states,
sharing nothing else at all, scored **0.5113 and was reported**. A complete name change
is the defining move of the behaviour being hunted, so the matcher is weighted against
its own target.

The structural half of this — the guard that three name signals were fooling — is
fixed. The weighting is not, and it ships that way deliberately. The first full sweep is
also the calibration run, and there is no ground truth to fit against yet. Replacing
0.45 with a different guess before that data exists swaps one unvalidated number for
another, and hides the fact that it was ever a guess.

## What to tune, in order

- **Retrieval first.** Measure what fraction of searches hit the candidate cap, and
  check which signals are allowed to seed. This sets your ceiling; nothing downstream
  can raise it.
- **Exclusion second.** Run a terms aggregation on every field you treat as identity
  evidence and read the head of the distribution before trusting it.
- **Guards third.** Confirm they count independent evidence rather than signal
  instances.
- **Weights last.** They are the most visible knob and they moved the least.
