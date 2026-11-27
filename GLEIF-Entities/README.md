# GLEIF-Entities

The GLEIF golden copy: every Legal Entity Identifier (Level 1) and the
parent and fund relationships between them (Level 2). The matcher is scored
against the successions GLEIF itself records: an inactive entity's
`SuccessorEntity` names the LEI that took over its business.

Everything below is an aggregate. No entity, LEI or address is named, except
the registered-agent addresses in "Shared legal addresses", which are named
because they are shared values to be filtered out, not matches.

## Fetching

```bash
.venv/bin/python GLEIF-Entities/fetch_gleif.py        # about 2 minutes
.venv/bin/python GLEIF-Entities/prepare_gleif.py      # about 4 minutes
.venv/bin/python GLEIF-Entities/prepare_gleif.py --print-header
```

`fetch_gleif.py` asks
`https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest` for the
latest publish, which returns `data.lei2`, `data.rr` and `data.repex`, each with
`full_file.{csv,json,xml}.url` and a `delta_files` block. It downloads the two
`full_file.csv` zips. The file names carry the publish timestamp
(`<yyyymmdd>-<hhmm>-gleif-goldencopy-lei2-golden-copy.csv.zip`), so they are
resolved on every run, never hard-coded. No key; GLEIF publishes the golden
copy under CC0, three times a day.

| File                | Zip on 2026-11-26 | Inside         | Records   | Floor |
| ------------------- | ----------------- | -------------- | --------- | ----- |
| `data/raw/lei2.zip` | 488 MB            | one 5.1 GB CSV | 3,508,047 | 10 MB |
| `data/raw/rr.zip`   | 23 MB             | one 246 MB CSV | 494,092   | 1 MB  |

`prepare_gleif.py` streams both zips (never extracting them) into
`data/entities/entities.csv` (734 MB) and writes its counters to
`data/entities/prepare-stats.json`. It reads the Level 1 file twice: once to
collect the successor map that `SUCCESSOR_CHAIN_END` needs, once to write rows.

Disk, checked 2026-11-26 before the download: about 1 TB free on the host and
75.5 GB available on the Colima VM's Elasticsearch data disk, against about
20 GB needed for the raw zips, the CSV and the index.

## Columns

Source names were read from the 2026-11-26 headers (LEI-CDF 3.1, RR-CDF 2.1)
and are pinned in `EntityColumns` and `RelationshipColumns`. The prepare step
refuses a header missing any of them.

| Column                       | Source                                                                                                                                  |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `LEI`                        | `LEI`                                                                                                                                   |
| `LEGAL_NAME`                 | `Entity.LegalName`                                                                                                                      |
| `OTHER_NAMES` (list)         | `Entity.OtherEntityNames.OtherEntityName.1-5` and the five transliterated names                                                         |
| `LEGAL_*`, `HQ_*`            | `Entity.LegalAddress.*` and `Entity.HeadquartersAddress.*`: `FirstAddressLine` as the street, `City`, `Region`, `Country`, `PostalCode` |
| `CATEGORY`                   | `Entity.EntityCategory`                                                                                                                 |
| `LEGAL_FORM`                 | `Entity.LegalForm.EntityLegalFormCode` (an entity legal form, or ELF, code)                                                             |
| `ENTITY_STATUS`              | `Entity.EntityStatus`                                                                                                                   |
| `REGISTRATION_STATUS`        | `Registration.RegistrationStatus`                                                                                                       |
| `CREATION_DATE`              | `Entity.EntityCreationDate`, truncated to the calendar day                                                                              |
| `SHUTDOWN_DATE`              | `Entity.EntityExpirationDate`, else the latest completed terminal event (see "Shutdown date")                                           |
| `SUCCESSOR_LEI` (list)       | `Entity.SuccessorEntity.1-5.SuccessorLEI`, cleaned                                                                                      |
| `SUCCESSOR_CHAIN_END` (list) | computed: the LEIs reached by following `SUCCESSOR_LEI` to its end                                                                      |
| `DIRECT_PARENT_LEI` (list)   | Level 2 `IS_DIRECTLY_CONSOLIDATED_BY`                                                                                                   |
| `ULTIMATE_PARENT_LEI` (list) | Level 2 `IS_ULTIMATELY_CONSOLIDATED_BY`                                                                                                 |
| `BRANCH_OF_LEI` (list)       | Level 2 `IS_INTERNATIONAL_BRANCH_OF`                                                                                                    |
| `FUND_MANAGER_LEI` (list)    | Level 2 `IS_FUND-MANAGED_BY`                                                                                                            |
| `UMBRELLA_FUND_LEI` (list)   | Level 2 `IS_SUBFUND_OF`                                                                                                                 |
| `MASTER_FUND_LEI` (list)     | Level 2 `IS_FEEDER_TO`                                                                                                                  |

