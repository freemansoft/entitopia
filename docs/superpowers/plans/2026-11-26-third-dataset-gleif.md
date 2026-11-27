# Third dataset, part 2: GLEIF-Entities — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load the GLEIF register, sweep US inactive legal entities for reappearance and absorption, measure recall against GLEIF's registered successor links, and report undeclared look-alike successions by score band with hand-reviewed samples.

**Architecture:**

- `GLEIF-Entities` is a new project. A fetch script downloads the Level 1 (LEI records) and Level 2 (relationships) golden copies. A prepare script streams both zips into one flat `entities.csv` with each entity's parents already joined onto it.
- The two sweeps reuse part 1's framework as it stands: `LifecycleWindow`, `DeclaredLinks`, `split_half`, the `_meta.declared_accounting` and no-truth guards, and `scripts/measure_declared_links.py`.
- Part 1 changes only by addition: a `not` clause in the closed population menu, an optional `candidates.filter`, band and undated counts in the measurement output, and a band sampler for hand review. Each addition defaults to today's behaviour, and the FDIC sweeps and their measured figures must not move.
- The anonymization scanner learns LEIs before any GLEIF value can reach a commit.

**Tech Stack:** Python 3.15 (`.venv`), pinned elasticsearch-py 9.4.1 with Elasticsearch 9.4.1 (local Colima cluster at `http://localhost:9200`), pytest, ruff, prettier 3.3.3 for Markdown and JSON.

**Spec:** `docs/superpowers/specs/2026-11-26-third-dataset-design.md` (the GLEIF half). Part 1 is `docs/superpowers/plans/2026-11-26-third-dataset-fdic.md`. Read its Interfaces blocks for every name this plan consumes.

**Stacking:** this branch is cut from `worktree-third-dataset-fdic` at `9aed268` (PR 64, base `main`). Any PR from it targets `worktree-third-dataset-fdic`, never `main`, and its description says it is stacked on PR 64. The chain is kept or dropped as a whole.

## Decisions this plan makes

These were left open by the spec or by part 1's "After this plan" list.

- **Each entity's parent LEIs are copied onto its own record (denormalized). There is no separate `relationships` index and no enrich policy.** The spec's data table lists both.
  - **What the parents are for.** They serve one purpose: tagging a pair `explains` when the two entities are parent and child, or share a parent.
  - **Why they must sit on the entity record.** `DeclaredLinks.classify` (`matching/declared.py`) decides a pair's tag by comparing field values on the two documents it is handed, the predecessor and the candidate, and it never queries anything else. It answers questions such as "is the candidate's `LEI` in the predecessor's `DIRECT_PARENT_LEI`?" and "do the two share an `ULTIMATE_PARENT_LEI`?". `scripts/measure_declared_links.py` re-tags the same way.
    - FDIC needed no copying, because each bank record already carries `NEWCERT`, `ULTCERT` and `RSSDHCR`.
    - GLEIF publishes parents in a separate Level 2 file, one row per relationship, so they must be brought onto each Level 1 row for that code to see them.
  - **The alternative considered: keep the relationships normalized.** That means a `relationships` index the sweep queries when it tags a pair. It was rejected for three reasons:
    - It adds one or two lookups for every emitted pair, or a batched lookup for every predecessor, across the whole sweep.
    - `DeclaredLinks` would need a new kind of rule that reads another index rather than the two documents. Part 1's framework is meant to be reused unchanged.
    - The measurement script would need the same lookup, or its tags would disagree with the sweep's.
  - **Why the copy is made in Python, not with an Elasticsearch enrich policy.** The spec's route loads Level 2 into a `relationships` index and copies the parents across at ingest through an enrich policy, as DOT-Commercial does.
    - An enrich policy works from a frozen snapshot taken each time the policy is executed. Reloading `relationships` from a newer download without re-executing it would load entities with the old parents. The load would still report success: the quiet, wrong output this repo keeps guarding against.
    - Instead, `prepare_gleif.py` reads both files of one download, pivots Level 2 to one row per child, and writes each child's parent LEIs into `entities.csv`. Parents and entities therefore always come from the same publish.
    - The join is a dictionary lookup over about 494,000 relationship rows, and it is unit-tested (`test_relationships_pivot_keeps_every_status`, `test_parents_joined_onto_entity`), which an enrich processor cannot be.
  - **What this gives up.** The raw relationship rows cannot be browsed as their own index. Only each entity's parent LEIs are kept, as list fields. The prepare step records relationship counts by type and status in the README, so the totals stay checkable.
- **Shared and reverse `truth` rules stay refused.** A declared-link rule recognizes a related pair in one of three ways:

  - **Forward key:** the closed entity's record names its successor. FDIC's `NEWCERT` is one.
  - **Reverse key:** the candidate's record names the closed entity, such as a child naming its parent.
  - **Shared:** both records carry the same value, such as one ultimate parent.

  Recall needs a fixed list of right answers per predecessor. A forward rule gives that list by reading the predecessor's own field. The other two kinds put the answer on other records, so `truth_targets` would have to search the whole index for each predecessor. Part 1 therefore lets only forward key rules be `truth`. Without that, pairs could be tagged truth that recall never counts.

  GLEIF records the successor on the inactive record itself (`SuccessorEntity`), which is the forward case, so the restriction costs it nothing. Neither dataset needs more.

  Reverse and shared rules are still used here, as `explains` rules: parent, child, shared ultimate parent and shared successor. Those mark a pair as related, so it is not reported as a finding. They never count toward recall, and that role was always allowed.

  - `DeclaredLinks.from_config` and its error message stay as they are. The GLEIF README records the decision.

- **The spec's `registration_date: unknown` tag is the absence of the successor side's `add_date`.** `_to_action` already omits `add_date` when the candidate has no parseable registration date, the same reading `LifecycleWindow.admits` uses.
  - A second field could disagree with it.
  - Reading the absence also lets the measurement run unchanged on part 1's existing FDIC pair indexes.
- **The population menu gains a `not` clause kind.**
  - The spec's GLEIF population excludes the `FUND` and `BRANCH` categories, and Task 2 may add duplicate registrations to the exclusions.
  - The closed menu (`nested-exists`, `term`, `all`, `any`) cannot express an exclusion. Listing the allowed categories with `any` instead would silently drop every record whose category is blank.
  - `not` is code-backed like the others, so the menu stays closed.
