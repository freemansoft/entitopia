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
| Blank `ENDEFYMD`                        | 0                                                                 |
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
   on failure. Nothing guards the pipeline itself.
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
