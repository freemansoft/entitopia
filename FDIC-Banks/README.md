# FDIC-Banks

FDIC BankFind institution records. The matcher is scored against the
successions the FDIC itself declares: a closed bank's `NEWCERT` names its
direct successor and `ULTCERT` the end of its merger chain.

Everything below is an aggregate. No bank, CERT, address or website is named.

## Fetching

```bash
cd FDIC-Banks
../.venv/bin/python fetch_fdic.py --dataset institutions
../.venv/bin/python fetch_fdic.py --dataset history     # coverage check only, never loaded
```

`https://api.fdic.gov/banks/<endpoint>?format=csv`, no key, 10,000 rows per page.

## Profile (fetched 2026-11-26)

| Fact                                    | Value                                                             |
| --------------------------------------- | ----------------------------------------------------------------- |
| Rows (`CERT` unique)                    | 27,838                                                            |
| Inactive (`ACTIVE` 0) / active          | 23,634 / 4,204                                                    |
| Blank `ESTYMD`                          | 0                                                                 |
| Blank `ENDEFYMD` (raw CSV)              | 0 (after load: 4,226 missing, 4,204 active plus 22 inactive)      |
| Blank `NEWCERT`                         | 0                                                                 |
| Blank `WEBADDR`                         | 23,700 (85.1%)                                                    |
| Blank `PRIORNAME1`                      | 15,450 (55.5%)                                                    |
| Blank `ADDRESS2`                        | 27,516 (98.8%)                                                    |
| Blank `ULTCERT`                         | 33 (29 of them inactive)                                          |
| `NEWCERT` with a `.0` suffix            | 0                                                                 |
| Distinct `NAME` / `ADDRESS` / `NEWCERT` | 22,979 / 23,520 / 7,963                                           |
| `RSSDHCR` (holding company) populated   | 13,550 of 23,634 inactive, 3,535 of 4,204 active (8,740 distinct) |

`NEWCERT` head: `0` appears 5,146 times, which is every active bank (4,204) plus
942 inactive banks with no successor. The next most common value covers 109
banks, so no real successor dominates.

`12/31/9999` is a junk value in `ENDEFYMD`: it means "not ended" on all 4,204
active banks and appears on 22 inactive banks, where it means nothing. The
ingest pipeline removes it before the date conversion, so the field is genuinely
missing. Active banks then have no end date, and the 22 inactive banks are
undated predecessors (inactive banks with no `ENDEFYMD` after the load: 22; none
loads as a 9999 date). `ESTYMD` has no sentinel.

The API adds an `ID` column of its own to every CSV row. It is not a bank
identifier (`CERT` is) and is loaded only as a keyword.

`CHANGEC1` among the 23,634 inactive rows: 223 (17,707), 211 (2,538), 221 (816),
217 (666), 240 (575), 213 (567), 230 (236), 224 (191), 222 (173), 235 (110),
216 (25), blank (23), 215 (3), and one each of 225, 810, 820 and 470.

## Hazards

1. **The API ends each page without a newline.** Appending the next page fused
   two records into one row and silently lost an institution at each page
   boundary (27,836 rows read instead of 27,838). `fetch()` terminates every
   page. Guarded by `tests/test_fdic_fetch.py`.
2. **A bad date must drop a value, not a bank.** `ESTYMD` and `ENDEFYMD` are
   strict dates; the ingest pipeline converts `MM/dd/yyyy` and removes the field
   on failure. `tests/test_fdic_pipeline.py` guards the ordering (the 12/31/9999
   sentinel is removed before the date conversion) by reading `pipelines.json`.
   Nothing guards the `on_failure` drop itself: no test runs the pipeline, and
   it was verified once with `_simulate`.
3. **The CSV header comes from the first record.** Unfiltered, the history CSV
   came back with `CHANGECODE`, `CHANGECODE_DESC`, `EFFDATE` and `ID` and no
   `CERT` columns, because branch events lack them. The history fetch is filtered
   to change codes 200-299, and `fetch()` refuses a header missing any requested
   column. Guarded by `tests/test_fdic_fetch.py`.