- **Dates are truncated to the calendar day, not converted to UTC.** GLEIF writes dates with an offset (`…T00:00:00+01:00`). Converting to UTC moves such a date to the previous day, and a one-day shift is enough to move a same-day successor between the two sweeps.
- **Successor chains are followed to their end at prepare time, and a pair inside one chain is `explains`.** This is GLEIF's version of FDIC's `ULTCERT` and `fdic-merger-tree`.
  - **The case.** Entity A was absorbed into B, and B was later absorbed into C. GLEIF records two links: A names B as its successor, and B names C. Both A and B are inactive, so both are predecessors in the population.
  - **What already works.** A→B and B→C are each a truth pair, because each predecessor names its own direct successor. Recall is counted one step at a time, as for FDIC.
  - **What does not.** The matcher may also pair A with C, since C now carries A's business. No GLEIF record links A to C directly, so without a chain rule that pair is tagged `none` and reported as an undeclared succession, a finding. It is the registrar's own recorded chain, so calling it a finding is a false positive, and it would be anonymized and hand-reviewed for nothing.
  - **FDIC already solves this.** Every bank in a merger chain carries the chain's last bank as `ULTCERT`, and an active bank carries its own `CERT`. The shared-`ULTCERT` rule then explains every pair inside one tree: A–C, B–C, and two banks absorbed into the same tree by different routes. GLEIF publishes no such field, so the prepare step computes one.
  - **The computed field.** `SUCCESSOR_CHAIN_END` lists, for every entity that is part of a chain, the LEIs reached by following `SUCCESSOR_LEI` until an entity names no successor. The end entity carries its own LEI, as an active FDIC bank carries its own `CERT`, so A–C is explained as well as A–B. An entity in no chain, which is most of the register, gets no value, and a shared rule never fires on a blank.
  - **The rule.** One `shared` `explains` rule on `SUCCESSOR_CHAIN_END` (`gleif-successor-tree`) covers the whole tree, including two entities absorbed into one successor. It replaces the narrower shared-`SUCCESSOR_LEI` rule. Truth outranks explains in `DeclaredLinks.classify`, so A→B and B→C keep their `truth` tag and still count toward recall.
  - **Why at prepare time, not in the sweep.** The sweep tags a pair by comparing fields on the two documents, so the chain end must already sit on both records. Following chains needs the whole successor map at once, which the prepare step has and a per-pair tagger does not. The prepare step therefore reads the Level 1 file twice: once to collect every LEI's successors, once to write rows.
  - **Odd shapes in the data.**
    - A successor list naming two LEIs (a split) gives two chain ends. The field is a list, and either one shared explains the pair.
    - A successor LEI absent from the file still counts as a chain end, since its LEI is known.
    - A cycle (A names B and B names A, which only bad data produces) would loop forever. It is cut at the first revisit and counted as `successor_cycle`. Its members take the cycle's smallest LEI as their chain end, so the result is reproducible and the pairs stay explained rather than read as findings.
  - **What it does not change.** It adds no truth link, so the recall denominator is untouched. It only moves chain pairs out of the findings.

## Global Constraints

- Run everything through `.venv/bin/python`. Never use bare `python3` or `pip3`.
- `.venv/bin/python -m ruff check .` prints `All checks passed!` before every commit.
- `.venv/bin/python -m pytest` (the whole suite) passes before every commit. A diff touching only `*.md` may skip both, per `CLAUDE.md`.
- Run `npx prettier@3.3.3 --check` on every changed Markdown and JSON file.
- Run the `anonymization-check` skill before every commit. From Task 1 on, the scanner looks up LEIs.
- Elasticsearch client calls pass explicit keyword arguments, never `body=`.
- Configuration is a `SimpleNamespace` loaded via `file_utils.load_from_file`. Read optional keys with `getattr(obj, "key", default)`.
- Comments and docstrings explain why, never what (`CLAUDE.md`).
- Every new guard gets a test whose module docstring names the silent failure it prevents, plus an entry in the README hazard or re-run table.
- **DOT-Commercial's, CMS-Providers' and FDIC-Banks' sweeps and measured figures must not change.** Every new setting defaults to today's behaviour. FDIC's measurement output may gain keys, but no existing key may change its value.
- **Naming.** A GLEIF pair tagged `truth` (a registered successor) may be named. Every `explains` pair (parent, child, sibling, or two entities in one successor chain) and every `none` pair is anonymized, whatever its score. Tests use invented LEIs and names only.
- Data lands in `GLEIF-Entities/data/<step>/`, which `.gitignore` already excludes (`*/data/`). Downloaded zips are untrusted input: they are read with `zipfile` and `csv`, never extracted into the repo or executed.
- Before any re-run against the loaded cluster, read the `run-entitopia` skill's re-run table. Local cluster writes are pre-authorized. Run commands likely to exceed about 2 minutes (the download, the load, full sweeps) in the background.
- **Stacking.** Before starting each task, run `git log -1 worktree-third-dataset-fdic`. If it has moved past the commit last merged, merge it in (`git merge worktree-third-dataset-fdic`) and re-run the suite. Never push that branch, and never retarget a PR to `main`. Ask the user before any push or PR.
- **Stop gates.** Where a step says "stop and report back", end the task there, write the figures to the shared notes folder, and ask the user. Do not substitute a fallback the spec forbids, such as the LEI registration date for a missing creation date.

## Review Focus

- **A duplicate registration naming its surviving LEI as successor.** That is one legal entity registered twice, not a succession. Its identical name would read as easy recall. Task 2 counts such records. When there are any, the population excludes them through the `not` clause and the README says how many. Tested in Task 6 (`test_population_excludes_what_profiling_found`).
- **A creation date with a non-UTC offset** (`1996-01-01T00:00:00+01:00`). It must load as `1996-01-01`, not `1995-12-31`, or a successor created on the shutdown day lands in the absorption window. Tested in Task 2 (`test_offset_date_keeps_calendar_day`).
- **An inactive record whose successor LEI is its own LEI.** The candidate search excludes the predecessor's own `_id`, so this link could only ever count as `not_retrieved`. The prepare step drops and counts it. Tested in Task 2 (`test_self_successor_dropped_and_counted`).
- **A registered agent's address shared by thousands of US entities.** Many US legal addresses are a filing agent's. On a legal-address signal each such entity would match every other one at that address. The address signal reads the headquarters address. Any legal-address signal tuning adds must carry a `max_shared_records` limit. Tested in Task 6 (`test_legal_address_is_rarity_limited`).
- **A truth successor that the candidate filter removes** (a successor whose category is `FUND`). It must be excluded from the denominator as `target_filtered_out`, not counted as `not_retrieved`, or the filter reads as a retrieval failure. Tested in Task 7 (`test_filtered_target_is_excluded_not_missed`).

---

### Task 1: The anonymization scanner learns LEIs and GLEIF legal-name suffixes

First, so every later commit that quotes a GLEIF value is checked.

**Files:**

- Modify: `.claude/skills/anonymization-check/scripts/scan_identifiers.py` (`PATTERNS`, `_ORG_SUFFIX`, `_TITLE_SUFFIX`, `extract`, `source_lookup`)
- Modify: `.claude/skills/anonymization-check/SKILL.md` (LEIs are looked up, LEI-keyed projects only; `explains` pairs count as flagged)
- Test: `tests/test_scan_identifiers_leis.py`

**Interfaces:**

- `PATTERNS["lei"]` matches a bare 20-character run `[0-9A-Z]{18}[0-9]{2}`, not adjacent to another letter, digit or `<`. `extract` keeps a match only when `lei_checksum_ok(value)` holds.
- `lei_checksum_ok(value: str) -> bool`, a module function. It applies the LEI standard's ISO 7064 MOD 97-10 check: letters map to `A=10 … Z=35`, and the resulting integer mod 97 must equal 1. Uppercase hex hashes and other 20-character runs fail the check.
- `source_lookup` treats `kind == "lei"` like `kind == "cert"`: a `term` on `project.entity_key` only when `project.entity_key == "LEI"`, and no clause otherwise.
- `_ORG_SUFFIX` adds `LP|L\.P\.|LLLP|L\.L\.L\.P\.|HOLDINGS|PARTNERS|GROUP|PLC|N\.V\.|S\.A\.|GMBH|AG`. `_TITLE_SUFFIX` adds `Holdings|Partners|Group|L\.P\.|LP`. Task 3 adds any legal-form word its stop-list measurement finds above 1% that this list lacks.