List columns are joined with `|`; `EntityColumns.list_valued()` names them for
the ingest pipeline. In Level 2 the start node is the child and the end node
the parent. Every row's node type is `LEI` (494,092 of 494,092).

## Profile (publish of 2026-11-26 16:00)

`scripts/profile_dataset.py --key LEI` over `entities.csv`: 3,508,047 rows,
`LEI` unique. Blank shares: `OTHER_NAMES` 83.8%, `LEGAL_REGION` 26.0%,
`CREATION_DATE` 17.1%, `SHUTDOWN_DATE` 92.6%, `SUCCESSOR_LEI` 99.0%,
`SUCCESSOR_CHAIN_END` 98.2%, `DIRECT_PARENT_LEI` 96.4%, `ULTIMATE_PARENT_LEI`
96.2%. Postal codes carry leading zeros (179,737 legal, 195,415 headquarters)
and `LEGAL_FORM` mixes numeric-looking codes with letters, so all of them must
be mapped as `keyword`.

| Value                                  | All records | US inactive |
| -------------------------------------- | ----------- | ----------- |
| Records                                | 3,508,047   | 19,413      |
| `CATEGORY` GENERAL                     | 3,107,633   | 14,454      |
| `CATEGORY` FUND                        | 256,699     | 4,944       |
| `CATEGORY` SOLE_PROPRIETOR             | 134,626     | 12          |
| `CATEGORY` RESIDENT_GOVERNMENT_ENTITY  | 7,034       | 2           |
| `CATEGORY` BRANCH                      | 1,966       | 1           |
| `CATEGORY` INTERNATIONAL_ORGANIZATION  | 89          | 0           |
| `ENTITY_STATUS` ACTIVE                 | 3,238,245   | –           |
| `ENTITY_STATUS` INACTIVE               | 260,669     | 19,413      |
| `ENTITY_STATUS` NULL                   | 9,133       | –           |
| `REGISTRATION_STATUS` ISSUED           | 2,011,759   | 0           |
| `REGISTRATION_STATUS` LAPSED           | 1,225,741   | 0           |
| `REGISTRATION_STATUS` RETIRED          | 261,036     | 19,413      |
| `REGISTRATION_STATUS` DUPLICATE        | 6,527       | 0           |
| `REGISTRATION_STATUS` ANNULLED         | 1,804       | 0           |
| `REGISTRATION_STATUS` PENDING_TRANSFER | 797         | 0           |
| `REGISTRATION_STATUS` PENDING_ARCHIVAL | 383         | 0           |

US means `LEGAL_COUNTRY` `US` (366,339 records). The 19,413 US inactive records
match the count the GLEIF API reported the same day.

**US inactive records:**

| Fact                                                  | Value                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------- |
| Blank `CREATION_DATE`                                 | 6,320 (32.6%)                                                    |
| Blank `SHUTDOWN_DATE`                                 | 21 (0.1%)                                                        |
| Naming a `SUCCESSOR_LEI`                              | 1,840 (9.5%), one successor each                                 |
| Successor present in the file                         | 1,840 of 1,840 links                                             |
| Successor with a `CREATION_DATE`                      | 1,670 of 1,840 links (90.8%); 1,282 of 1,401 distinct successors |
| `REGISTRATION_STATUS` of the successor-naming records | RETIRED, all 1,840                                               |
| Successor's `ENTITY_STATUS`                           | ACTIVE 1,206, INACTIVE 193, NULL 2 (distinct successors)         |
| Successor's `LEGAL_COUNTRY`                           | US 1,395 of 1,401 distinct successors                            |
| Identical `LEGAL_NAME` on both sides                  | 43 links                                                         |
| Excluding `FUND` and `BRANCH`                         | 14,468 records, 1,120 naming a successor                         |

**Stop gates: all pass.**

| Gate                                               | Measured                                     | Limit        |
| -------------------------------------------------- | -------------------------------------------- | ------------ |
| Successor targets lacking `CREATION_DATE`          | 9.2% of links, 8.5% distinct                 | at most half |
| US inactive records lacking `SHUTDOWN_DATE`        | 0.1%                                         | at most half |
| US inactive records naming a successor in the file | 1,840 (1,120 after the population exclusion) | at least 200 |

### Prepare counters

