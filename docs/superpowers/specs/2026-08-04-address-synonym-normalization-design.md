# Address synonym normalization

Date: 2026-08-04
Status: approved, not yet implemented

## Problem

`AddressSignal` compares two streets by set overlap over the `.tokens`
subfield, which is produced by the `street_tokens` analyzer. That analyzer
applies `street_suffix_synonyms`, a 16-group bidirectional synonym filter.
Three defects were measured against `carriers-2026.08.01-000001` (2,085,536
documents) and `chameleon-candidates-2026.08.02-000001` (223,500 pairs).

**1. The `P.O. BOX` family cannot match itself.** The standard tokenizer keeps
`P.O` as a single token. `punct_white` is a _token_ filter, so it rewrites the
period in place and emits the literal token `p o`, which can never equal `po`.

```
P.O. BOX 1234  ->  ['p o', 'box', '1234']
PO BOX 1234    ->  ['po',  'box', '1234']
```

43,799 `mailing_street` records use the punctuated form against 193,722 in the
plain form. The `.clean` subfield breaks identically, so the exact path fails
too. Two carriers filing the same box number under different spellings score
0.467 instead of 1.0.

**2. Common abbreviations are absent from the synonym list.** Document counts
for terms with no synonym entry, summed over `phy_street` and `mailing_street`
so a carrier using the term in both fields is counted twice: `route` 24,846
against `rte` 2,027; `unit` 40,967 (not linked to `apt`);
`floor`/`fl`; `bldg` 3,376; `trlr`; `lot`; `sp`/`space`; `pmb` 5,879; `rr`;
`hc`. Also absent: `ne`/`nw`/`se`/`sw`, `pl`, `ter`, `sq`, `expy`, `tpke`.

**3. Bidirectional expansion over-weights the least identifying token.**
`containment(a, b) = |a ∩ b| / min(|a|, |b|)`. Under expansion a matching
street suffix contributes **2** to the intersection and 2 to both set sizes, so
the suffix — the part of an address that identifies nothing — counts twice.
Measured: two unrelated addresses in the same state sharing only `AVE` score
**0.420** on a signal weighted 0.20.

```
1234 N. MAIN ST STE #4B  ->  [1234, n, north, main, st, street, ste, suite, 4b]
                              ^^^^^^^^^^^^^^^^^^^^^^^^^^ 4 of 9 tokens are noise
```

`#` is already handled correctly: `punct_white` drops it, so `STE #4B` and
`STE 4B` both yield `4b`.

## Scope

In scope: the analyzer configuration for street fields in both projects, and a
log-only guard against scoring on stale tokens.

Out of scope, deferred to its own spec: splitting an address into house number
/ street name / secondary unit and scoring the parts separately. The bag-of-
tokens model treats the house number as just another token, so `100 MAIN ST`
against `200 MAIN ST` scores 0.75 containment, or **0.525** after `fuzzy_scale`
— a different building with a strong score. Recorded as an Open item in
`DOT-Commercial/README.md`, not fixed here.

## Decisions

### Contraction to a canonical token, not bidirectional expansion

`street_suffix_canon` uses explicit mapping (`st, str, strt => street`) rather
than expansion (`st, street`). A matching suffix then contributes 1 rather than
2, and token sets roughly halve, so the house number and street name carry
proportionally more weight. This is the single largest effect of the change and
it is a **precision** gain: the shared-suffix-only pair above falls from 0.420
to 0.233.

The synonym filter is the right mechanism precisely because it matches whole
tokens. `st => street` leaves `1ST` untouched. A `pattern_replace` on `(st)`
would not — see the CMS-Providers deletion below.

### Secondary unit designators are dropped, not mapped to a shared token

Two candidates were measured. Mapping every Pub 28 Appendix C2 designator to
one canonical `unit` token makes `STE 200` and `UNIT 200` match, but it also
hands a free shared token to any two addresses that merely both have a suite:

```
100 MAIN ST SUITE 130  vs  55 OAK AVE APT 7206   (different street, city, state)
  current 0.000  ->  canonical `unit` token 0.140  ->  designator dropped 0.000
```

Dropping the designator word and keeping the unit number achieves the same
intended equivalence without manufacturing overlap, because the equivalence
never depended on the word:

```
100 MAIN ST STE 200  vs  100 MAIN ST UNIT 200    current 0.583  ->  0.700
100 MAIN ST STE 200  vs  100 MAIN ST #200        current 0.700  ->  0.700
```

The measured "gains" under the canonical-token variant were almost entirely of
the manufactured kind, which is why it was rejected.

### `street_clean` keeps its designators