- [ ] **Step 1: Write the failing tests** in `tests/test_scan_identifiers_leis.py`. Load the scanner by path as `tests/test_scan_identifiers_banks.py` does, and reuse its `_project` and `_CountingEs` shapes.

  Module docstring: GLEIF's key is a 20-character LEI that no existing pattern extracts. A scan of text quoting one would report "clean" over a value it never looked up, the quiet success the scanner exists to prevent. Every value in the file is invented.

  The test builds its LEIs with its own helper `_lei(prefix18: str) -> str`, which appends `98 - int(converted + "00") % 97` as two digits. It must not call the scanner's function, so a wrong implementation cannot agree with itself. Prefixes are `ZZZZ00INVENTED0001`, `ZZZZ00INVENTED0002` and so on. Tests:

  - `test_lei_is_extracted_bare`: `"successor " + _lei(...)` yields one `lei` candidate.
  - `test_lei_with_bad_check_digits_is_not_extracted`: the same LEI with its last digit changed yields none.
  - `test_lei_placeholder_is_not_extracted`: `<LEI-A>` yields none.
  - `test_hash_is_not_an_lei`: a 40-character lowercase hex string and its uppercase form yield none.
  - `test_lei_is_not_also_a_vin_or_number`: an LEI whose 18 leading characters are all digits yields only the `lei` candidate.
  - `test_lei_looked_up_only_where_the_entity_key_is_an_lei`: a fake `es` capturing `search`. A `CERT` project issues no search, and an `LEI` project issues one with `{"term": {"LEI": value}}`.
  - `test_gleif_names_are_extracted`: `QUUXWORTH VANTAGE HOLDINGS L.P.` and `Quuxworth Vantage Partners LP` are extracted as names.
  - `test_explains_pair_is_flagged`: `_CountingEs(truth=0, other=2)` gives `FLAGGED`. A parent or sibling pair is an `explains` pair, and the naming rule anonymizes it.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_scan_identifiers_leis.py -v`. Expected: the extraction tests fail, since no `lei` kind exists yet.

- [ ] **Step 3: Implement.** Add `lei` to the labelled set in `extract` so a digit-only LEI is not repeated as a `number`.

- [ ] **Step 4: Run the full suite and ruff, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check .claude/skills/anonymization-check/SKILL.md
git add .claude/skills/anonymization-check tests/test_scan_identifiers_leis.py
git commit -m "feat(anonymization-check): extract and look up LEIs and legal-form suffixes"
```

### Task 2: Fetch and prepare GLEIF, profile it, and pass the stop gates

**Files:**

- Create: `GLEIF-Entities/fetch_gleif.py`
- Create: `GLEIF-Entities/prepare_gleif.py`
- Create: `GLEIF-Entities/README.md` (Fetching, Profile, Hazards)
- Test: `tests/test_gleif_fetch.py`, `tests/test_gleif_prepare.py`

**Interfaces:**

- `fetch_gleif.py`:
  - `GOLDEN_COPY_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"`. This URL is from memory; Step 3 probes it first and replaces it if it is wrong.
  - `resolve_latest(opener=urllib.request.urlopen) -> dict[str, str]` returns `{"lei2": url, "rr": url}`, the full-file CSV zips from the latest publish. The URLs are resolved at run time, as `CMS-Providers/download_cms_provider.sh` resolves its own, because golden-copy file names carry their publish timestamp.
  - `download(url, dest: Path, min_bytes: int, opener=…) -> int` streams to `dest` plus `.part` and renames it only on success. It raises on a non-200 status, a body starting with `<`, a file that `zipfile.is_zipfile` rejects, or fewer than `min_bytes` bytes, and leaves neither file behind.
  - Floors: `lei2` 10 MB and `rr` 1 MB. These are two orders of magnitude below the real files, so they catch a stub without rejecting a real download, like the CMS plausibility floor.
  - It sets a generic `User-Agent` and uses no key.
  - Destinations: `GLEIF-Entities/data/raw/lei2.zip` and `GLEIF-Entities/data/raw/rr.zip`.
- `prepare_gleif.py`:

  - `iso_day(raw: str | None) -> str | None` returns the first 10 characters when they form a valid calendar date (`datetime.date.fromisoformat`), else `None`.
  - `pivot_relationships(rows: Iterable[dict], columns: RelationshipColumns) -> tuple[dict[str, dict[str, list[str]]], Counter]` maps a child LEI to `{output column: [parent LEIs]}` for each relationship type kept. Every relationship status is kept, because an inactive entity's relationships have usually lapsed and a lapsed parent still explains a pair. The counter records rows by type and status.
  - `prepare_entities(rows: Iterable[dict], parents, columns: EntityColumns) -> Iterator[dict]`, with a `PrepStats` counter for: `self_successor`, `successor_without_lei`, `successor_bad_format`, `separator_replaced`, `undated_creation` and `no_shutdown_date`.
  - `read_zip_csv(path: Path) -> Iterator[dict]` refuses a zip that does not hold exactly one `.csv` member, and refuses a header missing any configured column (FDIC hazard 3).
  - `EntityColumns` and `RelationshipColumns` are module-level dataclass constants mapping each output column to its source column. Step 3 fills the source names from the real header.
  - Output columns: `LEI, LEGAL_NAME, OTHER_NAMES, LEGAL_STREET, LEGAL_CITY, LEGAL_REGION, LEGAL_COUNTRY, LEGAL_POSTAL, HQ_STREET, HQ_CITY, HQ_REGION, HQ_COUNTRY, HQ_POSTAL, CATEGORY, LEGAL_FORM, ENTITY_STATUS, REGISTRATION_STATUS, CREATION_DATE, SHUTDOWN_DATE, SUCCESSOR_LEI, SUCCESSOR_CHAIN_END, DIRECT_PARENT_LEI, ULTIMATE_PARENT_LEI`. Add one parent column per further relationship type Step 3 keeps (`BRANCH_OF_LEI`, `FUND_MANAGER_LEI` and the like).
  - `successor_chain_ends(successors: dict[str, list[str]]) -> tuple[dict[str, list[str]], Counter]` maps every LEI that names a successor or is named as one to its sorted chain ends, as the decision "Successor chains are followed to their end" describes. The counter records `chain_members`, `chain_length` (a histogram of steps to the end), `split_successor` and `successor_cycle`. It runs on the cleaned successor map, after self-successors and malformed LEIs are dropped, so a dropped link cannot extend a chain. `prepare_entities` writes its result as `SUCCESSOR_CHAIN_END`.
  - List-valued columns (`OTHER_NAMES`, including transliterated names, `SUCCESSOR_LEI`, `SUCCESSOR_CHAIN_END` and the parent columns) are joined with `|`. A `|` inside a source value becomes a space, counted as `separator_replaced`.
  - `SUCCESSOR_LEI` keeps only values matching the LEI shape. A successor named with no LEI is counted as `successor_without_lei`, and one equal to the record's own LEI as `self_successor`; neither is written.
  - `SHUTDOWN_DATE` takes the entity's own expiration or end date when the header has one. Otherwise it takes the effective date of the latest completed legal-entity event whose type is terminal. The terminal types are listed from the Step 3 profile (expected: dissolution, liquidation, merger and acquisition). Both pass through `iso_day`.
  - Output path: `GLEIF-Entities/data/entities/entities.csv`. CLI: `--lei2`, `--rr`, `--out` (defaults to the paths above), `--print-header` (prints both raw headers and exits).

