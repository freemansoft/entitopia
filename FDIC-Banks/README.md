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

`WEBADDR.normalized` uses the `url_normalized` analyzer: lowercased, scheme,
leading `www.` and trailing slashes removed, so `http://www.example.com/` and
`example.com` both analyze to `example.com` (checked with `_analyze`). The
`exact-identifier` signal and its candidate seed read `WEBADDR.keyword` (exact
and case-folded in Python), not this subfield, so a scheme or `www.` difference
between two records still keeps them apart in the sweeps.

## Sweeps

Two sweeps over `institutions-000001` partition time at one overlap value
(`-180` days, the reappearance `min_gap_days`), so each succession is searched
by exactly one of them (`tests/test_succession_window_pairs.py`):

| Step                | Gap window (successor `ESTYMD` minus predecessor `ENDEFYMD`) | Undated candidates | Temporal signal                  |
| ------------------- | ------------------------------------------------------------ | ------------------ | -------------------------------- |
| `bank-reappearance` | -180 to +365 days                                            | excluded           | yes, `before_shutdown_scale` 1.0 |
| `bank-absorption`   | up to -181 days, no lower bound                              | kept               | none                             |

A successor created more than 365 days after the shutdown is in neither window,
by design. The truth rule is `NEWCERT`; `ULTCERT` (end of the merger chain) and
a shared holding-company id `RSSDHCR` are `explains` rules, which keep a pair out
of the findings without counting toward recall. Placeholders in `ignore_values`:
`0` on `NEWCERT`, `ULTCERT` and `RSSDHCR` (no `RSSDHCR` is `0` in the data; the
shortest value has seven digits, so the entry is only a guard). `max_shared_records`
is not set for `RSSDHCR`: it applies to signal fields only, and `RSSDHCR` is read
by a declared rule, not a signal.

First run, 2026-11-26, starting weights copied from `chameleon-detection` and
renormalized over the signals present (not yet tuned; Task 8 tunes them):

| Step                | Pairs   | `truth_links` | `truth_retrieved` | `truth_emitted` | Undated predecessors skipped | Truncated candidate sets |
| ------------------- | ------- | ------------- | ----------------- | --------------- | ---------------------------- | ------------------------ |
| `bank-reappearance` | 14,911  | 22,692        | 927               | 922             | 22                           | 0                        |
| `bank-absorption`   | 151,938 | 22,692        | 4,353             | 2,597           | 22                           | 1,056                    |

`truth_links` is every `NEWCERT` target in the index and counts the same in both
sweeps; each sweep can only retrieve the ones inside its own window, so these
are not recall figures. Absorption hit the `max_candidates` ceiling on 1,056 of
its 23,634 predecessors. Neither run reported a lookup, scoring or index error.