4. **A website field mapped as a bare `keyword` makes `exact-identifier` inert.**
   The signal seeds candidates and scans for shared values on
   `<field>.keyword`. `WEB_DOMAIN` was first mapped as a bare keyword, which has
   no such subfield: no candidates were retrieved, nothing was suppressed, and
   the sweeps logged success. `utils/config_liveness.py` now reports any
   `exact-identifier` field without a `.keyword` subfield, and the `validate`
   phase would have failed both sweeps on the old mapping. Guarded by
   `tests/test_config_liveness.py`
   (`test_an_identifier_field_without_a_keyword_subfield_is_reported`).
5. **A street without its town is not an address.** FDIC gives a bare "Main
   Street", with no number, as the address of 1,481 banks. The address signal
   scores an exact street match 1.0 and keeps that score across state lines, so
   on `ADDRESS` alone each of those banks matched every other at 1.0. On the
   tuning half, 7,301 undeclared absorption pairs at 0.5 or above were a shared
   number-less street in a different town, and 1,056 absorption predecessors
   hit `max_candidates` because the street seed retrieved hundreds of
   look-alikes. Both sweeps logged success. The ingest pipeline now derives
   `STREET_LOCAL` (`<street> | <city> <state>`): its `clean` subfield keeps the
   whole value, so an exact match and the seed need the same street in the same
   town, and its `tokens` subfield drops the town again, so the fuzzy
   comparison still reads the street alone. Both sweeps' address signal reads
   it. A bank missing its city or state gets no `STREET_LOCAL` at all, since a
   town-less `Main Street | ` would bring the cross-town match back (no loaded
   bank has a street without both).

   The hazard is only partly closed. The fuzzy comparison still reads the bare
   street, so two "Main Street" banks in different towns of the same state
   still score containment 1.0 times `fuzzy_scale` 0.7, an address score of
   0.7; the cross-state penalty halves that only between states. What the fix
   removes is the exact 1.0 and the seed flood. Guarded by `tests/test_fdic_street_local.py`, which reads the committed
   files; the pipeline and analyzer were checked once with `_simulate` and
   `_analyze`.

## Does `NEWCERT` cover the history?

Succession events are history rows with change codes 223 (merger without
assistance), 211 (failure, whole institution), 221 (absorption), 217
(passthrough receivership), 213 (assisted merger), 224 (affiliated pooling),
222 (consolidation), 216 (bridge bank), 215 (failure, multiple acquirer) and 225
(partial purchase and assumption). Codes 260, 240, 230 and 235 (closings) carry
no acquirer and are excluded.

| Code | Events | Outgoing bank in table | `NEWCERT` hit |
| ---- | ------ | ---------------------- | ------------- |
| 223  | 18,395 | 16,704                 | 16,684        |
| 211  | 2,550  | 2,525                  | 2,524         |
| 221  | 1,026  | 807                    | 807           |
| 217  | 642    | 642                    | 641           |
| 213  | 332    | 319                    | 318           |
| 224  | 199    | 191                    | 191           |
| 222  | 173    | 167                    | 167           |
| 216  | 26     | 25                     | 25            |
| 215  | 3      | 3                      | 3             |
| 225  | 1      | 1                      | 1             |

- 23,347 succession events, one per outgoing bank; 21,384 have an `OUT_CERT`
  present in the institutions table, and 21,361 of those have a `NEWCERT` equal
  to the surviving or acquiring CERT: **99.89% of the reachable events**.
- 1,963 events (8.4%) name an outgoing bank that the institutions endpoint does
  not return, so counted against all events coverage is **91.5%**. That gap does
  not count against `NEWCERT`: these events have no predecessor document, so no
  sweep can start from them and enriching from history would not reach them
  either. The 99.89% is the binding figure.
- 23 in-table events disagree with `NEWCERT` (a non-zero value that differs).

## Name stop list

The phonetic analyzers drop the words below (`institution_word_stop` in
`configuration/institutions/index-settings.json`), so two unrelated banks do not
score as alike because both are a "First National Bank". It replaces the
trucking-company list that the file started from. Measured 2026-11-26 as the
share of the 27,838 `NAME` values containing each word (a word counted once per
name):