- [ ] **Step 1: Write the failing tests.**

  `tests/test_gleif_fetch.py`. Module docstring: the CMS incidents, a 404 page saved as data and a stub cached as a real file. Uses a fake opener:

  - `test_resolves_full_file_urls`: a fake publish JSON gives both URLs.
  - `test_html_body_raises`.
  - `test_failed_download_leaves_nothing`: the opener raises after the first chunk, and neither `dest` nor `dest.part` exists afterwards.
  - `test_non_zip_refused`.
  - `test_below_floor_refused`.

  `tests/test_gleif_prepare.py`. Module docstring: a creation date shifted by a day moves a succession between sweeps, and a self-successor counts as a miss the matcher could never avoid. Both would pass loading unnoticed. Invented LEIs only. Tests:

  - `test_offset_date_keeps_calendar_day`: `iso_day("1996-01-01T00:00:00+01:00") == "1996-01-01"`, `iso_day("2012-06-06T15:53:00Z") == "2012-06-06"`, and `None` for `""`, `"garbage"` and `"2021-02-29"`.
  - `test_self_successor_dropped_and_counted`.
  - `test_chain_end_reaches_the_last_entity`: A→B→C gives `["C"]` for A, B and C alike, so A–C, A–B and B–C share a value.
  - `test_chain_end_of_a_split`: A naming B and D, each with no successor, gives `["B", "D"]` for A.
  - `test_chain_end_survives_a_cycle`: A→B→A terminates, counts one `successor_cycle`, and gives both members the smaller LEI.
  - `test_unchained_entity_has_no_chain_end`.
  - `test_successor_absent_from_file_is_an_end`.
  - `test_successor_without_lei_counted`.
  - `test_relationships_pivot_keeps_every_status`: child A with direct parents B (active) and D (inactive) and ultimate parent C gives `{"DIRECT_PARENT_LEI": ["B", "D"], "ULTIMATE_PARENT_LEI": ["C"]}`, with counts by status.
  - `test_parents_joined_onto_entity`.
  - `test_separator_in_value_replaced_and_counted`.
  - `test_missing_column_refused`.
  - `test_zip_must_hold_one_csv`.
  - `test_shutdown_prefers_own_end_date_then_latest_terminal_event`. Write it in Step 4, once Step 3 has fixed the columns.

- [ ] **Step 2: Run them to make sure they fail, implement both scripts, then run them to make sure they pass** (except the shutdown test).
      Run `.venv/bin/python -m pytest tests/test_gleif_fetch.py tests/test_gleif_prepare.py -v`.

- [ ] **Step 3: Download and read the headers.** Check free disk first. The raw zips, the prepared CSV and the index together need about 20 GB, so stop and report back if the host or the Colima VM has less than 30 GB free.

```bash
.venv/bin/python GLEIF-Entities/fetch_gleif.py
.venv/bin/python GLEIF-Entities/prepare_gleif.py --print-header
```

Fill `EntityColumns` and `RelationshipColumns` from the printed headers. Expected Level 1 names come from memory and must be confirmed: `LEI`, `Entity.LegalName`, `Entity.OtherEntityNames.*`, `Entity.LegalAddress.*`, `Entity.HeadquartersAddress.*`, `Entity.EntityCategory`, `Entity.LegalForm.EntityLegalFormCode`, `Entity.EntityStatus`, `Entity.EntityCreationDate`, `Entity.SuccessorEntity.*`, `Entity.LegalEntityEvents.*` and `Registration.RegistrationStatus`. Level 2 needs a start node, an end node, a relationship type and a relationship status.

- [ ] **Step 4: Write the shutdown test, prepare, and profile.**

```bash
.venv/bin/python GLEIF-Entities/prepare_gleif.py
.venv/bin/python scripts/profile_dataset.py GLEIF-Entities/data/entities/entities.csv --key LEI
```

Record these in the README. For the gate figures, a one-off count over `entities.csv` is enough; it is not committed code.

- The row count (expect about 3.5 million) and that `LEI` is unique.
- Every `PrepStats` counter and the relationship counts by type and status.
- The chain figures from `successor_chain_ends`: members, the chain-length histogram, splits and cycles. Any cycle at all is listed by count in the README hazards, because only bad data produces one.
- The distributions of `CATEGORY`, `ENTITY_STATUS` and `REGISTRATION_STATUS`, overall and among US inactive records (`LEGAL_COUNTRY` `US`, `ENTITY_STATUS` `INACTIVE`; the API reported 19,413 on 2026-11-26).
- Among US inactive records: blank rates of `CREATION_DATE` and `SHUTDOWN_DATE`, and how many name a `SUCCESSOR_LEI`.
- Among those successors: how many are in the file, and how many of those have a `CREATION_DATE`.
- Which `REGISTRATION_STATUS` values the successor-naming US inactive records carry, and how many have an identical `LEGAL_NAME` on both sides. Duplicate registrations show up here.
- The 10 most shared US `LEGAL_STREET` plus `LEGAL_CITY` values, with counts. These are registered-agent addresses, named in the README as excluded shared values, which `CLAUDE.md` permits.

**Stop gates.** Stop and report back with the figures if any of these hold:

- More than half of the successor targets lack `CREATION_DATE`. The spec forbids substituting the LEI registration date.
- More than half of the US inactive records lack `SHUTDOWN_DATE`.
- Fewer than 200 US inactive records name a successor that is in the file. Each half would then hold under 100 links, too few to tune on.

**Decisions recorded in the README:**

- Whether duplicate registrations exist, and so whether the population excludes them.
- Whether `FUND` and `BRANCH` are a large share of the US inactive records. If together they are under 5%, drop the population exclusion and say so, as the spec directs.
- Which further relationship types are kept as parent columns.

