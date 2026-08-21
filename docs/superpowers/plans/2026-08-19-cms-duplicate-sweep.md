# CMS Duplicate Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a second project produce scored pairs by configuration alone, turning "a new dataset is onboarded by writing JSON" from a property of the code into an observed fact.

**Architecture:** Give CMS-Providers' `hospitals` step an `entity-match` configuration in `all-entities` mode — no lifecycle, no temporal signal, no gap guards — and a `metrics.json` that records population shape rather than implying fraud. The framework is not modified; if something cannot be expressed in configuration, that is the finding, not a licence to edit `matching/`.

**Tech Stack:** Python 3.11+, Elasticsearch 9.4.1, pytest, ruff.

This is Plan 5 of five, covering rollout steps 10–11 of [the spec](../specs/2026-08-16-config-driven-analysis-portability-design.md). It depends on Plans 1–4, all merged.

## Global Constraints

- **Everything runs from `.venv`.** Tests: `.venv/bin/python -m pytest`. Lint: `.venv/bin/python -m ruff check .`
- **`ruff check .` must print `All checks passed!`** before any commit.
- **Do not modify `matching/`, `phase_providers/`, or `utils/`.** The whole point is that a second project needs no framework change. If one proves necessary, **stop and report it** — that is the most valuable result this plan can produce, and working around it silently would destroy the finding.
- **Never name a flagged hospital.** Pairs this sweep emits are overwhelmingly legitimate multi-record facilities, not fraud — but the repository rule is that any entity the matcher flagged is anonymized in committed material. Record counts, distributions and shapes; never a facility name, address or phone from a matched pair.
- **Do not re-run the DOT sweep.** Its baseline is certified; nothing here should touch it.
- **Branch:** `cms-duplicate-sweep`, cut from `main`.

---

## What the data actually contains

Measured 2026-08-19 against the freshly downloaded extract, replacing the five-row stub this plan was blocked on:

|                  |                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------- |
| Rows             | 5,419                                                                                                   |
| Columns          | **38**                                                                                                  |
| `Facility ID`    | **Unique across all 5,419 rows** — safe as `id_field`                                                   |
| Date columns     | **None.** Confirmed by the profiler; no ISO, legacy or US-format column exists                          |
| High-cardinality | `Facility ID`, `Facility Name`, `Address`, `City/Town`, `ZIP Code`, `County/Parish`, `Telephone Number` |

**Two findings change what this plan has to do.**

### The shipped mapping covers 7 of 38 columns

`CMS-Providers/configuration/hospitals/index-mappings.json` pins seven fields. The current extract has thirty-eight, so **31 columns would be dynamically mapped** — the README's first documented hazard, where every unpinned field lands as `text` and term queries against it silently match nothing.

The extract grew since that mapping was written. This plan pins the columns the sweep reads and **records the rest as a known gap** rather than mapping all 38: pinning a column nothing queries is busywork, and pretending the gap does not exist is worse than naming it.

### There are real duplicates, and few enough to check by hand

| Shape                                   | Groups | Records involved |
| --------------------------------------- | ------ | ---------------- |
| Identical `Facility Name`               | 90     | 221              |
| Identical `Telephone Number`            | 37     | 80               |
| Identical `Address`                     | 27     | 64               |
| Identical `Facility Name` + `City/Town` | 12     | 24               |

This is the acceptance criterion, and it is falsifiable in both directions:

- **A sweep finding zero pairs is broken**, not a clean bill of health. Ninety groups share a name exactly; any working configuration must retrieve them.
- **A sweep finding only the exact matches is also broken.** The entire point of phonetic and fuzzy analysis is to find near-misses that exact matching cannot. If the pair count lands at roughly the exact-match floor, the analyzers are configured but inert — the exact failure `index-settings` exists to cause and the third validation tier exists to catch.

So the expected result is **more than ~90 name-driven pairs, and comfortably fewer than thousands**. A number far outside that band is a finding to investigate before it is a result to record.

---

## File Structure

| File                                                                  | Change                                                  |
| --------------------------------------------------------------------- | ------------------------------------------------------- |
| `CMS-Providers/configuration/hospitals/index-mappings.json`           | Pin the columns the sweep reads; add analyzed subfields |
| `CMS-Providers/configuration/hospitals/index-settings.json`           | Already declares the analyzers — verify, do not rewrite |
| `CMS-Providers/configuration/hospital-duplicates/index-config.json`   | New: the output pairs index                             |
| `CMS-Providers/configuration/hospital-duplicates/index-mappings.json` | New                                                     |
| `CMS-Providers/configuration/hospital-duplicates/entity-match.json`   | New: the analysis                                       |
| `CMS-Providers/configuration/hospital-duplicates/metrics.json`        | New: population shape only                              |
| `CMS-Providers/configuration.json`                                    | Add the `hospital-duplicates` step                      |
| `CMS-Providers/README.md`                                             | Record what was measured and what the sweep means       |