| Word          | Share  | Word        | Share |
| ------------- | ------ | ----------- | ----- |
| `bank`        | 82.69% | `fsb`       | 1.54% |
| `of`          | 27.90% | `peoples`   | 1.53% |
| `national`    | 23.75% | `central`   | 1.45% |
| `association` | 19.58% | `new`       | 1.38% |
| `savings`     | 18.70% | `commerce`  | 1.29% |
| `first`       | 16.55% | `f`         | 1.27% |
| `and`         | 13.98% | `south`     | 1.25% |
| `the`         | 13.38% | `north`     | 1.07% |
| `state`       | 11.35% | `west`      | 1.05% |
| `loan`        | 9.61%  | `merchants` | 1.05% |
| `federal`     | 8.84%  | `union`     | 1.02% |
| `trust`       | 7.90%  |             |       |
| `company`     | 5.97%  |             |       |
| `county`      | 4.41%  |             |       |
| `community`   | 3.61%  |             |       |
| `citizens`    | 2.88%  |             |       |
| `american`    | 2.51%  |             |       |
| `farmers`     | 2.28%  |             |       |
| `city`        | 2.08%  |             |       |
| `a`           | 1.89%  |             |       |
| `security`    | 1.81%  |             |       |
| `valley`      | 1.76%  |             |       |
| `texas`       | 1.72%  |             |       |
| `s`           | 1.66%  |             |       |
| `united`      | 1.55%  |             |       |

The cut is every word at or above 1%. The legal suffixes `inc`, `co`, `corp`,
`corporation`, `ltd` and `llc` are also stopped although each is under 1%. A name
made only of stopped words (for example a bare "First National Bank") has no
phonetic tokens and the phonetic signals read it as not evaluable; the
`name-token` signal uses the unstopped `clean` subfield and still scores it.

The ingest pipeline derives `WEB_DOMAIN` from `WEBADDR`: lowercased, with the
scheme, a leading `www.` and any path, query or fragment removed, so
`http://www.example.com/`, `https://example.com/path` and `EXAMPLE.COM` all
become `example.com` (checked with `_ingest/pipeline/.../_simulate`). A blank or
missing `WEBADDR` leaves `WEB_DOMAIN` absent rather than empty, because blank
must never match blank. `WEB_DOMAIN` is mapped as `text` with a `keyword`
subfield because an `exact-identifier` signal's seed and shared-value scan read
`WEB_DOMAIN.keyword`. After the load,
4,137 banks carry one (4,079 distinct), the most shared domain is on 5 banks
(checked with a terms aggregation on `WEB_DOMAIN.keyword`) and none is empty, so
no `ignore_values` or `max_shared_records` entry is needed.

**Neither sweep uses the website.** Only 4 of the 23,634 inactive banks have
one (FDIC keeps websites for active banks), and the signal is evaluable only
when both sides have a value, so it could never score a pair whose predecessor
is a closed bank. No pair in either first run carried it. Tuning removed it
from both sweeps' signals and seeds, and the pair ids were identical before and
after (`bank-reappearance` 12,851 and `bank-absorption` 143,172, compared with
`scripts/compare_pair_ids.py`). Left in, its weight (about a quarter of the
total) read as the second-heaviest evidence in a config where it contributed
nothing. `WEB_DOMAIN` stays loaded for a sweep whose predecessors include active
banks.

## Sweeps

Two sweeps over `institutions-000001` partition time at one overlap value
(`-180` days, the reappearance `min_gap_days`), so each succession is searched
by exactly one of them (`tests/test_succession_window_pairs.py`):

| Step                | Gap window (successor `ESTYMD` minus predecessor `ENDEFYMD`) | Undated candidates | Temporal signal                  |
| ------------------- | ------------------------------------------------------------ | ------------------ | -------------------------------- |
| `bank-reappearance` | -180 to +365 days                                            | excluded           | yes, `before_shutdown_scale` 1.0 |
| `bank-absorption`   | up to -181 days, no lower bound                              | kept               | none                             |