- [ ] **Step 5: Run the full suite, ruff and prettier, run the anonymization check on the README, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check GLEIF-Entities
git add GLEIF-Entities tests/test_gleif_fetch.py tests/test_gleif_prepare.py
git commit -m "feat(GLEIF-Entities): fetch and prepare the golden copy, record the profile"
```

### Task 3: Analyzers, the measured stop list, mappings and the load

The stop list is measured before the first load. Reloading 3.5 million documents to change it costs about an hour, and part 1 had to reload.

**Files:**

- Create: `GLEIF-Entities/configuration.json` (steps `entities-ingestion-setup`, `entities`, then the two sweeps added in Task 6)
- Create: `GLEIF-Entities/configuration/entities-ingestion-setup/pipelines.json`
- Create: `GLEIF-Entities/configuration/entities/{index-config,index-mappings,index-settings}.json`
- Modify: `GLEIF-Entities/README.md` (stop list, load figures, hazards)
- Test: `tests/test_gleif_pipeline.py`

**Interfaces:**

- Pipeline `entities-pipeline-000001`:
  - Splits each `|`-joined column into an array, trimming the values.
  - Derives `HQ_LOCAL` and `LEGAL_LOCAL` as `<street> | <city> <region>`. This is FDIC's `STREET_LOCAL` shape and lesson (FDIC hazard 5). Copy its processor from `FDIC-Banks/configuration/institutions-ingestion-setup/pipelines.json` and change only the field names. A record missing its city or region gets no `*_LOCAL` field.
  - Parses `CREATION_DATE` and `SHUTDOWN_DATE` as `yyyy-MM-dd`, with an `on_failure` that removes the field, so a bad date drops a value, not an entity.
- Mappings:
  - `keyword`: `LEI`, `SUCCESSOR_LEI`, `SUCCESSOR_CHAIN_END`, every parent column, `CATEGORY`, `LEGAL_FORM`, `ENTITY_STATUS`, `REGISTRATION_STATUS`, `LEGAL_COUNTRY`, `HQ_COUNTRY`, `LEGAL_REGION`, `HQ_REGION` and both postal codes.
  - `date`, ISO format only: `CREATION_DATE` and `SHUTDOWN_DATE`.
  - `text` with `phonetic`, `phonetic_bm` and `clean` subfields: `LEGAL_NAME` and `OTHER_NAMES`.
  - `text` with `clean` and `tokens` subfields: `HQ_LOCAL` and `LEGAL_LOCAL`.
  - Copy the analyzer definitions from `FDIC-Banks/configuration/institutions/index-settings.json`.
- `index-config.json`: `id_field: "LEI"`, `num_rows: null`, `source: "entities.csv"`.

- [ ] **Step 1: Write the failing test** `tests/test_gleif_pipeline.py`. It reads the committed files, as `tests/test_fdic_street_local.py` does. Module docstring: a street without its town matches every same-named street, and a date that fails the strict mapping drops the whole entity. Both pass loading unnoticed. Tests:

  - `test_local_address_needs_city_and_region`: the `*_LOCAL` processors are conditioned on both fields being present.
  - `test_date_failure_removes_field`: each date processor has an `on_failure` removing its own field.
  - `test_list_columns_are_split`: every list-valued output column of `prepare_gleif.EntityColumns` has a `split` processor. Load the script by path.

- [ ] **Step 2: Run it to make sure it fails, write the configs, then run it to make sure it passes.**
      Run `.venv/bin/python -m pytest tests/test_gleif_pipeline.py -v`.

- [ ] **Step 3: Measure the stop list.** Count the share of `LEGAL_NAME` values holding each word, using the same tokenization as part 1's measurement (`FDIC-Banks/README.md`, "Name stop list"). Stop every word in more than about 1% of names: expect legal-form words (`LLC`, `INC`, `LP`, `FUND`, `TRUST`, `HOLDINGS`, `CAPITAL`, `PARTNERS`) and their dotted spellings. Measure two populations, US records and all records, because candidates come from the whole index. Stop the union.
      Then run `scripts/compare_phonetic_encoders.py` on `LEGAL_NAME` with the stop list applied, and keep the encoders it favours, as the spec's name-matching process requires. Leave `fuzzy_edits` off unless a pair-id comparison in Task 8 justifies it.
      Record the percentages in the README. Add any legal-form word above 1% that Task 1's suffix list lacks to the scanner, with a test line, in this commit.

- [ ] **Step 4: Check coverage and load.** Run the load in the background.

```bash
.venv/bin/python scripts/check_mapping_coverage.py --project=GLEIF-Entities --step=entities
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=entities-ingestion-setup
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=entities
curl -s "localhost:9200/entities-000001/_count"
curl -s "localhost:9200/entities-000001/_mapping/field/CREATION_DATE"
```

Expected:

- The coverage check reports no unpinned columns.
- The loader logs `0 of N documents failed`.
- `_count` equals the CSV row count.
- `CREATION_DATE` is mapped as `date`.
- Inactive US records with `SHUTDOWN_DATE` missing equal Task 2's figure. These are the predecessors `undated_predecessors: "skip"` will skip.

  Record the load time and the index size in the README.

- [ ] **Step 5: Run the full suite, ruff, prettier and the anonymization check, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check GLEIF-Entities
git add GLEIF-Entities tests/test_gleif_pipeline.py .claude/skills/anonymization-check tests/test_scan_identifiers_leis.py
git commit -m "feat(GLEIF-Entities): measured stop list, mappings, and the entities load"
```

### Task 4: A `not` clause in the population menu

Skip this task, and say so in the README, only if Task 2 kept no exclusion: no duplicate registrations, and funds and branches under 5%.

**Files:**