Only the `po_box_canon` char filter is added to `street_clean`. It remains the
literal "byte-identical after normalization" path, so `STE 200` against
`UNIT 200` correctly fails the exact test and falls through to the fuzzy path.
Stripping designators there would let two different doors in one building claim
an exact-match 1.0.

## Design

### DOT-Commercial

`DOT-Commercial/configuration/carriers/index-settings.json`.

New char filter, which must run before the tokenizer because the damage in
defect 1 happens at tokenization:

```json
"po_box_canon": {
  "type": "pattern_replace",
  "pattern": "(?i)\\bp[.\\s]*o[.\\s]*(box|b\\b)",
  "replacement": "pobox"
}
```

`street_suffix_synonyms` is replaced by `street_suffix_canon`, keeping
`"lenient": true`, seeded from USPS Publication 28 Appendix C1:

```
st, str, strt => street
ave, av, aven, avenu, avn => avenue
rd => road
blvd, boul, boulv => boulevard
dr, drv, driv => drive
ln => lane
hwy, hiway, highwy, hway => highway
pkwy, pky, parkwy, pkway => parkway
ct, crt => court
cir, circ, circl, crcl => circle
pl => place
ter, terr => terrace
sq, sqr, squ => square
trl, tr => trail
expy, expwy, expr => expressway
tpke, trnpk, turnpk => turnpike
frwy, fwy, freewy => freeway
rte, rt => route
byp, bypa, bypas => bypass
ext, extn => extension
lk => lake
mt, mnt => mount
pt => point
rdg, rdge => ridge
spg, spng, sprng => spring
vly, vlly => valley
xing, crssng => crossing
plz, plza => plaza
ctr, cen, cent, centre => center
n, no, nth => north
s, so, sth => south
e, ea => east
w, we => west
ne => northeast
nw => northwest
se => southeast
sw => southwest
```

New stop filter from Appendix C2, `ignore_case: true`:

```
ste, suite, apt, apartment, unit, rm, room, bldg, building, fl, floor,
trlr, trailer, lot, sp, space, pmb, dept, department, hngr, hangar,
ofc, office, slip, pier
```

Analyzers become:

```json
"street_clean": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "keyword",
  "filter": ["icu_normalizer", "icu_folding", "punct_white",
             "collapse_whitespace", "trim"]
},
"street_tokens": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "standard",
  "filter": ["icu_normalizer", "icu_folding", "punct_white",
             "unit_designator_stop", "street_suffix_canon"]
}
```

`unit_designator_stop` runs before `street_suffix_canon` so the designator is
gone before suffix mapping is attempted, which keeps the two lists independent
of each other.

Fields affected, all of which already carry both subfields and so need no
mapping change: `phy_street`, `mailing_street`, `boc3_agents.street_po`.

### CMS-Providers

CMS-Providers has no `entity-match.json`, so nothing scores addresses there
today. These changes are for parity and are verifiable only through `_analyze`
output, not through match scores. Recording that plainly so the next reader
does not go looking for a score that moved.

Its address fields carry only `.keyword` and `.clean`; there is no `.tokens`
subfield and no synonym-bearing analyzer at all.

- `hospitals` and `doctors-clinicians`: add `po_box_canon`,
  `street_suffix_canon`, `unit_designator_stop` and a `street_tokens` analyzer
  to `index-settings.json`, matching DOT-Commercial exactly; add `po_box_canon`
  to the existing `street_clean`.
- Add a `.tokens` subfield to `hospitals.Address` and to
  `doctors-clinicians.adr_ln_1` and `adr_ln_2` in `index-mappings.json`.
- Delete `street_suffix_map` from all three config directories. It is a
  `pattern_replace` with the unanchored pattern `(st)` and replacement
  `street`, so it would rewrite `FIRST` to `FIRstreet`. It is referenced by no
  analyzer, so it has never run — but it reads as the intended approach and
  would be copied by the next person. `facillity-affiliations` has no address
  field, so it gets this deletion and nothing else.

### Stale-analyzer guard

`phase_entity_match._validate_source_index` verifies that the scored subfields
_exist_, then instructs the operator to reload the index. It cannot detect a
`.tokens` subfield built by a **superseded** analyzer configuration: the
subfield name is unchanged, so the check passes, the sweep runs, and every
address score is computed from stale tokens. That is the failure shape
`CLAUDE.md` names as this codebase's recurring bug — a phase that logs success
while producing quietly wrong output. This change is the first time the
analyzer configuration changes after data is loaded, so it is the first time
the gap can bite.

- `phase_index_creation` writes a hash of the `settings.index.analysis` block
  into the index `_meta` at creation time, under `analysis_fingerprint`.