A successor created more than 365 days after the shutdown is in neither window,
by design. The truth rule is `NEWCERT`; `ULTCERT` (end of the merger chain), a
shared holding-company id `RSSDHCR` and a shared `ULTCERT` (`fdic-merger-tree`)
are `explains` rules, which keep a pair out of the findings without counting
toward recall. The shared `ULTCERT` rule follows merger chains transitively, as
the spec requires: in a chain A to B to C every bank carries C's `ULTCERT`, so
A-B, A-C and B-C are all explained, and so are two banks absorbed into one tree
by different routes. That works because 4,200 of the 4,204 active banks carry
their own `CERT` as `ULTCERT` (the other 4 have none) and no bank has `0`. The
`ULTCERT` key rule is kept: a pair whose candidate is an active tree root with
no `ULTCERT` of its own is explained only by it. Placeholders in `ignore_values`:
`0` on `NEWCERT`, `ULTCERT` and `RSSDHCR` (no `RSSDHCR` is `0` in the data; the
shortest value has seven digits, so the entry is only a guard). `max_shared_records`
is not set for `RSSDHCR`: it applies to signal fields only, and `RSSDHCR` is read
by a declared rule, not a signal.

First run, 2026-11-26, starting weights copied from `chameleon-detection` and
renormalized over the signals present, before tuning (the tuned figures are in
the next section):

| Step                | Pairs   | `truth_links` | `truth_retrieved` | `truth_emitted` | Undated predecessors skipped | Truncated candidate sets |
| ------------------- | ------- | ------------- | ----------------- | --------------- | ---------------------------- | ------------------------ |
| `bank-reappearance` | 14,911  | 22,692        | 927               | 922             | 22                           | 0                        |
| `bank-absorption`   | 151,938 | 22,692        | 4,353             | 2,597           | 22                           | 1,056                    |

`truth_links` is every non-`0` `NEWCERT` that a searched predecessor names,
including targets missing from the index, and counts the same in both sweeps;
each sweep can only retrieve the ones inside its own window, so these are not
recall figures. Absorption hit the `max_candidates` ceiling on 1,056 of
its 23,634 predecessors. Neither run reported a lookup, scoring or index error.

## Measured against FDIC's recorded successions

Measured 2026-11-26 on `institutions-2026.11.26-000007` (27,838 banks). Truth
links are split by a hash of the predecessor's `CERT`: weights and thresholds
were tuned on the `tune` half only, and the `report` half was measured once per
sweep after tuning was final, then once more after an `explains` rule the spec
calls for was added (see "Report half"). Precision counts `truth` against
`none` pairs above each score; `explains` pairs count toward neither, and the
FDIC record is taken as complete (`complete_truth`), which the hand check below
shows is not quite so.

### Choosing `overlap_days`: kept at 180

The valley rule wants the gap between a reappearance mode near the shutdown and
an absorption mode years before it. Tune-half `NEWCERT` links with both dates
(11,519), binned by successor `ESTYMD` minus predecessor `ENDEFYMD`. The
`--gaps` bins of `measure_declared_links.py` are too coarse for this, so the
table was computed directly from `institutions-000001`: every document with
`INACTIVE` 1 and `split_half(CERT) == "tune"` (`matching/declared.py`), whose
`NEWCERT` is not `0` and names a `CERT` in the index, and where both dates are
present. Each bin includes its lower bound and excludes its upper bound in whole
days; the rows sum to the `--gaps` totals (133 at -180 to -1, 661 at 0 to 365).

| Gap (days)     | Links | Per 30 days |
| -------------- | ----- | ----------- |
| before -3650   | 9,828 |             |
| -3650 to -1096 | 709   | 8.3         |
| -1095 to -366  | 148   | 6.1         |
| -365 to -181   | 38    | 6.2         |
| -180 to -91    | 20    | 6.7         |
| -90 to -61     | 15    | 15.0        |
| -60 to -31     | 12    | 12.0        |
| -30 to -8      | 55    | 71.7        |
| -7 to -1       | 31    | 132.9       |
| 0              | 651   |             |
| 1 to 6         | 10    | 50.0        |
| 7 to 365       | 0     | 0           |
| after 365      | 2     |             |

The reappearance mode is a spike: 651 of the 794 links in the reappearance
window have a successor registered on the shutdown day itself, and none at all
from 7 to 365 days after. Before the shutdown the density falls to about 6 to 8
links per 30 days by -90 and stays there back to -3650; the absorption mode
sits further back still, with 9,828 links more than ten years old. The valley is
that flat floor from about -90 to -3650. It has no minimum to aim at, and 180
lies inside it, so `overlap_days` stays at the carrier project's 180 and
`tests/test_succession_window_pairs.py` is unchanged. Moving it to 90 would
shift only the 20 links at -180 to -91, all at floor density, between the
sweeps.

