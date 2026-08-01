# Matching signals

`entity-match` scores candidate pairs across a fixed menu of signal types with configured weights, and writes ranked pairs with per-signal evidence. Configuration selects and weights; it does not express query logic.

## Shape of the sweep

For each **predecessor** (an entity that terminated), retrieve candidate **successors**, score each pair, emit the ones that clear the guards.

Two Elasticsearch round trips per predecessor: one `bool.should` query to retrieve candidates, one `_mtermvectors` call to fetch the **analyzed tokens** for the predecessor and all candidates at once. Scoring needs the tokens Elasticsearch actually produced — phonetic encodings, synonym expansions — and those live in the inverted index rather than `_source`. Calling `_analyze` per string would be one request per field per entity across a sweep of hundreds of thousands.

## The signal menu

| Type               | Reads                                             | Notes                                                                                    |
| ------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `name-phonetic`    | analyzed tokens of a phonetic subfield            | list it **twice** with different `subfield` values to weight the two encoders separately |
| `name-token`       | analyzed tokens of a cleaned subfield             | literal token overlap                                                                    |
| `address`          | exact keyword subfield, then fuzzy token subfield | exact first, fuzzy as fallback                                                           |
| `exact-identifier` | raw `_source` phone/fax/email                     | binary; rejects placeholders                                                             |
| `agent`            | shared low-cardinality identifier                 | rarity-weighted                                                                          |
| `temporal`         | termination date vs creation date                 | the succession signal                                                                    |
| `vin-overlap`      | shared equipment identifiers                      | binary; one shared asset is strong                                                       |

Adding a new signal type means Python, not config. That is deliberate — a raw query DSL in JSON becomes as complex as code while being far harder to test.

## The `None` vs `0.0` distinction

Every signal returns a float in `[0.0, 1.0]` **or nothing at all**.

- **`None` = not evaluable.** Data missing on one or both sides. The signal drops out and the remaining weights renormalize.
- **`0.0` = evaluated, no similarity.** The comparison happened and found nothing.

Conflating them penalizes an entity for having sparse records rather than judging it neutrally. This distinction runs through every signal and through the scorer, and it is the single most important invariant in the matching code. A dataset with 23% blank fields will produce mostly-`None` signals, which is correct behaviour.

The corollary: **blank must never match blank.** Two records that both lack a phone have not agreed on anything.

## Guards

Renormalizing over evaluable signals means a single lucky signal could otherwise reach a perfect score. Three guards bound it:

- **`min_signals`** — how many signals must be evaluable.
- **`min_total_score`** — the score floor.
- **`require_identity_signal`** — at least one name/address/identifier signal must have **actually fired** (score > 0), not merely been evaluable. Temporal proximity alone is meaningless when hundreds of thousands of entities have terminated; a pair needs a real identity link.

That last distinction is subtle and easy to break. A name signal that is _evaluable but scores 0.0_ must not satisfy the identity requirement.

## Calibration traps

### Weights need not sum to 1.0

Unevaluable signals drop out and the total renormalizes, so only the **ratios** matter. A config summing to 0.94 is not a bug.

### Do not triple-count one field

This produced a measurably backwards ranking. Three name signals over the same two fields carried 0.45 of a 0.94 total. Combined with corporate-suffix stripping — which reduces most names to a single token, making set overlap effectively binary — the result:

| Pair  | Reality                                                              | Score               |
| ----- | -------------------------------------------------------------------- | ------------------- |
| A → B | byte-identical street, same state, created 45 days after termination | **0.348 — dropped** |
| C → D | different states, sharing only the token `ABC`                       | **0.511 — emitted** |

A complete name change is the _defining_ move of this fraud pattern, so ranking name above address-plus-timing is exactly wrong.

It also defeats `min_signals`: three arms reading one field count as three signals, so a name-only pair clears a floor written to require corroboration. Either rebalance, or count same-field arms as one signal.

### Rarity weighting: get the formula right

For a low-cardinality shared attribute, weight by rarity — sharing a rare value is meaningful, sharing a dominant one is not.

The obvious formula is wrong. `1 - count/N` looks like rarity and is not: with 89 distinct values whose largest share is 9.4%, it compresses **every** value into `[0.906, 1.0]`, leaving no discriminating power at all — the exact opposite of the point.

Use normalized inverse document frequency:

```text
rarity = log(N / count) / log(N)
```

which spreads the same population across `[0.167, 1.0]`:

```text
largest value (134,283 of 1,426,508)   0.167
mid value      (54,062)                0.231
rare value     (2)                     0.951
unseen value   (0)                     1.000
```

Two implementation notes learned by breaking them: guard `N < 2` or `log(1)/log(1)` divides by zero, and **normalize the lookup key the same way the signal normalizes the value** — a case mismatch makes every lookup miss, every value falls back to "unseen", and the weighting silently switches itself off.

### Timing should be asymmetric

A successor created _before_ the predecessor terminated is a pre-positioned shell — a real tactic, but weaker evidence than one created days after. Score both, weight the backward case lower, and bound its window.

Take the **most recent** termination when an entity has several. Note this can disagree with the filter that selected the predecessor if the object array is not `nested`; see `enrichment.md`.

### Thresholds are guesses until a real sweep

The first full run against production data is also the calibration run. Ship the weights and floor in config so they can be retuned without code changes, and say plainly in the project README that they are uncalibrated. A synthetic end-to-end test proves the wiring works; it proves nothing about ranking.

## Candidate retrieval

Recall is bounded by the seed query — a successor sharing nothing with its predecessor across the seeded fields is invisible. Seed on fields that are actually discriminating.

**Do not seed on a low-cardinality field.** Seeding on a value shared by 9% of the corpus returns essentially random candidates and buries the real ones. Use such a field for corroboration only.

When a candidate query returns exactly `max_candidates`, real matches may have been cut off. Count and warn on that — silent truncation looks identical to "nothing matched".

## Output

Write per-signal contributions, not just a total. A result reading `0.87` is unusable; one reading _name-phonetic 0.9, same street, shared agent, created 34 days after termination_ is triageable. Include a keyword array of which signals fired so results can be faceted.

Make the document `_id` deterministic from the pair so reruns overwrite rather than duplicate.

## Verifying a signal actually fires

A signal can be configured, weighted, and completely dead — because a field path is wrong, a subfield is missing, or an enrichment never carried the data. The test is not that the sweep produces output; it is that **this specific signal appears in a pair's fired list**.

Build a synthetic pair that should trigger exactly one signal, run the sweep, and confirm that signal appears with the expected contribution — `weight × score`, checked against the config. That is how the VIN signal was proven, and it is the only way to distinguish a working signal from a plausible-looking dead one.