- `phase_entity_match` recomputes the hash from the on-disk
  `index-settings.json` and compares. **On mismatch it logs an error naming
  both hashes and the reindex command, and continues.** It does not refuse to
  run: an operator deliberately sweeping an older index is a legitimate case,
  and a hard stop would block it. The point is that the mismatch is stated
  rather than silent.
- An index created before this change has no `analysis_fingerprint`. That is
  logged at warning level once and treated as unknown, not as a mismatch.

## Measured impact

2,000 pairs sampled at random from `chameleon-candidates-2026.08.02-000001`,
replayed through both analyzer configurations, scored with `AddressSignal`'s
own formula (exact first, else `containment × 0.7`, halved across states).

|                                            | Count              | Mean delta |
| ------------------------------------------ | ------------------ | ---------- |
| Address score changed                      | 797 / 2000 (39.9%) |            |
| Score decreased                            | 790                | −0.074     |
| Score increased                            | 7                  | +0.029     |
| Approx. pairs falling below the 0.35 floor | 46                 |            |
| Approx. pairs rising above it              | 0                  |            |

**This sample cannot show the recall gain, and the zero above is an artifact of
that.** It is drawn from pairs the _current_ configuration already emitted, so
a pair the current analyzer never surfaced is structurally absent. The recall
case is defect 1 and defect 2 — 43,799 punctuated `P.O.` records and the
`rte`/`route` split — none of which can produce a candidate today. Confirming
that side requires the full re-sweep listed under Rollout.

The precision gain is the part this sample does establish, and it is the
dominant effect: 790 pairs down, concentrated on pairs whose only overlap was a
street suffix or a directional.

## Testing

- **`tests/test_street_analysis.py`**, new, skipped when Elasticsearch is
  unreachable. Asserts token output for a table of synthetic addresses: the
  four `P.O.` spellings, designator forms, suffix contraction, directionals,
  and `1ST AVE` against `FIRST AVE` as a standing regression guard against the
  `pattern_replace` bug being reintroduced.
- **`tests/test_scorer.py` / `test_signals.py`** are unaffected — they are
  Elasticsearch-free and feed token sets directly.
- **Acceptance gate.** The measurement harness moves to
  `scripts/measure_address_analyzers.py` and is run before and after: ~40% of
  pairs change, ~46 fall below the floor, and the manufactured-overlap case
  stays at 0.000.
- **Full re-sweep**, checked against the sanity anchor in
  `DOT-Commercial/README.md`: a carrier re-registering under a byte-identical
  legal name at the same address and phone one day after shutdown must remain
  top-ranked. If it moves, the change is wrong.
- `.venv/bin/python -m ruff check .` prints `All checks passed!`.

## Rollout

1. Apply the configuration changes to both projects.
2. Recreate and reload the `carriers` index (2,085,536 documents). The old
   index is left in place until the sweep is verified.
3. Re-run the `entity-match` sweep.
4. Compare against the acceptance gate and the sanity anchor.
5. Update the "Name and address analyzers" section of
   `DOT-Commercial/README.md` with the new behavior and the measured numbers.
6. Add the Open item below.

## Deferred

To be added to `DOT-Commercial/README.md` Open items:

> **`AddressSignal` scores a street as an unordered bag of tokens, so the house
> number carries no more weight than the word `STREET`.** Two different
> buildings on one road overlap on everything except one token: `100 MAIN ST`
> against `200 MAIN ST` scores 0.75 containment, **0.525** after `fuzzy_scale`,
> on a signal weighted 0.20. Canonical-token contraction reduced but did not
> remove this, since it shrinks the denominator as well. The fix is to parse
> the street into house number, street name and secondary unit and score the
> parts separately, letting a house-number mismatch cap the result rather than
> cost one token. Deferred from the address synonym normalization work.

## Risks

- **Every address score moves.** 40% of existing pairs change, so published
  score numbers in `DOT-Commercial/README.md` go stale and are refreshed in
  step 5. Thresholds remain uncalibrated either way, as documented.
- **46 pairs per 2,000 drop below the floor.** These are pairs whose only
  address evidence was a shared suffix, so losing them is the intent — but the
  re-sweep should confirm none of them carried a VIN or phone match that made
  them worth keeping.
- **The stop list removes a real word when a street is named for one.** A
  street literally named `LOT` or `KEY` loses that token. `KEY` was excluded
  from the C2 list for this reason (`KEY LARGO`, `KEY WEST`); `LOT` and `SP`
  are retained because no measured street name uses them. If a later sweep
  shows otherwise, the entry comes out.