### Tuned configuration

| Setting                                                          | `bank-reappearance`                    | `bank-absorption`                |
| ---------------------------------------------------------------- | -------------------------------------- | -------------------------------- |
| Address field                                                    | `STREET_LOCAL` (hazard 5)              | `STREET_LOCAL`                   |
| Website (`exact-identifier`)                                     | removed                                | removed                          |
| Seeds                                                            | name-phonetic, address                 | name-phonetic, address           |
| Weights: phonetic dm / bm / name-token / address / temporal      | 0.219 / 0.1314 / 0.0876 / 0.292 / 0.27 | 0.30 / 0.18 / 0.12 / 0.40 / none |
| `temporal.max_gap_days`                                          | 60 (was 365)                           | no temporal signal               |
| `min_total_score`, `max_candidates`, `max_pairs_per_predecessor` | 0.35, 500, 10 (unchanged)              | 0.35, 500, 10 (unchanged)        |

Weights are renormalized to sum to 1. That is not quite cosmetic: a pair whose
names agree fully and whose street does not now scores exactly 0.60 in
absorption, where the old weights gave it 0.59997. The same pairs moved from
below the 0.6 band to inside it (tune recall at 0.6 rose from 0.041 to 0.052 with
no score changing by more than 0.001), and 67 pairs were lost and 101 gained at
the `min_total_score` and per-predecessor edges. Bands that sit on a sum of
weights are fragile; read the 0.6 band with that in mind.

### Tuning log (tune half)

Each run was compared by pair id with `scripts/compare_pair_ids.py` against the
run it changed. The stopping rule was: keep a change only if it raises recall at
0.70 without lowering precision at 0.70, and stop after about six runs per
sweep.

| Run | Change                                             | Pairs   | Pair ids lost / gained | Recall at 0.70 | Precision at 0.70 | Kept                                     |
| --- | -------------------------------------------------- | ------- | ---------------------- | -------------- | ----------------- | ---------------------------------------- |
| R0  | first run (Task 7)                                 | 14,911  |                        | 0.482          | 0.640             |                                          |
| R1  | address on `STREET_LOCAL`                          | 12,851  | 2,071 / 11 vs R0       | 0.481          | 0.957             | yes: one link lost, precision +0.32      |
| R2  | website signal and seed removed                    | 12,851  | identical to R1        | 0.481          | 0.957             | yes: no effect                           |
| R3  | temporal weight 0.0676 to 0.25                     | 16,523  | 1,154 / 4,826 vs R2    | 0.509          | 0.912             | no: precision fell                       |
| R4  | R3 plus temporal `max_gap_days` 365 to 60          | 10,456  | 4,719 / 2,324 vs R2    | 0.509          | 0.960             | yes                                      |
| R5  | same-town rarity signal on `LOCALITY`, weight 0.15 | 7,037   | 3,480 / 61 vs R4       | 0.495          | 0.978             | no: recall fell                          |
| R6  | `name-token` added to the seeds                    | 83,072  | 450 / 73,066 vs R4     | 0.540          | 0.927             | no: precision fell                       |
| RF  | R4 with weights renormalized                       | 10,460  | 0 / 4 vs R4            | 0.509          | 0.960             | final                                    |
| A0  | first run (Task 7)                                 | 151,938 |                        | 0.0200         | 0.025             |                                          |
| A1  | address on `STREET_LOCAL`                          | 143,172 | 16,752 / 7,986 vs A0   | 0.0208         | 0.170             | yes                                      |
| A2  | website signal and seed removed                    | 143,172 | identical to A1        | 0.0208         | 0.170             | yes: no effect                           |
| A3  | same-town rarity signal on `LOCALITY`, weight 0.15 | 118,642 | 28,293 / 3,763 vs A2   | 0.0181         | 0.246             | no: recall fell                          |
| A4  | address `fuzzy_scale` 0.7 to 0.35                  | 138,922 | 13,343 / 9,093 vs A2   | 0.0143         | 0.598             | no: recall fell                          |
| A5  | `max_pairs_per_predecessor` 10 to 50               | 357,579 | 0 / 214,407 vs A2      | 0.0208         | 0.166             | no: precision fell, recall at 0.70 level |
| AF  | A2 with weights renormalized                       | 143,206 | 67 / 101 vs A2         | 0.0208         | 0.169             | final                                    |

