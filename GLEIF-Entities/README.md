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

| Column                       | Source                                                                                                                                                                                   |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LEI`                        | `LEI`                                                                                                                                                                                    |
| `LEGAL_NAME`                 | `Entity.LegalName`                                                                                                                                                                       |
| `OTHER_NAMES` (list)         | `Entity.OtherEntityNames.OtherEntityName.1-5` and the five transliterated names                                                                                                          |
| `LEGAL_*`, `HQ_*`            | `Entity.LegalAddress.*` and `Entity.HeadquartersAddress.*`: `FirstAddressLine` as the street (`AdditionalAddressLine.1` after a care-of line), `City`, `Region`, `Country`, `PostalCode` |
| `CATEGORY`                   | `Entity.EntityCategory`                                                                                                                                                                  |
| `LEGAL_FORM`                 | `Entity.LegalForm.EntityLegalFormCode` (an entity legal form, or ELF, code)                                                                                                              |
| `ENTITY_STATUS`              | `Entity.EntityStatus`                                                                                                                                                                    |
| `REGISTRATION_STATUS`        | `Registration.RegistrationStatus`                                                                                                                                                        |
| `CREATION_DATE`              | `Entity.EntityCreationDate`, truncated to the calendar day                                                                                                                               |
| `SHUTDOWN_DATE`              | `Entity.EntityExpirationDate`, else the latest completed terminal event (see "Shutdown date")                                                                                            |
| `SUCCESSOR_LEI` (list)       | `Entity.SuccessorEntity.1-5.SuccessorLEI`, cleaned                                                                                                                                       |
| `SUCCESSOR_CHAIN_END` (list) | computed: the LEIs reached by following `SUCCESSOR_LEI` to its end                                                                                                                       |
| `DIRECT_PARENT_LEI` (list)   | Level 2 `IS_DIRECTLY_CONSOLIDATED_BY`                                                                                                                                                    |
| `ULTIMATE_PARENT_LEI` (list) | Level 2 `IS_ULTIMATELY_CONSOLIDATED_BY`                                                                                                                                                  |
| `BRANCH_OF_LEI` (list)       | Level 2 `IS_INTERNATIONAL_BRANCH_OF`                                                                                                                                                     |
| `FUND_MANAGER_LEI` (list)    | Level 2 `IS_FUND-MANAGED_BY`                                                                                                                                                             |
| `UMBRELLA_FUND_LEI` (list)   | Level 2 `IS_SUBFUND_OF`                                                                                                                                                                  |
| `MASTER_FUND_LEI` (list)     | Level 2 `IS_FEEDER_TO`                                                                                                                                                                   |

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

| Counter                   | Count   | Meaning                                                               |
| ------------------------- | ------- | --------------------------------------------------------------------- |
| `self_successor`          | 0       | successor equal to the record's own LEI, dropped                      |
| `successor_without_lei`   | 18,373  | successor named with no LEI, not written; no truth link can hold it   |
| `successor_bad_format`    | 0       | successor LEI of the wrong shape, dropped                             |
| `separator_replaced`      | 418     | a `\|` inside a value, replaced by a space                            |
| `undated_creation`        | 599,199 | no parseable `Entity.EntityCreationDate` (all records)                |
| `no_shutdown_date`        | 2,107   | inactive records with no end date and no completed terminal event     |
| `care_of_street_replaced` | 695,803 | a care-of first line replaced by the next address line (legal and HQ) |
| `care_of_no_street`       | 100,730 | a care-of first line with no next line; street left blank             |

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

Most US legal addresses are a registered agent's. 287,464 of the 366,339 US
legal addresses (78.5%) have a care-of first line (`C/O ...` or `CARE OF ...`),
against 62,631 headquarters addresses (17.1%). The prepare step reads the
street from the next address line for those (276,015 legal and 54,038
headquarters addresses in the US) and leaves it blank when there is none
(11,449 legal and 8,593 headquarters). Worldwide the counts are 695,803
replaced and 100,730 blanked, legal and headquarters together.

After that, the 10 most shared US legal street plus city values are
registered-agent and filing-service street addresses, carried by entities
that have nothing else in common. They are named here because they are
values to be excluded, not matches:

| Street / city                                        | Entities |
| ---------------------------------------------------- | -------- |
| 251 LITTLE FALLS DRIVE / WILMINGTON                  | 46,424   |
| CORPORATION TRUST CENTER / WILMINGTON                | 36,878   |
| CORPORATION TRUST CENTER 1209 ORANGE ST / WILMINGTON | 10,795   |
| 850 NEW BURTON ROAD / DOVER                          | 6,395    |
| 1209 ORANGE STREET / WILMINGTON                      | 5,261    |
| 2711 CENTERVILLE ROAD / WILMINGTON                   | 3,410    |
| `<AGENT-STREET-A>` / DOVER                           | 2,747    |
| 850 NEW BURTON ROAD SUITE 201 / DOVER                | 2,248    |
| `<AGENT-STREET-B>` / WILMINGTON                      | 2,141    |
| `<AGENT-STREET-C>` / WILMINGTON                      | 1,948    |

Three streets are shown as placeholders. Each is a filing agent's address
here, but in another project's index one carrier or bank at the same street
was matched by an accusatory sweep, so naming the street would name it too.

One agent's address appears under several spellings (with and without the
building name, the suite, or `STREET` abbreviated), so an exact-value ignore
list would need every spelling. That is why the address signal reads the
headquarters address, and why any legal-address signal needs a
`max_shared_records` limit rather than an ignore list.

## Name stop list

The phonetic analyzers drop the words below (`entity_word_stop` in
`configuration/entities/index-settings.json`), so two unrelated entities do not
score as alike because both are an "LLC" or a "GmbH". The analyzers themselves
are FDIC-Banks' with this list in place of the bank words. Measured 2026-11-26,
before the first load, as the share of `LEGAL_NAME` values containing each
word (a word counted once per name), in two populations: the 366,339 US
records, where the predecessors are, and all 3,508,047 records, where the
candidates come from. The list is the union of both.

Words are counted as the stop filter sees them: the `name_phonetic` chain up
to the filter (standard tokenizer, `icu_normalizer`, `icu_folding`,
`punct_white`), applied by Elasticsearch to a scratch index of the names and
counted with a terms aggregation. That matters for dotted spellings: the
standard tokenizer keeps `L.L.C.` as one token and `punct_white` turns its dots
into spaces, so the stopword that matches it is `l l c`, with spaces. A
stopword written `l.l.c.` would never match anything. The four single
characters are the ideographs of the Chinese legal form for a limited company,
which the standard tokenizer splits one per token.

| Word         | US     | All    | Word            | US    | All   |
| ------------ | ------ | ------ | --------------- | ----- | ----- |
| `llc`        | 52.35% | 5.56%  | `co`            | 1.01% | 1.65% |
| `inc`        | 11.90% | 2.21%  | `management`    | 1.64% | 1.07% |
| `limited`    | 1.15%  | 10.41% | `equity`        | 1.62% | 0.68% |
| `fund`       | 8.35%  | 3.62%  | `s l`           | 0.00% | 1.59% |
| `gmbh`       | 0.00%  | 5.55%  | `investment`    | 1.57% | 0.96% |
| `trust`      | 5.10%  | 3.21%  | `corporation`   | 1.55% | 0.44% |
| `lp`         | 4.85%  | 0.80%  | `a`             | 0.96% | 1.54% |
| `b v`        | 0.00%  | 4.79%  | `realty`        | 1.47% | 0.21% |
| `private`    | 0.40%  | 4.59%  | `associates`    | 1.45% | 0.26% |
| `l p`        | 4.19%  | 0.72%  | `oy`            | 0.00% | 1.44% |
| `holdings`   | 4.06%  | 1.32%  | `invest`        | 0.11% | 1.42% |
| `s r l`      | 0.00%  | 3.99%  | `investments`   | 1.40% | 1.12% |
| `ltd`        | 0.86%  | 3.91%  | `global`        | 1.38% | 1.15% |
| `holding`    | 0.61%  | 3.80%  | `series`        | 1.37% | 0.24% |
| `限`         | 0.00%  | 3.03%  | `s a`           | 0.00% | 1.34% |
| `有`         | 0.00%  | 3.02%  | `s`             | 0.62% | 1.32% |
| `公`         | 0.00%  | 3.01%  | `property`      | 1.30% | 0.34% |
| `司`         | 0.00%  | 3.01%  | `iii`           | 1.27% | 0.35% |
| `owner`      | 2.93%  | 0.31%  | `international` | 1.24% | 0.93% |
| `ab`         | 0.15%  | 2.83%  | `bank`          | 1.23% | 0.33% |
| `partners`   | 2.75%  | 0.70%  | `income`        | 1.22% | 0.34% |
| `etf`        | 2.74%  | 0.50%  | `apartments`    | 1.21% | 0.13% |
| `ii`         | 2.71%  | 0.77%  | `portfolio`     | 1.19% | 0.34% |
| `company`    | 2.69%  | 1.15%  | `and`           | 1.19% | 1.04% |
| `l l c`      | 2.61%  | 0.29%  | `credit`        | 1.16% | 0.38% |
| `capital`    | 2.55%  | 1.55%  | `partnership`   | 1.15% | 0.24% |
| `of`         | 2.32%  | 0.70%  | `real`          | 1.12% | 0.40% |
| `aps`        | 0.00%  | 2.31%  | `kg`            | 0.01% | 1.10% |
| `properties` | 2.22%  | 0.42%  | `park`          | 1.08% | 0.22% |
| `the`        | 2.19%  | 2.16%  | `energy`        | 1.05% | 0.49% |
| `sl`         | 0.07%  | 2.19%  | `estate`        | 1.04% | 0.38% |
| `i`          | 1.92%  | 0.99%  | `di`            | 0.01% | 1.02% |
| `as`         | 0.07%  | 1.90%  | `cap`           | 1.01% | 0.19% |
| `street`     | 1.89%  | 0.27%  | `corp`          | 0.90% | 0.28% |
| `group`      | 1.78%  | 1.12%  | `srl`           | 0.00% | 0.89% |
| `de`         | 0.21%  | 1.76%  | `sa`            | 0.06% | 0.77% |

The cut is every word at or above 1% in either population. `corp`, `srl` and
`sa` are also stopped although each is under 1%, because each is another
spelling of a stopped form (`corporation`, `s r l`, `s a`). As with FDIC the
list holds generic words as well as legal forms (`street`, `owner`, `ii`,
`properties`); a word in more than 1% of names identifies nobody whatever it
means. A name made only of stopped words has no phonetic tokens and the phonetic
signals read it as not evaluable; in a seeded sample of 5,000 names that was 2
US names (0.04%) and 4 worldwide (0.08%). The `name-token` signal reads the
unstopped `clean` subfield and still scores them.

The legal forms on this list that the anonymization scanner's suffix list
lacked were added to it in the same change (`LIMITED`, `HOLDING`,
`PARTNERSHIP`, `B.V.`, `S.R.L.`, `S.L.`, `SA`, `AB`, `APS`, `AS`, `OY`, `KG`
and their spellings), so a GLEIF name ending in one is extracted and looked up
(`tests/test_scan_identifiers_leis.py`,
`test_gleif_stop_list_legal_forms_are_extracted`).

## Phonetic encoders

`name_phonetic` uses `double_metaphone` at its default code length of 4, and
`name_phonetic_bm` uses `beider_morse` pinned to English and Spanish, chosen
with `scripts/compare_phonetic_encoders.py` before the load. The script was run
on `LEGAL_NAME` in the scratch index of all 3,508,047 names used for the stop
list, with the stop list applied (`--base-filters
icu_normalizer,icu_folding,punct_white,entity_word_stop`). All-edits AUC on 600
names, three seeds, 2026-11-26:

| Encoder                            | Seed 20261126 | Seed 7 | Seed 99 |
| ---------------------------------- | ------------- | ------ | ------- |
| `double_metaphone`, max_code_len 4 | 0.860         | 0.871  | 0.860   |
| `double_metaphone`, max_code_len 6 | 0.851         | 0.863  | 0.854   |
| `metaphone`                        | 0.851         | 0.862  | 0.852   |
| `beider_morse` english+spanish     | 0.772         | 0.787  | 0.782   |
| no encoder (cleaned tokens)        | 0.737         | 0.745  | 0.748   |

The order held on every seed, so the code length is 4, as CMS-Providers chose
for hospital names, rather than FDIC-Banks' and DOT-Commercial's 6. The margin
is under 0.01, a small gain rather than a fix. Recall at a 1% false-match rate
agrees (66.6%, 68.2% and 67.3% at length 4 against 66.3%, 66.4% and 66.2% at
6). Beider-Morse scores below every metaphone variant on its own but is kept as
the second arm: it is its own subfield, so a sweep can weight it on its
own, and DOT-Commercial showed it catching cross-language respellings that
double-metaphone keeps apart ("Name and address analyzers" there), which
matters more in a register that is mostly non-US names. The sampled names are
only those written in Latin capitals, digits and common punctuation, because
the script's edits are defined over A-Z.

## Loading

```bash
.venv/bin/python scripts/check_mapping_coverage.py --project=GLEIF-Entities --step=entities
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=entities-ingestion-setup
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=entities   # about 3 minutes
```

The coverage check reports 0 of 27 columns unpinned. It lists `HQ_LOCAL` and
`LEGAL_LOCAL` as "pinned but not in CSV", which is expected: the ingest
pipeline derives both.

Loaded 2026-11-26 into `entities-2026.11.26-000001`, behind the alias
`entities-000001`: 3,508,047 documents from 3,508,047 CSV rows, no failed
documents, in 174 seconds for the whole step (2 minutes 42 seconds of
indexing). The index is 5.4 GB on one shard once merges settled (5,375 MB; the
replica is unassigned on the one-node cluster). `CREATION_DATE` and
`SHUTDOWN_DATE` are mapped as `date` (`strict_date_optional_time||yyyy-MM-dd`),
the parent and successor columns as `keyword` arrays.

Checked against the CSV after the load:

| Field                 | CSV rows with a value | Indexed documents with it |
| --------------------- | --------------------- | ------------------------- |
| `CREATION_DATE`       | 2,908,848             | 2,908,848                 |
| `SHUTDOWN_DATE`       | 258,562               | 258,562                   |
| `SUCCESSOR_LEI`       | 35,562                | 35,562                    |
| `SUCCESSOR_CHAIN_END` | 62,911                | 62,911                    |
| `DIRECT_PARENT_LEI`   | 127,758               | 127,758                   |
| `ULTIMATE_PARENT_LEI` | 133,967               | 133,967                   |
| `FUND_MANAGER_LEI`    | 153,655               | 153,655                   |
| `UMBRELLA_FUND_LEI`   | 75,295                | 75,295                    |
| `BRANCH_OF_LEI`       | 2,008                 | 2,008                     |
| `MASTER_FUND_LEI`     | 1,409                 | 1,409                     |

No date was dropped by the pipeline's `on_failure`. Every list column was
split: the indexed values equal the CSV's `|`-separated values
(`SUCCESSOR_LEI` 35,723, `SUCCESSOR_CHAIN_END` 63,048), and no indexed
`SUCCESSOR_LEI` contains a `|`. Of the 19,413 US inactive records, 21 have no
`SHUTDOWN_DATE`, the profile's figure; those are the predecessors
`undated_predecessors: "skip"` will skip. 6,320 have no `CREATION_DATE`, also
as profiled.

`HQ_LOCAL` is on 2,566,272 documents and `LEGAL_LOCAL` on 2,563,963; the rest
lack a street, a city or a region (`LEGAL_REGION` is blank on 26.0% of
records, mostly outside the US). 10,809 US records have no `HQ_LOCAL`.

**The loader reads literal "NA"-like strings as missing.** `pd.read_csv`
keeps its default `na_values`, so a whole cell reading `NA`, `N/A`, `n/a`,
`NULL`, `null` or `None` loads as no value. In this file that is:

| Column(s)                      | Values read as missing                               |
| ------------------------------ | ---------------------------------------------------- |
| `ENTITY_STATUS`                | 9,133 `NULL`, GLEIF's own value; the field is absent |
| `LEGAL_COUNTRY` / `HQ_COUNTRY` | 109 / 103 `NA`, the ISO code for Namibia             |
| `LEGAL_STREET` / `HQ_STREET`   | 4,562 / 4,547, almost all `N/A`                      |
| `LEGAL_CITY` / `HQ_CITY`       | 4,385 / 4,389, almost all `N/A`                      |
| `LEGAL_POSTAL` / `HQ_POSTAL`   | 166 / 165                                            |
| `OTHER_NAMES` (whole cell)     | 25                                                   |
| `LEGAL_NAME`                   | 1 `NA`                                               |

The placeholders (`N/A` streets and cities) are better absent. They are also
why `HQ_LOCAL`, `LEGAL_LOCAL` and `OTHER_NAMES` sit 17, 20 and 25 documents
short of a strict count of non-blank CSV cells: every one of those rows has a
part that pandas read as missing. The rest is a real loss. The predecessor
population selects `INACTIVE` US records, so neither the absent `NULL` status
nor the absent Namibian country changes it, but a candidate filter or signal
that read either field would have to allow for it. A `term` query for
`ENTITY_STATUS` `NULL` finds nothing; use `must_not exists`. See hazard 17.

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
   its file as well as the partial one (`test_failed_download_leaves_nothing`),
   but a run killed between the two downloads still leaves a mixed pair. So the
   prepare step reads the publish stamp that starts each zip's member name
   (`<yyyymmdd>-<hhmm>-gleif-goldencopy-...`) and refuses two that differ, or a
   member with no stamp. Guarded by `test_mixed_publish_refused`.
6. **A delta file mistaken for the full file.** The publish JSON lists
   last-day and last-week deltas whose zips are valid and well above the
   floors. Only `full_file.csv` is read. Guarded by
   `test_resolves_full_file_urls`.
7. **A `|` inside a value would split it in the ingest pipeline.** 418 values
   carried one; each became a space. Guarded by
   `test_separator_in_value_replaced_and_counted`.
8. **The surviving side of a merger carries the merger event too**: 3,574
   completed `MERGERS_AND_ACQUISITIONS` events sit on active records. A
   shutdown date taken from it would mark a live acquirer as ended, so events
   date only inactive records. Guarded by
   `test_shutdown_prefers_own_end_date_then_latest_terminal_event`.
9. **Successor cycles.** None in this publish (0). A cycle would loop forever,
   so each is collapsed into one node. A cycle with an exit resolves to the
   ends its exit reaches; only a closed cycle gives its members its smallest
   LEI as their chain end. Guarded by `test_chain_end_survives_a_cycle` and
   `test_chain_end_of_a_cycle_with_an_exit`.
10. **The CSV keeps only five of each repeated element.** Successors, names
    and events past the fifth are not in the file at all. 14 records fill the
    fifth successor slot and 21,620 the fifth event slot, so a terminal event
    past the fifth would be lost; inactive records left without any shutdown
    date number 2,107. Nothing guards this: only the JSON or XML golden copy
    would carry the rest.
11. **Successors named without an LEI** (18,373) cannot be truth links. They
    are counted, not guessed at.
12. **A vocabulary that stops matching leaves a column blank everywhere.** A
    relationship type renamed in a new RR-CDF release would leave its parent
    column empty on every entity, and a renamed terminal event type or status
    would leave every inactive record without a shutdown date. Both would
    load. The prepare step refuses a configured relationship type that
    matches no Level 2 row, and a run in which no inactive record gets a
    `SHUTDOWN_DATE`, including one with no `INACTIVE` record at all (a renamed
    status value would make every record look active). Guarded by
    `test_relationship_type_with_no_rows_refused`,
    `test_no_dated_inactive_record_refused` and `test_no_inactive_record_refused`.
13. **A care-of first line is the agent, not the street.** Kept as the street,
    the `*_LOCAL` address fields derived from it would hold the filing agent's
    name, and every client of one agent would match every other on address.
    When the first line starts `C/O` or `CARE OF` (any case), the street is
    read from the first additional line, or left blank when there is none (see
    "Shared legal addresses" for the counts). Guarded by
    `test_care_of_street_read_from_next_line`.
14. **A street without its town matches every same-named street.** FDIC
    listed a bare "Main Street" for 1,481 banks, and compared on the street
    alone each matched every other at 1.0 across state lines (FDIC hazard 5).
    `HQ_LOCAL` and `LEGAL_LOCAL` copy FDIC's `STREET_LOCAL`:
    `<street> | <city> <region>`, set only when all three parts are present,
    because `<street> | ` would again match every namesake with no town.
    Guarded by `tests/test_gleif_pipeline.py`
    (`test_local_address_needs_city_and_region`).
15. **A date that fails the strict mapping drops the whole entity.**
    Elasticsearch rejects the document, not the value, so one malformed
    creation date would remove a predecessor or its successor from every
    sweep, and the load would report one failure among millions. Each date
    processor removes its own field on failure instead. Guarded by
    `test_date_failure_removes_field`.
16. **A `|`-joined column left unsplit is one long keyword.** A parent or
    successor LEI inside it would never equal the other record's `LEI`, so
    every declared link through it would read as missing while the load
    succeeded. The pipeline splits every column `EntityColumns.list_valued()`
    names, read from the prepare script itself so a new list column cannot be
    missed. Guarded by `test_list_columns_are_split`.
17. **The loader turns a literal `NA`, `N/A` or `NULL` into no value.**
    `utils/csv_load_utils.py` reads with pandas' default `na_values`, which
    every project's load shares, so Namibia's country code `NA` and GLEIF's
    `ENTITY_STATUS` `NULL` load as absent (counts under "Loading"). Nothing
    errors and the count matches. Nothing guards this: changing the loader
    would change what the other projects load, so it is recorded rather than
    fixed here.
18. **A duplicate registration read as a succession.** A `DUPLICATE` record
    names the LEI it duplicates as its successor, so the pair is one entity
    with one name: the easiest recall there is, and none of it real. The sweep
    population excludes `REGISTRATION_STATUS` `DUPLICATE`, `CATEGORY` `FUND`
    and `CATEGORY` `BRANCH` through `not` clauses ("Decisions from the
    profile"). Guarded by `tests/test_gleif_configs.py`
    (`test_population_excludes_what_profiling_found`, whose expected
    exclusions are written from this README, not read from the config) and by
    the literal query pinned in `tests/test_population.py`
    (`test_shipped_configs_build_unchanged`).
19. **A list-valued name field failed every candidate search it was in.**
    `OTHER_NAMES` is a list, and the name signals passed it whole as a `match`
    query, which Elasticsearch rejects (`[match] unknown token [START_ARRAY]`).
    The rejection failed the predecessor's whole candidate query, address seed
    included, so every predecessor with another name got no candidates; the
    sweep logged one error line per predecessor and carried on. Found in the
    first timed sample. The name signals now seed one clause per name; a
    scalar field builds the same clause as before, so FDIC's and
    DOT-Commercial's queries are unchanged. Guarded by `tests/test_signals.py`
    (`test_list_valued_name_field_seeds_one_clause_per_name`).
20. **A registered agent's address on a legal-address signal links every
    client of the agent** (see "Shared legal addresses"). The address signal
    reads `HQ_LOCAL`, and any signal reading a `LEGAL_*` address field must
    carry a `max_shared_records` limit that the corpus frequency scan
    actually enforces. Guarded by `test_legal_address_is_rarity_limited`,
    which passes vacuously today because no signal reads a legal address.
    Shared headquarters addresses are not limited: see "Shared headquarters
    addresses" under "Sweeps".

## Sweeps

Two sweeps over `entities-000001` partition time at one overlap value
(`-180` days, the reappearance `min_gap_days`), as FDIC-Banks' do, so each
succession is searched by exactly one of them
(`tests/test_succession_window_pairs.py`):

| Step               | Gap window (successor `CREATION_DATE` minus predecessor `SHUTDOWN_DATE`) | Undated candidates | Temporal signal                                      |
| ------------------ | ------------------------------------------------------------------------ | ------------------ | ---------------------------------------------------- |
| `lei-reappearance` | -180 to +365 days                                                        | excluded           | yes, `max_gap_days` 365, `before_shutdown_scale` 1.0 |
| `lei-absorption`   | up to -181 days, no lower bound                                          | kept               | none                                                 |

The population (`us-inactive`) is `ENTITY_STATUS` `INACTIVE` and
`LEGAL_COUNTRY` `US`, less `FUND`, `BRANCH` and `DUPLICATE`: 14,468 records.
Predecessors with no `SHUTDOWN_DATE` are skipped. Candidates are drawn from
the whole register.

**Signals.** Starting weights are part 1's tuned `bank-absorption` weights:
`name-phonetic` 0.3 (`phonetic`) and 0.18 (`phonetic_bm`) and `name-token`
0.12 (`clean`), each over `LEGAL_NAME` and `OTHER_NAMES` with `cross_field`,
and `address` 0.4 on `HQ_LOCAL` (`clean` exact, `tokens` fuzzy at 0.7, state
`HQ_REGION`). `lei-reappearance` scales those by 0.73 and adds `temporal` at
0.27, the split `bank-reappearance` uses. Seeds are `name-phonetic` and
`address`, `max_candidates` 500. There is no phone, email, website or vehicle
identifier to use. No placeholder was found on the LEI columns, so
`ignore_values` is empty.

**Declared links.** `complete_truth` is false: GLEIF records a successor only
when the filer reports one. The one `truth` rule is the forward
`SUCCESSOR_LEI` key (`gleif-successor`); only a forward key rule can be truth,
because recall counts the targets a predecessor names itself
(`test_only_truth_rule_is_the_successor`). Everything else is `explains`:

| Rule                                                         | Kind                   | Field                 |
| ------------------------------------------------------------ | ---------------------- | --------------------- |
| `gleif-direct-parent` / `gleif-direct-child`                 | key, forward / reverse | `DIRECT_PARENT_LEI`   |
| `gleif-ultimate-parent` / `gleif-ultimate-child`             | key, forward / reverse | `ULTIMATE_PARENT_LEI` |
| `gleif-head-office` / `gleif-branch`                         | key, forward / reverse | `BRANCH_OF_LEI`       |
| `gleif-fund-manager` / `gleif-managed-fund`                  | key, forward / reverse | `FUND_MANAGER_LEI`    |
| `gleif-umbrella-fund` / `gleif-sub-fund`                     | key, forward / reverse | `UMBRELLA_FUND_LEI`   |
| `gleif-master-fund` / `gleif-feeder-fund`                    | key, forward / reverse | `MASTER_FUND_LEI`     |
| `gleif-shared-direct-parent`, `gleif-shared-ultimate-parent` | shared                 | the two parent fields |
| `gleif-successor-tree`                                       | shared                 | `SUCCESSOR_CHAIN_END` |

`gleif-successor-tree` is GLEIF's `fdic-merger-tree`: two entities in one
recorded chain or tree (A paired with C when A names B and B names C, or two
entities absorbed into one successor) are explained, not found.

### Shared headquarters addresses

Many headquarters addresses are shared too, though by fewer records than the
legal ones: a filing agent's street used as the headquarters, or one asset
manager's office on every fund and vehicle it runs. Counted 2026-11-26 on
`HQ_LOCAL.keyword` folded to lower case (the `clean` subfield the signal
compares is analyzed text and cannot be aggregated, so these are a lower
bound: spellings that differ only in punctuation count apart):

| Records sharing one `HQ_LOCAL` | US records (355,530 with `HQ_LOCAL`) | All records (2,566,272) |
| ------------------------------ | ------------------------------------ | ----------------------- |
| at least 300                   | 42 values, 37,796 records            | 132 values, 103,037     |
| at least 1,000                 | 7 values                             | 24 values               |
| most shared                    | 5,965                                | 6,208                   |

**No limit is set, because none would take effect.** `max_shared_records`
reaches only fields a signal reports through `exact_evidence_fields`, which
the corpus frequency scan reads. The address signal reports none and never
consults the ignored values, so an `HQ_LOCAL` entry would pass validation and
change nothing. Limiting a shared address needs the address signal to report
its exact subfield to the scan, a framework change outside this sweep.

The cost is precision, not retrieval. The address signal scores an exact
`HQ_LOCAL` match 1.0. In `lei-absorption` its weight of 0.4 alone clears
`min_total_score` 0.35, so two unrelated entities at one shared office need
only a second contributing signal (`min_signals` 2), such as a shared name
token, to be emitted; in `lei-reappearance` it carries 0.292 of the 0.35. The
shared addresses do not fill the candidate cap: see "Full sweeps".

### Timed sample and the candidate filter

A local, uncommitted `population.max_records: 1000` on both sweeps
(2026-11-26), the first 1,000 predecessors by `LEI`. Both runs validated clean
(the only warning: no metrics baseline yet).

| Step               | Wall time (`entity-match`) | Pairs | `truth_links` | `truth_retrieved` | `truth_emitted` | Undated skipped | Truncated sets | Errors |
| ------------------ | -------------------------- | ----- | ------------- | ----------------- | --------------- | --------------- | -------------- | ------ |
| `lei-absorption`   | 153 s                      | 8,715 | 73            | 19                | 17              | 2               | 957            | 0      |
| `lei-reappearance` | 127 s                      | 9,700 | 73            | 6                 | 5               | 2               | 689            | 0      |

Extrapolated to 14,468 predecessors: about 37 minutes for absorption and 31 for
reappearance, far inside the 6-hour stop gate.

**Successor category of emitted pairs:**

| Step               | `GENERAL` | `FUND` | `BRANCH` | Other | `FUND` + `BRANCH` |
| ------------------ | --------- | ------ | -------- | ----- | ----------------- |
| `lei-absorption`   | 8,017     | 629    | 7        | 62    | 636 (7.3%)        |
| `lei-reappearance` | 8,272     | 1,158  | 5        | 265   | 1,163 (12.0%)     |

**Decision: no candidate filter (plan Task 7 skipped).** The rule is set on the
absorption sample, where funds and branches are 7.3% of emitted pairs, under
the 10% at which they were judged to cost recall through the candidate cap.
The capped candidate sets were re-run with their categories read back: funds
and branches fill 10.1% of the absorption sets' slots (16.5% for
reappearance), and are the majority of a set in 49 of 959 capped absorption
sets (44 of 691 for reappearance), so funds are not what fills the cap.
The re-run also searched the 2 predecessors the sweeps skip as undated, which
is why it counts 959 and 691 capped sets and 74 truth links against the
sweeps' 957, 689 and 73.

Reappearance is above 10% (12.0%). Its funds are noise in the findings rather
than a recall cost: of the 74 truth links its sample names, none was matched by
the seeds and the window but cut by the cap. In absorption, 9 of 74 were. Those
9 are a cap cost of general entities, which a fund filter would not recover.

### Full sweeps

Run 2026-11-26 with `max_records` back to `null`, starting weights, before
any tuning. Pair indexes `lei-absorption-pairs-2026.11.26-000003` and
`lei-reappearance-pairs-2026.11.26-000002` (earlier same-day generations
were the samples, since deleted):

| Step               | Wall time   | Pairs   | `truth_links` | `truth_retrieved` | `truth_emitted` | Undated predecessors skipped | Truncated candidate sets | Errors |
| ------------------ | ----------- | ------- | ------------- | ----------------- | --------------- | ---------------------------- | ------------------------ | ------ |
| `lei-reappearance` | 31 min 53 s | 140,596 | 1,119         | 30                | 20              | 19                           | 10,046                   | 0      |
| `lei-absorption`   | 37 min 14 s | 126,177 | 1,119         | 289               | 133             | 19                           | 13,895                   | 0      |

As for FDIC, `truth_links` counts every successor a searched predecessor
names, in both sweeps, and each sweep can retrieve only those inside its own
window, so these are not recall figures. Neither run reported a lookup,
scoring or index error. Funds and branches are 8.7% of absorption's emitted
pairs (11,029) and 11.4% of reappearance's (15,986), the sample's picture.

The candidate cap binds on 96% of absorption's 14,449 searched predecessors
and 70% of reappearance's, far more than FDIC's 1,056 of 23,634. The
`name-phonetic` seed fills it. Counted on 200 predecessors drawn at random
from the population (seed 20261126), each seed's own clauses under the
sweep's window filter and own-id exclusion, read-only `_count`:

| Seed alone reaching 500 | `lei-absorption` | `lei-reappearance` |
| ----------------------- | ---------------- | ------------------ |
| `name-phonetic`         | 190 (95.0%)      | 144 (72.0%)        |
| `address`               | 8 (4.0%)         | 2 (1.0%)           |
| both seeds together     | 190 (95.0%)      | 145 (72.5%)        |

The address seed never reached the cap where the name seed did not. Tuning
the cap is therefore a question about the name seed (phonetic codes shared
across 3.5 million names), not about shared addresses, which matter for
precision instead (see "Shared headquarters addresses").