---

### Task 1: Reload hospitals with the fields the sweep reads

**Files:**

- Modify: `CMS-Providers/configuration/hospitals/index-mappings.json`
- Verify: `CMS-Providers/configuration/hospitals/index-settings.json`

- [ ] **Step 1: Read the existing settings before changing any mapping**

`index-settings.json` already declares `name_clean`, `name_phonetic`, `street_clean`, `street_tokens` and `phone_clean`, with the full street-suffix synonym set. **Confirm each analyzer named in the mapping exists in the settings**, because a mapping referring to an analyzer that is not declared fails index creation, and one referring to a _column_ that does not exist is silently inert.

- [ ] **Step 2: Pin the columns the sweep will read**

At minimum: `Facility ID` (keyword), `Facility Name`, `Address`, `City/Town`, `State`, `ZIP Code`, `Telephone Number`, `County/Parish`. Each analyzed field keeps a `keyword` subfield — aggregating or term-querying an analyzed field otherwise matches nothing.

**Do not map all 38.** Record in the project README which columns are pinned and that the remainder are dynamically mapped, so the gap is documented rather than hidden.

- [ ] **Step 3: Delete the index and reload**

Mappings are immutable on a live index. This is expected:

```bash
curl -s -X DELETE "http://localhost:9200/hospitals-*"
.venv/bin/python execute_project.py --project=CMS-Providers --step=hospitals
```

**Confirm the destructive delete is limited to `hospitals-*`** before running it. The DOT indexes and `chameleon-candidates-2026.08.17-000001` must not be touched — that index is the certified baseline.

- [ ] **Step 4: Verify the analyzers actually fire**

Do not trust that the load reported success. Ask the cluster:

```bash
curl -s "http://localhost:9200/hospitals-000001/_analyze" -H 'Content-Type: application/json' \
  -d '{"field": "Facility Name.phonetic", "text": "SAINT MARYS MEDICAL CENTER"}'
```

Expected: phonetic tokens, not the input words. A field that returns the original text is a subfield that was never analyzed, which scores every pair unevaluable and is invisible in every count.

- [ ] **Step 5: Confirm the document count and commit**

`5,419` documents, matching the CSV. A shortfall means documents were dropped on a parse failure — the hazard that cost 62 rows on this same project before.

---

### Task 2: Configure the duplicate sweep

**Files:**

- Create: `CMS-Providers/configuration/hospital-duplicates/{index-config,index-mappings,entity-match}.json`
- Modify: `CMS-Providers/configuration.json`

- [ ] **Step 1: Write the output index config and mappings**

Model on `DOT-Commercial/configuration/chameleon-detection/`. The pairs index carries no `source` (nothing loads it) — the `dependentRequired` rule from Plan 2 permits exactly this.

- [ ] **Step 2: Write `entity-match.json` in `all-entities` mode**

```json
{
  "source_index": "hospitals-000001",
  "source_settings_step": "hospitals",
  "entity": {
    "key": "Facility ID",
    "key_label": "facility_id",
    "summary_fields": ["Facility Name", "Address", "City/Town", "State"]
  },
  "population": { "mode": "all-entities", "sort_field": "Facility ID", "max_records": null },
  "candidates": {
    "max_candidates": 100,
    "seed_signals": ["name-phonetic", "address", "exact-identifier"]
  },
  "signals": [
    { "type": "name-phonetic", "weight": 0.4, "fields": ["Facility Name"], "subfield": "phonetic" },
    { "type": "name-token", "weight": 0.2, "fields": ["Facility Name"], "subfield": "clean" },
    {
      "type": "address",
      "weight": 0.25,
      "fields": ["Address"],
      "exact_subfield": "clean",
      "fuzzy_subfield": "tokens"
    },
    { "type": "exact-identifier", "weight": 0.15, "phone_fields": ["Telephone Number"] }
  ],
  "scoring": {
    "min_total_score": 0.5,
    "min_signals": 2,
    "require_identity_signal": true,
    "max_pairs_per_predecessor": 10
  }
}
```

**No `lifecycle` block, no `temporal` signal, no gap guards** — the data carries no dated events, and Plan 1 made their absence the way a duplicate-detection project is expressed. Weights are a starting point, not a result; Task 4 measures whether they produce a sane population.

- [ ] **Step 3: Add the step, with `validate` ahead of `entity-match`**

- [ ] **Step 4: Run the validate phase and fix what it reports**

```bash
.venv/bin/python execute_project.py --project=CMS-Providers --step=hospital-duplicates --phase=validate
```

**This is the first real test of the validator against config it did not help write.** Every finding is interesting: a true one proves the tier works on a second project, and a false one is a validator defect to fix in a follow-up rather than to work around here.

- [ ] **Step 5: Commit config only, before running the sweep**

---

### Task 3: Metrics that describe a population, not an accusation