What the rejected runs showed, so they need not be rerun blind:

- **R5 and A3, a same-town signal.** It is the strongest precision lever in
  both sweeps (reappearance precision at 0.6 rose from 0.63 to 0.91), but it
  dilutes every truth pair whose successor sits in another town or in a city
  common enough to have low rarity, and that cost recall at 0.70. The field
  stays loaded so the trial is a config edit. Before re-enabling it, note that
  the sweep counts value frequencies with a terms aggregation capped at
  `RARITY_TERMS_SIZE` = 500 (`phase_providers/phase_entity_match.py`), and
  `LOCALITY` has 10,178 distinct values. The 500 most common cover 9,687 banks
  and reach down to towns with 8; any town outside them reads as unseen and
  scores rarity 1.0, against about 0.77 for the least common town inside, so
  every small town scores as if it were unique.
- **R6, seeding on unstopped name words.** It is the reappearance recall lever:
  not-retrieved links fell from 300 to 50 and overall recall rose from 0.62 to
  0.79. The cost is eight times the pairs, 5.3 million candidates examined
  instead of 35,000, 1,762 truncated candidate sets, 119 links retrieved but
  cut, and precision at 0.70 down by 0.033. The recovered links are mostly
  successors registered on the shutdown day with no phonetic name match, found
  because the seed matched shared generic words inside the window.
- **A4, a lower fuzzy street weight.** Absorption precision at 0.70 rose from
  0.17 to 0.60, but recall at 0.70 fell by a third, because the absorption truth
  pairs that score high are the ones whose street partly agrees.
- **A5, more pairs per predecessor.** Overall absorption recall rose from 0.126
  to 0.165, all of it below 0.70, for 2.5 times the pairs.

### Report half

Re-measured 2026-11-26 after the `fdic-merger-tree` rule was added to both
sweeps. Re-measuring the report half is legitimate here: the rule comes from the
spec (multi-step chains are followed transitively), not from report-half
results, and no weight, threshold or window changed. Tags are written at sweep
time, so both sweeps were re-run; their pair ids are identical to the runs first
measured (10,460 and 143,206 pairs, `scripts/compare_pair_ids.py`), and recall,
the misses and the exclusions are unchanged, because a tag never affects which
pairs are emitted. Only `explains_by_band` and precision moved. The rule tags
480 reappearance and 6,525 absorption pairs across both halves.

| Precision, report half | before the rule | after |
| ---------------------- | --------------- | ----- |
| reappearance at 0.70   | 0.908           | 0.915 |
| reappearance at 0.80   | 0.974           | 0.977 |
| absorption at 0.70     | 0.184           | 0.203 |
| absorption at 0.80     | 0.598           | 0.652 |
| absorption at 0.90     | 0.870           | 0.917 |

`bank-reappearance`, `.venv/bin/python scripts/measure_declared_links.py
--project FDIC-Banks --step bank-reappearance --index
bank-reappearance-pairs-000001 --half report --gaps`, which resolves the alias
and prints the concrete index it measured:

```json
{
  "half": "report",
  "pair_index": "bank-reappearance-pairs-2026.11.26-000012",
  "denominator": 697,
  "exclusions": {
    "target_missing_from_index": 4,
    "target_outside_window": 10460,
    "target_undated": 0,
    "predecessor_undated": 0,
    "predecessor_not_searched": 0
  },
  "recall": 0.6097560975609756,
  "recall_by_band": {
    "0.35": 0.6097560975609756,
    "0.5": 0.599713055954089,
    "0.6": 0.5523672883787661,
    "0.7": 0.4949784791965567,
    "0.8": 0.42898134863701576,
    "0.9": 0.28407460545193686
  },
  "misses": {
    "not_retrieved": 270,
    "retrieved_not_emitted": 2
  },
  "explains_by_band": {
    "0.35": 292,
    "0.5": 100,
    "0.6": 33,
    "0.7": 6,
    "0.8": 2,
    "0.9": 0
  },
  "precision_by_band": {
    "0.35": 0.08694762684124387,
    "0.5": 0.3013698630136986,
    "0.6": 0.6169871794871795,
    "0.7": 0.9151193633952255,
    "0.8": 0.9771241830065359,
    "0.9": 0.9801980198019802
  },
  "gap_histogram": {
    "<-3650": 9589,
    "-3650..-366": 827,
    "-365..-181": 41,
    "-180..-1": 109,
    "0..365": 588,
    ">365": 3
  }
}
```

