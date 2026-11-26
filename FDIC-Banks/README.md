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

Active banks carry `ENDEFYMD` `12/31/9999` as a "not ended" sentinel, and so do
22 inactive banks. After the load these 22 are indistinguishable by date from
live banks. No inactive bank lacks `ENDEFYMD` outright.

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

- 23,347 succession events, one per outgoing bank.
- 21,384 have an `OUT_CERT` present in the institutions table, and 21,361 of
  those (99.89%) have a `NEWCERT` equal to the surviving or acquiring CERT.
- 1,963 events (8.4%) name an outgoing bank that the institutions endpoint does
  not return at all, so they can never be tested against `NEWCERT`. Counted
  against all events, coverage is 91.5%.
- The 23 misses all have a non-zero `NEWCERT` that differs from the event.