| Counter                 | Count   | Meaning                                                             |
| ----------------------- | ------- | ------------------------------------------------------------------- |
| `self_successor`        | 0       | successor equal to the record's own LEI, dropped                    |
| `successor_without_lei` | 18,373  | successor named with no LEI, not written; no truth link can hold it |
| `successor_bad_format`  | 0       | successor LEI of the wrong shape, dropped                           |
| `separator_replaced`    | 416     | a `\|` inside a value, replaced by a space                          |
| `undated_creation`      | 599,199 | no parseable `Entity.EntityCreationDate` (all records)              |
| `no_shutdown_date`      | 2,107   | inactive records with no end date and no completed terminal event   |

### Successor chains

`successor_chain_ends` over the cleaned successor map (every record, not only
US inactive ones): 62,911 chain members, 130 records naming more than one
successor, and **0 cycles**. Steps from a member to its farthest end:

| Steps | Members |
| ----- | ------- |
| 0     | 27,349  |
| 1     | 34,070  |
| 2     | 1,412   |
| 3     | 72      |
| 4     | 6       |
| 5     | 2       |

The 1,492 members two or more steps from their end are the pairs the
`gleif-successor-tree` explains rule exists for: without a shared chain end,
a matched pair two steps apart would be reported as an undeclared succession.

### Relationships

Level 2 rows by type and relationship status. Every type and status is kept;
306,855 children carry at least one parent column.

| Type                            | ACTIVE  | INACTIVE | NULL |
| ------------------------------- | ------- | -------- | ---- |
| `IS_DIRECTLY_CONSOLIDATED_BY`   | 127,735 | 21       | 2    |
| `IS_ULTIMATELY_CONSOLIDATED_BY` | 133,927 | 36       | 4    |
| `IS_FUND-MANAGED_BY`            | 153,459 | 1        | 195  |
| `IS_SUBFUND_OF`                 | 75,124  | 0        | 171  |
| `IS_INTERNATIONAL_BRANCH_OF`    | 2,007   | 1        | 0    |
| `IS_FEEDER_TO`                  | 1,409   | 0        | 0    |

## Decisions from the profile

- **Duplicate registrations exist, and the population excludes them.** 6,527
  records carry `REGISTRATION_STATUS` `DUPLICATE` (1,441 of them US), and every
  one names another LEI as its successor, the registration it duplicates: one
  legal entity registered twice, not a succession, whose identical name would read as easy recall. All
  6,527 have `ENTITY_STATUS` `NULL`, so a population of `INACTIVE` entities
  already leaves them out. The sweep population still excludes them explicitly
  with a `not` clause on `REGISTRATION_STATUS` `DUPLICATE`, so a later publish that
  marks a duplicate inactive cannot bring one in silently. The 43 identical-name
  links among the 1,840 are not duplicates by GLEIF's own status (all
  `RETIRED`), and only 1 of the 30 with both creation dates shares the date, so
  they read as re-registrations after a conversion or move. They stay truth
  links.
- **The `FUND` and `BRANCH` exclusion stays.** Together they are 4,945 of the
  19,413 US inactive records (25.5%, almost all `FUND`), far above the 5% below
  which the spec says to drop it.
- **Every further relationship type is kept as a parent column**:
  `BRANCH_OF_LEI`, `FUND_MANAGER_LEI`, `UMBRELLA_FUND_LEI` and
  `MASTER_FUND_LEI`, beside `DIRECT_PARENT_LEI` and `ULTIMATE_PARENT_LEI`. Each
  lets an explains rule recognize two funds of one manager, a sub-fund and its
  umbrella, a feeder and its master, or a branch and its head office as
  related rather than as a finding. The excluded categories still appear as
  candidates, so their links matter there.

## Shutdown date

`Entity.EntityExpirationDate` is blank on all 3,508,047 records, so in practice
the shutdown date is the effective date of the latest completed terminal event.
The terminal types, from the profile of every event type:

| Type                       | Completed on inactive records |
| -------------------------- | ----------------------------- |
| `DISSOLUTION`              | 197,494                       |
| `MERGERS_AND_ACQUISITIONS` | 39,844                        |
| `LIQUIDATION`              | 16,116                        |
| `ABSORPTION`               | 6,767                         |
| `BREAKUP`                  | 147                           |

`ABSORPTION` and `BREAKUP` end the entity by definition and were added to the
expected list. `BANKRUPTCY` (1,075 completed on inactive records) and
`INSOLVENCY` (175) are not terminal: an entity survives both, and a later
dissolution or liquidation dates its end. `DEMERGER`, `SPINOFF` and the
`TRANSFORMATION_*` types leave the entity in place. Events with status
`IN_PROGRESS` or `WITHDRAWN_CANCELLED` never date a shutdown.