`bank-absorption`, the same command with `--step bank-absorption --index
bank-absorption-pairs-000001`:

```json
{
  "half": "report",
  "pair_index": "bank-absorption-pairs-2026.11.26-000010",
  "denominator": 10457,
  "exclusions": {
    "target_missing_from_index": 4,
    "target_outside_window": 700,
    "target_undated": 0,
    "predecessor_undated": 0,
    "predecessor_not_searched": 0
  },
  "recall": 0.12374485990245769,
  "recall_by_band": {
    "0.35": 0.12374485990245769,
    "0.5": 0.08243282012049345,
    "0.6": 0.04676293391986229,
    "0.7": 0.021229798221287177,
    "0.8": 0.015205125753084059,
    "0.9": 0.009562972171750981
  },
  "misses": {
    "not_retrieved": 8330,
    "retrieved_not_emitted": 833
  },
  "explains_by_band": {
    "0.35": 6656,
    "0.5": 3222,
    "0.6": 1180,
    "0.7": 216,
    "0.8": 62,
    "0.9": 14
  },
  "precision_by_band": {
    "0.35": 0.0202891279124463,
    "0.5": 0.024998549968099297,
    "0.6": 0.04376622214266535,
    "0.7": 0.20255474452554745,
    "0.8": 0.6516393442622951,
    "0.9": 0.9174311926605505
  },
  "gap_histogram": {
    "<-3650": 9589,
    "-3650..-366": 827,
    "-365..-181": 41,
    "-180..-1": 109,
    "0..365": 588,
    ">365": 3
  }
}
```

The two denominators partition the report half's dated links: 697 plus 10,457
is 11,154, and each sweep's `target_outside_window` is the other's denominator
plus the 3 links registered more than 365 days after the shutdown, which
neither sweep searches.

### What it shows

- **Reappearance works and its misses are retrieval.** It finds 61% of the
  report half's reappearance-shaped successions, 49.5% at 0.70 and above, with
  91.5% precision there (97.7% at 0.80). The tune half reads 0.509 recall and
  0.964 precision at 0.70 under the same rules, so the report half is a little
  lower on both, as a held-out half should be. 270 of the 272 misses were never
  retrieved: the successor shares neither a phonetic name word nor a street in
  the same town. Only 2 were retrieved but not emitted, so no weight change can
  recover the rest; seeding as in R6 retrieves most of them.
- **Absorption does not work on institution records.** It finds 12.4% of the
  report half's absorptions, 2.1% at 0.70, with 20.3% precision at 0.70 (91.7%
  at 0.90: 100 `truth` and 9 `none` pairs; the tune half reads 0.188 at
  0.70). 8,330 of the 9,163 misses (91%) were never retrieved, and retrieval is
  not the fixable kind: on the tune half at the first run, of 8,508 links never retrieved only about 440 shared a name
  word outside the stop list, 819 shared the town, 7 shared the street, and
  1,737 shared the holding company. An established acquirer usually has its own
  name and its own headquarters in another town of the same state (84% of
  absorption links are same-state, 13% same-town), so the fields FDIC carries on
  an institution record cannot connect most absorptions to their acquirer. No
  weight, threshold or `max_candidates` value changes that.

  How these were produced, so they can be regenerated: the 8,508 are the links
  `measure_declared_links.py --step bank-absorption --half tune` classifies
  `not_retrieved` under the first-run `entity-match.json` (commit `3fe1e38`,
  address on `ADDRESS`); retrieval status comes from re-running the candidate
  search, so it needs only that config and the institutions index. Each link's
  predecessor and target documents were then compared: a shared name word is a
  lowercased whitespace token of `NAME` or `PRIORNAME1`, stripped of
  `,.&()'`, that is not in the stop list above (with its legal suffixes, plus
  `n.a.` and `na`), so the 440 is approximate; the town is `CITY` equal
  ignoring case with the same `STALP`; the street is `ADDRESS` equal ignoring
  case; the holding company is a non-empty `RSSDHCR` equal on both. The 84% and
  13% are the same state and town comparisons over all 10,723 tune-half links
  whose gap is below -180 days.