- Modify: `matching/population.py` (`CLAUSE_KINDS`, `_resolve`, the module docstring's menu sentence)
- Modify: `schema/entity-match.schema.json` (selector clause `not`: a string, the name of another selector)
- Modify: `utils/config_liveness.py` (`_check_term_clauses` already walks every selector; confirm it reaches a `term` named through `not`)
- Test: `tests/test_population.py` (add cases)

**Interfaces:**

- `{"not": "<selector name>"}` resolves to `{"bool": {"must_not": [<resolved selector>]}}`. Cycle detection follows `not` references as it follows `all` and `any`.
- `CLAUSE_KINDS = ("nested-exists", "term", "all", "any", "not")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_population.py`. Extend the module docstring by one paragraph: an exclusion written as `any` of the allowed values silently drops every record whose value is blank. Tests:

  - `test_not_clause_negates_named_selector`: `all: [inactive, not-fund]` builds the expected `must` plus `must_not` dict.
  - `test_not_cycle_reported`: `a: {"not": "b"}` and `b: {"all": ["a"]}` raise `ValueError` naming the path.
  - `test_not_unknown_selector_raises`.
  - `test_shipped_configs_build_unchanged`: every shipped `entity-match.json` builds the same query as before. Pin the DOT and FDIC queries as literals copied from today's output.

- [ ] **Step 2: Run them to make sure they fail, implement, then run the full suite and ruff.**
      Run `.venv/bin/python -m pytest tests/test_population.py -v`, then `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`.

- [ ] **Step 3: Commit.**

```bash
git add matching/population.py schema/entity-match.schema.json utils/config_liveness.py tests/test_population.py
git commit -m "feat(population): a not clause, so a selector can exclude without dropping blanks"
```

### Task 5: Band counts, undated counts and a band sampler in the measurement

**Files:**

- Modify: `utils/declared_measure.py` (`LinkOutcome`, `PairRow`, `summarize`, `gap_histogram`, new `sample_bands`)
- Modify: `scripts/measure_declared_links.py` (`read_pairs`, `predecessor_links`, `main`: `--gaps fine`)
- Create: `scripts/sample_declared_bands.py`
- Test: `tests/test_measure_declared_links.py` (add cases), `tests/test_sample_declared_bands.py`

**Interfaces:**

- `LinkOutcome` gains `target_undated: bool = False`. It is set whenever the target was fetched and has no parseable registration date, whatever the status.
- `PairRow` gains `candidate_undated: bool = False`. `read_pairs(es, index, side, cand_side, dated)` sets it when `dated` (the step's lifecycle has a `registration_date`) and `<cand_side>.add_date` is absent. `cand_side` is the second name from `pair_side_names_for`.
- `summarize` adds two keys and changes no existing one:
  - `"undated": {"target_links_measured": int, "target_links_excluded": int, "pairs_by_band": {band: int}}`. The pair counts are cumulative at or above each band, like `recall_by_band`.
  - `"pairs_in_band": {label: {"truth": int, "explains": int, "none": int, "none_undated": int}}`. These are half-open intervals from `bands`, labelled `"0.35-0.5"`, `"0.5-0.6"` … `"0.9+"`, with pairs below the lowest band left out.
  - These are the spec's per-band findings figures: the share of truth is `truth / (truth + explains + none)` per interval, computed by the reader. Intervals, not cumulative counts, because the hand-review sample is drawn per interval.
- `gap_histogram(links, edges=_GAP_EDGES)`. `FINE_GAP_EDGES` reproduces the bins of the table in `FDIC-Banks/README.md`, "Choosing `overlap_days`", so the valley can be read from the script, not from a one-off. `--gaps` keeps its coarse output, and `--gaps fine` selects the fine bins.
- `sample_bands(pairs: list[dict], bands, per_band: int, seed: int, declared: str = "none") -> list[dict]` draws up to `per_band` pairs per interval with `random.Random(seed)`, plus a separate `"undated"` stratum of undated-candidate pairs.
  - Each row carries the pair `_id`, its band, `declared`, `total_score`, both sides' summary fields, `gap_days`, and `"verdict": null`.
  - Verdicts are `"real"`, `"explained-missed"` (a relationship the `explains` rules missed) or `"look-alike"`, the three columns part 1's hand check used.
- `scripts/sample_declared_bands.py --project P --step S --index I [--per-band 15] [--seed 42] [--declared none] [--out P/data/review/<step>-sample.jsonl] [--count FILE]`.

  - `--count` prints verdict counts per band from a reviewed file.
  - It refuses an `--out` outside a `data/` directory, because the rows carry real names.

- [ ] **Step 1: Write the failing tests.**

  In `tests/test_measure_declared_links.py`:

  - `test_existing_keys_unchanged`: one fixed `links`/`pairs` input. Every key part 1 defined has the value it had before this change; pin the literals.
  - `test_undated_targets_counted_both_ways`: two measured undated targets and one excluded as `target_undated` give `target_links_measured == 2` and `target_links_excluded == 1`.
  - `test_pairs_in_band_are_intervals`: pairs at 0.55 and 0.95 land in `"0.5-0.6"` and `"0.9+"` only, and one at 0.2 lands nowhere.
  - `test_undated_pairs_by_band`.
  - `test_fine_gap_bins_match_fdic_table`: gaps of `-3651, -1096, -366, -181, -91, -61, -31, -8, -1, 0, 1, 7, 366` fall one per bin, in the README table's order.

  `tests/test_sample_declared_bands.py`. Module docstring: a sample drawn from the largest band hides the bands that decide the threshold, and a sample written outside `data/` commits real names. Tests:

  - `test_per_band_cap_and_seed_stable`.
  - `test_undated_stratum_separate`.
  - `test_only_requested_declared_tag`.
  - `test_out_outside_data_refused`.
  - `test_count_verdicts_by_band`.

- [ ] **Step 2: Run them to make sure they fail.**
      Run `.venv/bin/python -m pytest tests/test_measure_declared_links.py tests/test_sample_declared_bands.py -v`.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Check that FDIC's figures did not move.** Re-run part 1's report-half measurement for both FDIC sweeps against the pair indexes `FDIC-Banks/README.md` cites. Every value under its "Report half" must be unchanged; only the new keys may be added. If those indexes are gone, say so in the commit message and rely on `test_existing_keys_unchanged`.

- [ ] **Step 5: Run the full suite and ruff, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add utils/declared_measure.py scripts/measure_declared_links.py scripts/sample_declared_bands.py tests/test_measure_declared_links.py tests/test_sample_declared_bands.py
git commit -m "feat(scripts): per-band and undated counts, fine gap bins, and a band sampler"
```

### Task 6: The two GLEIF sweeps, the declared rules, and a timed sample run

**Files:**

- Create: `GLEIF-Entities/configuration/lei-reappearance/{entity-match,index-config,index-mappings,metrics}.json`
- Create: `GLEIF-Entities/configuration/lei-absorption/{entity-match,index-config,index-mappings,metrics}.json`
- Modify: `GLEIF-Entities/configuration.json` (two sweep steps: `index-create`, `index-map`, `validate`, `entity-match`)
- Modify: `tests/test_succession_window_pairs.py` (add `("GLEIF-Entities", "lei-reappearance", "lei-absorption")` to `PAIRS`)
- Test: `tests/test_gleif_configs.py`

**Interfaces:**

- Both sweeps share:
  - `source_index: "entities-000001"` and `entity.key: "LEI"`.
  - `entity.summary_fields`: `LEGAL_NAME`, `HQ_CITY`, `HQ_REGION`, `LEGAL_COUNTRY`, `CATEGORY` and `ULTIMATE_PARENT_LEI`. `CATEGORY` is there so Step 4 can count funds among emitted pairs.
  - `lifecycle`: `{shutdown_date: "SHUTDOWN_DATE", registration_date: "CREATION_DATE", end_date: "SHUTDOWN_DATE"}`.
  - Population: selector `us-inactive`, defined as `all` of `inactive` (`term ENTITY_STATUS INACTIVE`) and `us` (`term LEGAL_COUNTRY US`). It also includes, per Task 2's decisions, `not` selectors for `FUND`, `BRANCH` and the duplicate registration status. `sort_field: "LEI"`.
  - `scoring.undated_predecessors: "skip"`.
  - `ignore_values`: whatever placeholders Task 2 found on the LEI columns. If it found none, `{}`.
  - `declared_links` with `complete_truth: false`:

```json
"rules": [
  {"name": "gleif-successor", "role": "truth", "kind": "key", "field": "SUCCESSOR_LEI"},
  {"name": "gleif-direct-parent", "role": "explains", "kind": "key", "field": "DIRECT_PARENT_LEI"},
  {"name": "gleif-ultimate-parent", "role": "explains", "kind": "key", "field": "ULTIMATE_PARENT_LEI"},
  {"name": "gleif-direct-child", "role": "explains", "kind": "key", "field": "DIRECT_PARENT_LEI", "reverse": true},
  {"name": "gleif-ultimate-child", "role": "explains", "kind": "key", "field": "ULTIMATE_PARENT_LEI", "reverse": true},
  {"name": "gleif-shared-direct-parent", "role": "explains", "kind": "shared", "field": "DIRECT_PARENT_LEI"},
  {"name": "gleif-shared-ultimate-parent", "role": "explains", "kind": "shared", "field": "ULTIMATE_PARENT_LEI"},
  {"name": "gleif-successor-tree", "role": "explains", "kind": "shared", "field": "SUCCESSOR_CHAIN_END"}
]
```

Add a forward and a reverse `explains` key rule for each further parent column Task 2 kept. The successor-tree rule is GLEIF's version of FDIC's `fdic-merger-tree`. Two entities in one recorded chain or tree are explained, not found: A paired with C when A→B→C, and two entities absorbed into one successor (see "Successor chains are followed to their end").

- Signals, with starting weights copied from part 1's tuned `bank-absorption` config:
  - `name-phonetic` on `LEGAL_NAME` and `OTHER_NAMES` with `cross_field`, both `phonetic` and `phonetic_bm`.
  - `name-token` on `clean`.
  - `address` on `HQ_LOCAL`, with `exact_subfield: "clean"`, `fuzzy_subfield: "tokens"` and `state_field: "HQ_REGION"`.
  - There is no phone, email, website or VIN to use.
- `candidates.seed_signals`: `name-phonetic` and `address`. `max_candidates: 500`.
- `lei-reappearance`: `scoring.min_gap_days: -180`, `max_gap_days: 365`, `undated_candidates: "exclude"`, plus a `temporal` signal with `max_gap_days: 365` and `before_shutdown_scale: 1.0`.
- `lei-absorption`: `scoring.max_gap_days: -181`, no `min_gap_days`, `undated_candidates: "keep"`, no `temporal` signal.
- Pair index mappings pin `declared` and `declared_by` as `keyword`. Copy `FDIC-Banks/configuration/bank-absorption/index-mappings.json`.
- `metrics.json`: copy FDIC's.

- [ ] **Step 1: Write the failing tests.** Add the `PAIRS` entry to `tests/test_succession_window_pairs.py`, and write `tests/test_gleif_configs.py`.

  Module docstring: a duplicate registration read as a succession inflates recall with pairs that are one entity, and a registered agent's address on a legal-address signal links every client of that agent. Neither shows in a run's log. Tests:

  - `test_population_excludes_what_profiling_found`: `PopulationSelector(None, "entities-000001", config.population).build_query()` for both sweeps holds `term ENTITY_STATUS INACTIVE` and `term LEGAL_COUNTRY US`, and a `must_not` for each exclusion the README records. The expected exclusions are a constant `EXPECTED_EXCLUSIONS` in the test file, written from the decision Task 2 recorded in the README, not read from the config under test.
  - `test_only_truth_rule_is_the_successor`: exactly one `truth` rule, the forward `SUCCESSOR_LEI` key rule, and `complete_truth` is false.
  - `test_legal_address_is_rarity_limited`: every signal whose `fields` include a `LEGAL_*` address field has a `max_shared_records` entry for that field. With today's config (no legal-address signal) the test passes vacuously, and it states that in its assertion message.

- [ ] **Step 2: Run them to make sure they fail, write the configs, then run them to make sure they pass, along with part 1's `test_denominators_partition`.**
      Run `.venv/bin/python -m pytest tests/test_succession_window_pairs.py tests/test_gleif_configs.py tests/test_measure_declared_links.py -v`.

- [ ] **Step 3: Validate, then run a timed sample.** Set `population.max_records: 1000` in a local, uncommitted edit to both sweep configs.

```bash
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=lei-absorption
.venv/bin/python execute_project.py --project=GLEIF-Entities --step=lei-reappearance
```

Expected:

- `validate` passes, including the declared-link paths against the live mapping.
- Each completion line shows a non-zero `truth_links` and `truth_retrieved`.
- Neither run raises.

  Record the wall time per 1,000 predecessors and extrapolate to the full population. If either sweep would exceed 6 hours, stop and report back with the figure and the truncated-candidate count, before tuning `max_candidates` or the seeds.

- [ ] **Step 4: Decide the candidate filter.** On the sample's absorption pair index, count emitted pairs by `successor.CATEGORY`, and count truncated candidate sets.

  - **If `FUND` plus `BRANCH` successors are 10% or more of emitted pairs:** do Task 7 before the full sweeps.
  - **Otherwise:** skip Task 7. Record the share and the decision in the README.

- [ ] **Step 5: Revert the `max_records` edit, run both full sweeps in the background, then commit.** Record each completion line in the README: pairs, `truth_links`, `truth_retrieved`, `truth_emitted`, undated predecessors skipped, truncated sets and errors.

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check GLEIF-Entities
git add GLEIF-Entities tests/test_succession_window_pairs.py tests/test_gleif_configs.py
git commit -m "feat(GLEIF-Entities): reappearance and absorption sweeps with declared parent and successor rules"
```

### Task 7: The candidate filter (spec framework change 2), if Task 6 calls for it

**Why this task exists, and why it is conditional:**

- **The population and the candidates are filtered separately.** The population is US inactive entities, with funds and branches excluded. Candidates are drawn from the whole register of about 3.5 million LEIs, and nothing stops a fund or a branch from being one.
- **Funds crowd out real successors.** Investment funds carry their own LEIs, often in families with near-identical numbered names (`<MANAGER> Growth Fund I`, `… Fund II`). A predecessor whose name shares a word such as "Capital" or "Partners" with such a family can have dozens of its `max_candidates` slots taken by funds. The real successor is then never retrieved, which is a recall loss.
- **Funds add noise to the findings.** Those that are retrieved and scored add undeclared pairs that are almost never real successions, and they crowd the bands that are reviewed by hand.
- **Branches behave the same way.** A foreign company's US branch has its own LEI and often the parent's name.
- **The problem is inferred, not measured.** The spec says so and says to drop this change if profiling shows funds are no problem. The filter costs framework code, a schema key and a new measurement exclusion, so Task 6 measures first.
- **The 10% threshold is a judgement, not a calibration.** Below it, funds are a nuisance the hand-review bands absorb. Above it, they plausibly cost recall through the candidate cap, as part 1's address flood did (FDIC hazard 5). If the sample shows a different shape, such as capped candidate sets made up mostly of funds while emitted pairs stay low, record it and ask before deciding either way.

**Files:**

- Modify: `matching/population.py` (extract the clause resolver as `ClauseResolver(selectors).resolve(name) -> dict`. `PopulationSelector._resolve` delegates to it, so the two cannot drift)
- Modify: `matching/candidates.py` (`__init__`: read `candidates.filter`; `find`: append the resolved clause to the filter list)
- Modify: `phase_providers/phase_entity_match.py` (`build_sweep_components` passes `population.selectors` to the finder)
- Modify: `scripts/measure_declared_links.py` (`classify_target`: a target the filter removes is `target_filtered_out`)
- Modify: `utils/declared_measure.py` (`EXCLUSIONS` gains `"target_filtered_out"`)
- Modify: `schema/entity-match.schema.json` (`candidates.filter`: a string naming a selector in `population.selectors`)
- Modify: `GLEIF-Entities/configuration/lei-*/entity-match.json` (`candidates.filter: "not-fund-or-branch"`)
- Test: `tests/test_candidate_filter.py`

**Interfaces:**

- `candidates.filter` names a selector defined in `population.selectors`, using the same closed menu, `not` included. It is one table, so a selector cannot mean one thing for the population and another for candidates.
- `CandidateFinder.__init__` gains `selectors: dict | None = None`, the `vars()` of `population.selectors`. Without it, `filter` cannot resolve.
- An unknown name raises at `CandidateFinder.__init__`, not at the first search.
- `predecessor_links` runs the filter check right after a target is fetched, so `classify_target` keeps its signature.
- Retrieval-only: every scored pair comes from retrieval, so the scorer needs no gate for it.
- The measurement asks the cluster whether a target passes the filter: `es.count(index=source_index, query={"bool": {"filter": [{"term": {key: target}}, clause]}})`. It does not re-evaluate the clause in Python, so the check cannot disagree with the search. A target failing it is `target_filtered_out`, checked after `target_missing_from_index` and before the window.

- [ ] **Step 1: Write the failing tests** in `tests/test_candidate_filter.py`. Module docstring: a filter clause that is ignored lets funds take every capped candidate slot, and a filtered-out truth target counted as `not_retrieved` reads as a retrieval failure. Tests:

  - `test_filter_reaches_the_query`: a fake `es` capturing `search`. The resolved clause is in `query.bool.filter` alongside the window clauses.
  - `test_no_filter_query_unchanged`: the FDIC configs build the same query as before.
  - `test_unknown_filter_raises_at_init`.
  - `test_filtered_target_is_excluded_not_missed`: a fake count of 0 for the target gives `target_filtered_out`, and the target is not in the denominator.
  - `test_resolver_shared`: `PopulationSelector` and `CandidateFinder` resolve one selector to identical dicts.

- [ ] **Step 2: Run them to make sure they fail, implement, then run the full suite and ruff.**

- [ ] **Step 3: Re-run both GLEIF sweeps in the background** and record the new completion lines, the fund share of emitted pairs, and the truncated count next to Task 6's.

- [ ] **Step 4: Commit.**

```bash
git add matching phase_providers/phase_entity_match.py scripts/measure_declared_links.py utils/declared_measure.py schema/entity-match.schema.json GLEIF-Entities tests/test_candidate_filter.py
git commit -m "feat(candidates): an optional filter from the population's clause menu"
```

### Task 8: Tune on the tune half

**Files:**

- Modify: `GLEIF-Entities/configuration/lei-*/entity-match.json` (tuned values)
- Modify: `GLEIF-Entities/README.md` (overlap choice, tuning log, tuned configuration)

- [ ] **Step 1: Choose `overlap_days`.**
      Run `.venv/bin/python scripts/measure_declared_links.py --project GLEIF-Entities --step lei-absorption --index <pair index> --half tune --gaps fine`.

  - The research sample suggests about 10% of links are reappearance-shaped, so the tune half may hold only a few dozen.
  - If the fine bins show a valley between a spike near the shutdown and the absorption mass, set `overlap_days` there in both configs. Task 6's guard keeps the two configs paired.
  - Otherwise keep 180, and say in the README that GLEIF has too few reappearance-shaped links to show a valley, with the counts. Present the table as part 1's README does.

- [ ] **Step 2: Tune.** Use the tune half only. Try one change at a time, re-run the sweep and the script, and compare pairs by id with `scripts/compare_pair_ids.py`.

  - Keep a change only if it raises tune-half recall at 0.70 without lowering the truth share in the 0.7 and higher intervals of `pairs_in_band`. There is no precision for GLEIF, so this share stands in for it.
  - Candidates to try:
    - A `LEGAL_LOCAL` address signal with `max_shared_records`, against the registered-agent hazard.
    - `name-token` weight.
    - `min_total_score`, `max_candidates` and `max_pairs_per_predecessor`.
    - Seeding on unstopped name words (part 1's R6), only if the sample run's time allows it.
  - Log every attempt in a table shaped like part 1's tuning log: change, pairs, compare-ids delta, recall at 0.70, truth share at 0.70 and above, and kept or not.
  - Stop when two consecutive changes move neither figure.

- [ ] **Step 3: Run prettier and the anonymization check, then commit.**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
npx prettier@3.3.3 --check GLEIF-Entities
git add GLEIF-Entities
git commit -m "feat(GLEIF-Entities): tune both sweeps on the tune half"
```

### Task 9: Measure the report half and report findings by band

**Files:**

- Modify: `GLEIF-Entities/README.md` ("Measured against GLEIF's registered successors", "Undeclared successions by band", "Follow-ups")

- [ ] **Step 1: Measure the report half once per sweep.** Run the script with `--half report` for both sweeps. Paste both JSON outputs into the README, dated, with the command lines and the concrete pair index each cites. State each denominator and every exclusion in prose next to the recall figure. With fewer than 30 links in a denominator, quote counts rather than percentages. There is no precision figure: `complete_truth` is false, and the README says why.

- [ ] **Step 2: Draw and read the samples.**

```bash
.venv/bin/python scripts/sample_declared_bands.py --project GLEIF-Entities --step lei-absorption --index <pair index> --per-band 15
.venv/bin/python scripts/sample_declared_bands.py --project GLEIF-Entities --step lei-reappearance --index <pair index> --per-band 15
```

Read each row locally and fill `verdict`:

- `real`: the same business carried on by the candidate. Signs are the same or a clearly renamed name, the same headquarters town, and dates consistent with a takeover.
- `explained-missed`: a parent, sibling or affiliate the `explains` rules did not catch.
- `look-alike`: anything else.

Then run `--count` on each file. The review files stay under `GLEIF-Entities/data/review/` and are never committed.

- [ ] **Step 3: Write the findings.** For each sweep, a table with one row per `pairs_in_band` interval, plus the undated band: undeclared pairs, truth share, sample size, and the counts of `real`, `explained-missed` and `look-alike`.

  - Name the lowest interval worth a reviewer's time, argued from these figures. This is the spec's equivalent of the carrier project's strong band.
  - State the anonymized count of undeclared pairs at or above it.
  - No entity from an `explains` or `none` pair appears anywhere: no name, LEI, address or city-plus-name combination. Describe patterns with placeholders, for example `<ENTITY-A> -> <ENTITY-A> HOLDINGS (same HQ street), +14 days`.

- [ ] **Step 4: Run prettier and the anonymization check on the README diff, then commit.**

```bash
npx prettier@3.3.3 --check GLEIF-Entities/README.md
git add GLEIF-Entities/README.md
git commit -m "docs(GLEIF-Entities): report-half recall and undeclared successions by band"
```

### Task 10: Documentation and the guard tables

**Files:**

- Modify: `README.md`:
  - Reference implementations table: a `GLEIF-Entities` row.
  - Glossary: "Undated band" and "Candidate filter". Extend "Declared link" with the parent and child rules.
  - Hazards and open items: name each new guard test.
- Modify: `.claude/skills/run-entitopia/SKILL.md`:
  - GLEIF-Entities steps: fetch, prepare, setup, load, sweeps.
  - Re-run table rows. Reloading `entities` takes about an hour and moves the alias off a populated index. Same-day generations apply to both sweeps. `_meta.declared_accounting` is per index. Review files under `data/review/` are overwritten by a re-sample with the same `--out`.
- Modify: `schema/README.md` (the `not` clause and `candidates.filter`)
- Modify: `docs/adding-a-dataset.md`: one paragraph on telling a truth link from an `explains` link when the data records parents as well as successors, and on why truth rules must be forward key rules.

- [ ] **Step 1: Write the documentation changes.** Every guard the README names comes with its test: `test_scan_identifiers_leis.py`, `test_gleif_fetch.py`, `test_gleif_prepare.py`, `test_gleif_pipeline.py`, `test_population.py` (the `not` cases), `test_gleif_configs.py`, `test_succession_window_pairs.py`, `test_sample_declared_bands.py`, and `test_candidate_filter.py` if Task 7 ran.

- [ ] **Step 2: Run prettier on every changed Markdown file and the anonymization check, then commit.** The diff is Markdown only, so pytest and ruff may be skipped.

```bash
npx prettier@3.3.3 --check README.md docs/adding-a-dataset.md schema/README.md .claude/skills/run-entitopia/SKILL.md
git add README.md docs/adding-a-dataset.md schema/README.md .claude/skills/run-entitopia/SKILL.md
git commit -m "docs: GLEIF-Entities, the not clause, the candidate filter, and band findings"
```

## After this plan

Ask the user before pushing the branch or opening its PR. The PR targets `worktree-third-dataset-fdic`. Its description:

- says it is stacked on PR 64;
- lists every guard the change touched (`test_population.py`, `test_measure_declared_links.py`, `test_succession_window_pairs.py`) and why each still holds;
- carries the anonymization-check result.

Follow-ups for the README, not this plan:

- A worldwide population, which needs multilingual analyzers.
- Joining GLEIF to FDIC through `FED_RSSD` or a name match, a non-goal of the spec.