## Shared legal addresses

The 10 most shared US legal `FirstAddressLine` plus city values are
registered-agent and filing-service addresses, carried by entities that have
nothing else in common. They are named here because they are values to be
excluded, not matches:

| Street / city                                              | Entities |
| ---------------------------------------------------------- | -------- |
| C/O THE CORPORATION TRUST COMPANY / WILMINGTON             | 45,922   |
| C/O CORPORATION SERVICE COMPANY / WILMINGTON               | 44,099   |
| C/O COGENCY GLOBAL INC. / DOVER                            | 7,770    |
| 251 LITTLE FALLS DRIVE / WILMINGTON                        | 5,107    |
| C/O NATIONAL REGISTERED AGENTS, INC. / WILMINGTON          | 4,786    |
| C/O CAPITOL SERVICES, INC. / DOVER                         | 3,573    |
| C/O MAPLES FIDUCIARY SERVICES (DELAWARE) INC. / WILMINGTON | 2,503    |
| CORPORATION TRUST CENTER 1209 ORANGE ST / WILMINGTON       | 2,406    |
| C/O REGISTERED AGENT SOLUTIONS, INC. / DOVER               | 2,313    |
| C/O C T CORPORATION SYSTEM / BOSTON                        | 2,029    |

287,559 of the 366,339 US legal addresses (78.5%) have a first line starting
`C/O`, against 62,635 headquarters addresses (17.1%). That is why the address
signal reads the headquarters address, and why any legal-address signal needs
a `max_shared_records` limit.

## Hazards

1. **An offset date converted to UTC moves a day.** GLEIF writes
   `1996-01-01T00:00:00+01:00`; in UTC that is 1995-12-31, enough to move a
   same-day successor between the two sweeps. `iso_day` truncates to the
   calendar day. Guarded by `tests/test_gleif_prepare.py`
   (`test_offset_date_keeps_calendar_day`).
2. **A self-successor is a recall miss nothing can fix**, because the
   candidate search excludes the predecessor's own id. Dropped and counted (0
   in this publish). Guarded by `test_self_successor_dropped_and_counted`.
3. **A renamed or missing column would load as blanks**: millions of entities
   with no successor or date, and a successful load. `read_zip_csv` refuses a
   header missing any configured column, and a zip that holds anything but one
   CSV (FDIC hazard 3's lesson). Guarded by `test_missing_column_refused` and
   `test_zip_must_hold_one_csv`.
4. **A stub, an error page or a cut-off transfer saved as data** (the CMS
   incidents). `download` refuses a non-200 status, a body starting with `<`,
   a file below the floor and a file that is not a zip. Guarded by
   `tests/test_gleif_fetch.py`.
5. **Two files from different publishes.** The prepare step joins Level 2 onto
   Level 1, so an old `rr.zip` kept beside a new `lei2.zip` would attach stale
   parents and report success. A failed download removes any earlier copy of
   its file as well as the partial one, so the prepare step cannot run on a
   mixed pair. Guarded by `test_failed_download_leaves_nothing`.
6. **A delta file mistaken for the full file.** The publish JSON lists
   last-day and last-week deltas whose zips are valid and well above the
   floors. Only `full_file.csv` is read. Guarded by
   `test_resolves_full_file_urls`.
7. **A `|` inside a value would split it in the ingest pipeline.** 416 values
   carried one; each became a space. Guarded by
   `test_separator_in_value_replaced_and_counted`.
8. **The surviving side of a merger carries the merger event too**: 3,574
   completed `MERGERS_AND_ACQUISITIONS` events sit on active records. A
   shutdown date taken from it would mark a live acquirer as ended, so events
   date only inactive records. Guarded by
   `test_shutdown_prefers_own_end_date_then_latest_terminal_event`.
9. **Successor cycles.** None in this publish (0). A cycle would loop forever;
   it is cut and its members take its smallest LEI as their chain end. Guarded
   by `test_chain_end_survives_a_cycle`.
10. **The CSV keeps only five of each repeated element.** Successors, names
    and events past the fifth are not in the file at all. 14 records fill the
    fifth successor slot and 21,620 the fifth event slot, so a terminal event
    past the fifth would be lost; inactive records left without any shutdown
    date number 2,107. Nothing guards this: only the JSON or XML golden copy
    would carry the rest.
11. **Successors named without an LEI** (18,373) cannot be truth links. They
    are counted, not guessed at.