- **Precision depends on the score band far more than in reappearance.**
  Absorption at 0.35 to 0.70 is mostly unrelated banks that share a name word,
  in another town and usually another state (69% of undeclared pairs at 0.5
  and above on the tune half). Among the highest-scoring pairs the street and
  town agree; with merger trees explained, the false matches left there are
  mostly two charters at one address or one common bank name in two towns
  (next section).

### Hand check of the 30 highest-scoring undeclared pairs

Read locally on 2026-11-26 from both halves of each pair index, after the
`fdic-merger-tree` rule. Counts only:

| Sweep               | Real succession FDIC did not record | Holding company, affiliate or chain the `explains` rules missed | Unrelated look-alike |
| ------------------- | ----------------------------------- | --------------------------------------------------------------- | -------------------- |
| `bank-reappearance` | 12                                  | 3                                                               | 15                   |
| `bank-absorption`   | 3                                   | 4                                                               | 23                   |

The first reading, before the rule, was 11, 5 and 14 for reappearance and 3, 15
and 12 for absorption. The rule tagged 3 of that reading's 30 reappearance pairs
and 14 of its 30 absorption pairs `explains`: in each, both banks carry one
`ULTCERT` (a failed bank and its bridge bank, a charter re-formed under its own
name, banks absorbed into one tree by different routes). The second reading
re-read all 30 in each sweep, so its columns are not the first reading's minus
the tagged pairs. Absorption's 30th place falls inside a tie: 14 pairs score
exactly 0.88, and the 8 of them read are those the sort returned first.

- **Reappearance precision is a lower bound.** 12 of 30 is over the 5 the plan
  allowed before treating FDIC's record as complete. The shape is consistent:
  identical or near-identical name, same street in the same town, successor
  registered within days, and in 11 of the 12 a predecessor whose `CHANGEC1` is
  a closing code (240 or 230) with `NEWCERT` 0, so FDIC recorded no successor
  at all. Charter conversions, conservatorship successors and a deposit-insurance
  bank standing in for a closed one look like this. The 3 affiliate pairs are a
  same-name charter in the same town, opened within a month of FDIC recording
  the predecessor's merger into a different bank.
- **Absorption's false matches are now mostly look-alikes.** The merger-tree
  rule moved most of the chain pairs out of the denominator; 4 affiliate pairs
  remain, two of them a one-day merger-vehicle charter paired with the savings
  association at its address. Of the 23 look-alikes, 9 share a numbered street
  address in the same town and may be a building or branch passing between
  charters; they were counted as look-alikes because nothing in the record says
  so. The other 14 are a common bank name in two towns, or a street name with
  no number shared by two banks in one town.
- Under the plan's rule absorption precision is not flagged as a lower bound (3
  of 30). It is still understated by the 4 affiliate pairs, and by however many
  of the 9 shared-address pairs are real.

### Follow-ups

1. **FDIC branch locations for absorption**, as the spec names. After a merger
   the acquired bank's offices become the acquirer's branches, so a successor
   with a branch at the predecessor's street is the evidence an institution
   record lacks. Without it, absorption recall cannot pass the roughly 30% of
   links that share a name word, a street or a town at all.
2. **More `explains` rules**, config only: a shared `NEWCERT` and `NEWCERT`
   read in reverse. The shared `ULTCERT` rule this item first named is now in
   both sweeps (`fdic-merger-tree`). Measure the rest on the tune half first,
   since they change both sweeps' precision denominators.
3. **R6 for reappearance**, if recall matters more than run time and 0.03 of
   precision at 0.70. The candidate cost is the thing to address first.

Metric baselines for both steps were written from the final runs with
`scripts/run_metrics.py --write-baseline` to
`data/precision/baseline-bank-reappearance.json` and
`data/precision/baseline-bank-absorption.json` (local and gitignored, like
CMS-Providers' baseline), so `validate` no longer warns that none exist.