**Files:**

- Create: `CMS-Providers/configuration/hospital-duplicates/metrics.json`

- [ ] **Step 1: Write metrics recording shape only**

Pair counts by score band, and how many distinct facilities appear. **No `canary`, no `triage`** — those are chameleon-detection terms asserting a fraud shape, and hospital records sharing a name are overwhelmingly legitimate multi-record facilities. Importing that vocabulary would make the output read as an accusation the data does not support.

- [ ] **Step 2: No `baseline` key on the first run**

There is nothing to compare against yet. Plan 3 made a missing baseline a _warning_ rather than a failure for exactly this case; Task 4 writes the first one.

- [ ] **Step 3: Validate and commit**

---

### Task 4: Run the sweep and judge the result against the floor

**Files:**

- Create: `CMS-Providers/data/precision/baseline-hospital-duplicates.json` (gitignored under `*/data/`; record the numbers in the README instead)

- [ ] **Step 1: Run it**

```bash
.venv/bin/python execute_project.py --project=CMS-Providers --step=hospital-duplicates
```

Small corpus, so minutes rather than hours.

- [ ] **Step 2: Judge against the measured floor, not against hope**

- **Zero pairs is a failure.** 90 groups share a name exactly and must be retrievable.
- **Roughly 90–120 pairs suggests the analyzers are inert** — exact matches only, no fuzzy lift. Verify with `_analyze` before accepting it.
- **Thousands of pairs suggests the seed is too broad or the floor too low** for a corpus of 5,419.

Record the actual number whatever it is. A result outside the expected band is a finding, and reporting it honestly is worth more than a configuration tuned until it looks right.

- [ ] **Step 3: Inspect a sample by hand**

Pull ten pairs across score bands and judge whether each is the same facility. **Keep names out of the repo** — record the count judged correct and the shapes seen, never the values.

This is the only check that the pairs mean anything. Every automated number here describes a distribution, and a distribution cannot tell you whether the matcher is right.

- [ ] **Step 4: Write the first baseline and record the numbers in the README**

- [ ] **Step 5: Commit with the measured result in the message**

---

### Task 5: Close the open items this was for

**Files:**

- Modify: `README.md`, `CMS-Providers/README.md`, `docs/adding-a-dataset.md`

- [ ] **Step 1: Close the portability open item — or do not**

`README.md` carries: _"The portability claim is unproven by any second project."_

Close it **only if the sweep ran on configuration alone.** If any framework change was needed, the item stays open and records exactly what could not be expressed — that is a more valuable outcome than a closed item, and burying it would make every later portability claim untrustworthy.

Record what it took: how many config files, how many lines, how long, and what the validator caught.

- [ ] **Step 2: Update CMS-Providers' own open items**

Its README says _"No `entity-match` step. Duplicate-clinician detection across registrations would be the natural use."_ Update to reflect what now exists, and keep the **succession** item open — this plan does duplicate detection, and CMS still carries no dated events, so the fraud-succession goal remains blocked on data this project does not download.

- [ ] **Step 3: Add the 7-of-38 mapping gap as a CMS open item**

With the measured numbers, so the next person knows the extract grew and the mapping did not follow.

- [ ] **Step 4: Point `docs/adding-a-dataset.md` at a worked second example**

Its guidance has only ever had one project to point at, and that one has a lifecycle. A duplicate-detection example is the case a reader with no dated events needs.

- [ ] **Step 5: Commit**

---

## Self-Review

**Spec coverage.** Rollout step 10 asks for a `hospital-duplicates` step in `all-entities` mode with `entity.key: "Facility ID"`, no lifecycle, and metrics recording population shape only — Tasks 2–4. Step 11 asks for the documentation pass and closing open item 5's successor — Task 5.

**One spec expectation corrected by measurement.** The spec assumed hospitals needed no mapping work because the shipped `index-mappings.json` "already declares exactly the subfields the signal vocabulary references." True for the seven columns it pins, but the extract has 38, so 31 are dynamically mapped. Task 1 exists because of that, and the spec's claim was made against the five-row stub.

**The acceptance criterion is falsifiable in both directions**, which the spec's "prove the framework runs" was not: zero pairs is a failure, and ~90 pairs is also a failure, because the second means the analyzers are inert. Both bounds come from counting the corpus rather than from judgement.

**Placeholder scan.** Task 2 carries a complete `entity-match.json`; Tasks 1, 3 and 4 describe content whose specifics depend on measurements taken during the task. Every step names its acceptance condition.

**Ordering hazard.** Task 1 must precede Task 2: the validator's third tier checks subfields against the live mapping, so running Task 2's validate step against the old seven-column index would report failures that Task 1 fixes.

**Standing instruction.** If any task requires editing `matching/`, `phase_providers/` or `utils/`, stop and report rather than proceeding. The claim under test is that a second project needs no framework change, and quietly making one would answer the question in the worst possible way.
