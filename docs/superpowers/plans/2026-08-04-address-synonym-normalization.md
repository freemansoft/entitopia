# Address Synonym Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make street matching robust to abbreviation, punctuation and secondary-unit spelling by contracting street suffixes to a canonical token, dropping unit designators, and repairing the `P.O. BOX` tokenization break — in both projects.

**Architecture:** Nearly all of this is Elasticsearch analyzer configuration; no matching code changes. A new char filter runs before the tokenizer to fix `P.O.`, a rewritten synonym filter contracts suffixes instead of expanding them, and a stop filter removes secondary-unit designator words while keeping the unit number. A small Python addition stamps a hash of the analysis block into the index `_meta` at creation and logs — without blocking — when the sweep runs against an index whose tokens predate the current configuration.

**Tech Stack:** Python 3.11 in `.venv`, elasticsearch-py 9.4.1, Elasticsearch 9.4.1 with `analysis-icu` and `analysis-phonetic`, pytest, ruff.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-04-address-synonym-normalization-design.md`. Read it before starting.
- **Never invoke bare `python3` or `pip3`.** Every command uses `.venv/bin/python`.
- **`.venv/bin/python -m ruff check .` must print `All checks passed!`** before any commit. Exemptions need a written reason at the narrowest scope.
- **Comments explain why, never what.** Every function, class and module gets a comment stating why it exists, who calls it, and what breaks if it changes. Do not narrate the body's steps.
- **Never name a real flagged entity** in code, comments, config, docs or commit messages — no company names, DOT numbers, addresses, phones or emails belonging to matched records. Use synthetic placeholders like `100 MAIN ST`. Entities named because they are _excluded_ (filing services, ignore-list values like `GGGG`) are fine.
- **Elasticsearch client calls pass explicit keyword arguments, never `body=`.**
- **Config loads through `file_utils.load_from_file`, producing `SimpleNamespace`.** Use attribute access and `getattr(obj, "key", default)`.
- Elasticsearch runs locally via `docker compose -f docker/compose.yml up -d --build`. `es_config.json` points at `http://localhost:9200`.
- Branch is `address-synonym-normalization`, already created, with the spec committed at `37feabd`.

---

### Task 1: Street analyzer configuration for DOT-Commercial

**Files:**

- Create: `tests/test_street_analysis.py`
- Modify: `DOT-Commercial/configuration/carriers/index-settings.json`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: the analyzer names `street_clean` and `street_tokens` in the DOT carriers index settings, and the filter names `po_box_canon`, `street_suffix_canon`, `unit_designator_stop`. Task 2 copies these three filter definitions verbatim into CMS-Providers. Task 3 hashes the block that contains them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_street_analysis.py`. It builds a throwaway index from the real on-disk settings file, so it tests the configuration that actually ships rather than a copy:

```python
"""Analyzer-level tests for the street analyzers in DOT-Commercial's carriers index.

These exist because street matching lives in Elasticsearch configuration, not in
Python: `AddressSignal` only compares token sets it is handed, so a defect in the
analyzer produces wrong scores that every Python-level test still passes. The
cases below are the three failures measured in the design spec — the `P.O. BOX`
tokenization break, missing abbreviations, and secondary-unit spelling — plus a
standing guard against a regression that a naive `pattern_replace` would cause.

Skipped rather than failed when Elasticsearch is unreachable, so the suite stays
runnable without Docker; the ES-free tests carry the bulk of the coverage.
"""

import json

import pytest
from elasticsearch import Elasticsearch

SETTINGS_PATH = "DOT-Commercial/configuration/carriers/index-settings.json"
TEST_INDEX = "test-street-analysis"


@pytest.fixture(scope="module")
def es_index():
    with open(SETTINGS_PATH) as handle:
        settings = json.load(handle)["settings"]

    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}],
        request_timeout=30,
    )
    try:
        reachable = client.ping()
    except Exception:
        reachable = False
    if not reachable:
        pytest.skip("Elasticsearch is not reachable on localhost:9200")

    client.options(ignore_status=404).indices.delete(index=TEST_INDEX)
    client.indices.create(index=TEST_INDEX, settings=settings)
    yield client
    client.options(ignore_status=404).indices.delete(index=TEST_INDEX)


def tokens(es_index, analyzer, text):
    response = es_index.indices.analyze(index=TEST_INDEX, analyzer=analyzer, text=text)
    return {token["token"] for token in response["tokens"]}


@pytest.mark.parametrize(
    "spelling",
    ["P.O. BOX 1234", "PO BOX 1234", "P O BOX 1234", "P.O.BOX 1234"],
)
def test_po_box_spellings_all_produce_the_same_tokens(es_index, spelling):
    # The standard tokenizer keeps `P.O` as one token, so punct_white — a token
    # filter — rewrote the period in place and emitted the literal token `p o`,
    # which could never equal `po`. 43,799 mailing_street records use a
    # punctuated form and could not match their plain-form counterparts.
    assert tokens(es_index, "street_tokens", spelling) == {"pobox", "1234"}


def test_po_box_spellings_agree_on_the_exact_subfield_too(es_index):
    punctuated = tokens(es_index, "street_clean", "P.O. BOX 1234")
    plain = tokens(es_index, "street_clean", "PO BOX 1234")
    assert punctuated == plain


def test_street_suffix_contracts_to_one_canonical_token(es_index):
    # Contraction, not expansion: under expansion a matching suffix contributed
    # 2 to both the intersection and the set sizes, so the least identifying
    # part of an address counted twice.
    assert tokens(es_index, "street_tokens", "100 MAIN ST") == {"100", "main", "street"}
    assert tokens(es_index, "street_tokens", "100 MAIN STREET") == {"100", "main", "street"}


def test_previously_missing_abbreviations_now_canonicalize(es_index):
    assert tokens(es_index, "street_tokens", "100 RTE 9") == {"100", "route", "9"}
    assert tokens(es_index, "street_tokens", "100 MAIN PL") == {"100", "main", "place"}
    assert tokens(es_index, "street_tokens", "100 MAIN ST NW") == {
        "100", "main", "street", "northwest",
    }


def test_unit_designator_word_is_dropped_but_the_number_survives(es_index):
    # Dropping the word gives STE 200 == UNIT 200 == #200 without handing a
    # shared token to two unrelated addresses that merely both have a suite.
    expected = {"100", "main", "street", "200"}
    assert tokens(es_index, "street_tokens", "100 MAIN ST STE 200") == expected
    assert tokens(es_index, "street_tokens", "100 MAIN ST UNIT 200") == expected
    assert tokens(es_index, "street_tokens", "100 MAIN ST #200") == expected


def test_different_unit_numbers_stay_distinguishable(es_index):
    assert tokens(es_index, "street_tokens", "100 MAIN ST STE 200") != tokens(
        es_index, "street_tokens", "100 MAIN ST APT 400"
    )


def test_ordinal_street_names_are_not_rewritten(es_index):
    # Guards the CMS-Providers bug deleted in Task 2: an unanchored
    # pattern_replace of `(st)` -> `street` turns FIRST into FIRstreet. A
    # synonym filter matches whole tokens, so `1st` and `first` are untouched.
    assert tokens(es_index, "street_tokens", "100 1ST AVE") == {"100", "1st", "avenue"}
    assert tokens(es_index, "street_tokens", "100 FIRST AVE") == {"100", "first", "avenue"}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_street_analysis.py -v
```

Expected: the `P.O. BOX`, contraction, abbreviation, unit-designator and ordinal tests FAIL (the current analyzer emits `p o`, expands `st` to both `st` and `street`, has no `rte`/`pl`/`nw` entries, and keeps `ste`). `test_po_box_spellings_agree_on_the_exact_subfield_too` also fails. If every test is skipped, start Elasticsearch with `docker compose -f docker/compose.yml up -d --build` and re-run — a skipped suite proves nothing here.

- [ ] **Step 3: Add the char filter and the two new token filters**

In `DOT-Commercial/configuration/carriers/index-settings.json`, add a `char_filter` block as a sibling of the existing `filter` and `analyzer` blocks under `settings.index.analysis`:

```json
"char_filter": {
  "po_box_canon": {
    "type": "pattern_replace",
    "pattern": "(?i)\\bp[.\\s]*o[.\\s]*(box|b\\b)",
    "replacement": "pobox"
  }
}
```

Inside the existing `filter` block, **delete `street_suffix_synonyms` entirely** and add these two in its place:

```json
"street_suffix_canon": {
  "type": "synonym",
  "lenient": true,
  "synonyms": [
    "st, str, strt => street",
    "ave, av, aven, avenu, avn => avenue",
    "rd => road",
    "blvd, boul, boulv => boulevard",
    "dr, drv, driv => drive",
    "ln => lane",
    "hwy, hiway, highwy, hway => highway",
    "pkwy, pky, parkwy, pkway => parkway",
    "ct, crt => court",
    "cir, circ, circl, crcl => circle",
    "pl => place",
    "ter, terr => terrace",
    "sq, sqr, squ => square",
    "trl, tr => trail",
    "expy, expwy, expr => expressway",
    "tpke, trnpk, turnpk => turnpike",
    "frwy, fwy, freewy => freeway",
    "rte, rt => route",
    "byp, bypa, bypas => bypass",
    "ext, extn => extension",
    "lk => lake",
    "mt, mnt => mount",
    "pt => point",
    "rdg, rdge => ridge",
    "spg, spng, sprng => spring",
    "vly, vlly => valley",
    "xing, crssng => crossing",
    "plz, plza => plaza",
    "ctr, cen, cent, centre => center",
    "n, no, nth => north",
    "s, so, sth => south",
    "e, ea => east",
    "w, we => west",
    "ne => northeast",
    "nw => northwest",
    "se => southeast",
    "sw => southwest"
  ]
},
"unit_designator_stop": {
  "type": "stop",
  "ignore_case": true,
  "stopwords": [
    "ste", "suite", "apt", "apartment", "unit", "rm", "room",
    "bldg", "building", "fl", "floor", "trlr", "trailer", "lot",
    "sp", "space", "pmb", "dept", "department", "hngr", "hangar",
    "ofc", "office", "slip", "pier"
  ]
}
```

`key` is deliberately absent from that stop list — `KEY LARGO` and `KEY WEST` are street names, and removing the token would erase the identifying word rather than a designator.

- [ ] **Step 4: Rewire the two street analyzers**

Replace the existing `street_clean` and `street_tokens` definitions in the `analyzer` block with:

```json
"street_clean": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "keyword",
  "filter": ["icu_normalizer", "icu_folding", "punct_white", "collapse_whitespace", "trim"]
},
"street_tokens": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "standard",
  "filter": ["icu_normalizer", "icu_folding", "punct_white", "unit_designator_stop", "street_suffix_canon"]
}
```

`unit_designator_stop` runs before `street_suffix_canon` so the designator is gone before suffix mapping is attempted, keeping the two lists independent. `street_clean` gains only the char filter: it stays the literal "byte-identical after normalization" path, so `STE 200` against `UNIT 200` correctly fails the exact test and falls through to the fuzzy path instead of claiming an exact 1.0.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_street_analysis.py -v
```

Expected: PASS, no skips. If index creation raises, confirm `analysis-icu` and `analysis-phonetic` are installed — index creation fails outright without them.

- [ ] **Step 6: Confirm nothing else regressed and lint**

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Expected: all tests pass; ruff prints `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_street_analysis.py DOT-Commercial/configuration/carriers/index-settings.json
git commit -m "Contract street suffixes and repair P.O. BOX tokenization

Replaces bidirectional suffix expansion with contraction to a canonical
token, drops USPS Pub 28 secondary unit designators while keeping the unit
number, and adds a char filter so the P.O. BOX family tokenizes consistently.
The char filter must precede the tokenizer: the standard tokenizer keeps P.O
as one token, so punct_white rewrote the period in place and emitted a
literal 'p o' that could never match 'po'."
```

---

### Task 2: CMS-Providers parity and dead-config removal

**Files:**

- Modify: `CMS-Providers/configuration/hospitals/index-settings.json`
- Modify: `CMS-Providers/configuration/hospitals/index-mappings.json`
- Modify: `CMS-Providers/configuration/doctors-clinicians/index-settings.json`
- Modify: `CMS-Providers/configuration/doctors-clinicians/index-mappings.json`
- Modify: `CMS-Providers/configuration/facillity-affiliations/index-settings.json`

**Interfaces:**

- Consumes: the `po_box_canon`, `street_suffix_canon` and `unit_designator_stop` definitions written in Task 1 — copy them verbatim.
- Produces: a `street_tokens` analyzer and `.tokens` subfields in CMS-Providers. Nothing later in this plan depends on them.

CMS-Providers has no `entity-match.json`, so nothing scores addresses there today. This is parity work: it is verifiable through `_analyze` output but not through match scores. Note that in the commit message so the next reader does not go hunting for a score that moved.

- [ ] **Step 1: Copy the three filter definitions into both scored configs**

In `CMS-Providers/configuration/hospitals/index-settings.json` and `CMS-Providers/configuration/doctors-clinicians/index-settings.json`, add the `char_filter` block and the `street_suffix_canon` and `unit_designator_stop` filters exactly as written in Task 1, Step 3.

- [ ] **Step 2: Delete the dead `street_suffix_map` from all three configs**

Remove this filter from `hospitals`, `doctors-clinicians` **and** `facillity-affiliations`:

```json
"street_suffix_map": {
  "pattern": "(st)",
  "type": "pattern_replace",
  "replacement": "street"
}
```

The pattern is unanchored, so it rewrites `st` anywhere in a token — `FIRST` becomes `FIRstreet`. It is referenced by no analyzer, so it has never run, but it reads as the intended approach and would be copied by the next person. `facillity-affiliations` has no address field and gets this deletion only — no other change.

- [ ] **Step 3: Add the `street_tokens` analyzer and update `street_clean`**

In `hospitals` and `doctors-clinicians`, replace `street_clean` and add `street_tokens`:

```json
"street_clean": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "keyword",
  "filter": ["icu_normalizer", "icu_folding", "punct_white", "collapse_whitespace", "trim"]
},
"street_tokens": {
  "char_filter": ["po_box_canon"],
  "tokenizer": "standard",
  "filter": ["icu_normalizer", "icu_folding", "punct_white", "unit_designator_stop", "street_suffix_canon"]
}
```

Both files need a `collapse_whitespace` filter added alongside the existing ones, since CMS-Providers does not currently define it:

```json
"collapse_whitespace": {
  "pattern": "\\s+",
  "type": "pattern_replace",
  "replacement": " "
}
```

**This goes beyond the spec text, which only called for adding `po_box_canon` to `street_clean`.** It is included because `punct_white` turns each punctuation mark into a space without collapsing the run, so without `collapse_whitespace` two identical addresses differing only in punctuation become different single tokens — the failure `DOT-Commercial/README.md` records for `55 CEDAR ST, STE 4` against `55 CEDAR ST STE 4`. Flag it in review; drop the step if the reviewer wants the spec followed literally.

- [ ] **Step 4: Add the `.tokens` subfield to the address fields**

In `CMS-Providers/configuration/hospitals/index-mappings.json`, the `Address` field becomes:

```json
"Address": {
  "type": "text",
  "fields": {
    "keyword": { "type": "keyword" },
    "clean": { "type": "text", "analyzer": "street_clean" },
    "tokens": { "type": "text", "analyzer": "street_tokens" }
  }
}
```

In `CMS-Providers/configuration/doctors-clinicians/index-mappings.json`, apply the same `tokens` subfield to **both** `adr_ln_1` and `adr_ln_2`, preserving their existing `keyword` and `clean` subfields.

- [ ] **Step 5: Verify both configs create an index and analyze correctly**

```bash
.venv/bin/python - <<'PY'
import json
from elasticsearch import Elasticsearch

client = Elasticsearch(hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=30)
for name in ("hospitals", "doctors-clinicians"):
    path = "CMS-Providers/configuration/{}/index-settings.json".format(name)
    settings = json.load(open(path))["settings"]
    index = "test-cms-{}".format(name)
    client.options(ignore_status=404).indices.delete(index=index)
    client.indices.create(index=index, settings=settings)
    for text in ("P.O. BOX 1234", "100 MAIN ST STE 200", "100 1ST AVE"):
        got = client.indices.analyze(index=index, analyzer="street_tokens", text=text)
        print(name, repr(text), sorted(t["token"] for t in got["tokens"]))
    client.options(ignore_status=404).indices.delete(index=index)
PY
```

Expected output, for both index configs:

```
... 'P.O. BOX 1234'       ['1234', 'pobox']
... '100 MAIN ST STE 200' ['100', '200', 'main', 'street']
... '100 1ST AVE'         ['100', '1st', 'avenue']
```

If index creation raises a `BadRequestError` about an unknown filter, a filter name was mistyped or `collapse_whitespace` was not added.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add CMS-Providers/configuration
git commit -m "Mirror street analyzers into CMS-Providers and delete dead suffix filter

Adds the canonical suffix, unit designator and P.O. BOX filters plus a
street_tokens analyzer and .tokens subfields, matching DOT-Commercial.
CMS-Providers has no entity-match config, so nothing scores addresses there
yet — this is parity, verifiable via _analyze but not via match scores.

Also removes street_suffix_map, a pattern_replace of unanchored (st) ->
street that would rewrite FIRST as FIRstreet. It was referenced by no
analyzer and had never run, but it read as the intended approach."
```

---

### Task 3: Stamp an analysis fingerprint at index creation

**Files:**

- Create: `utils/analysis_fingerprint.py`
- Create: `tests/test_analysis_fingerprint.py`
- Modify: `phase_providers/phase_index_creation.py:54-66`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `utils.analysis_fingerprint.fingerprint_analysis(settings: dict | None) -> str | None`, returning a 16-character hex digest or `None` when the payload declares no analyzers. Task 4 calls this exact function with the same argument shape and compares its result to the value stored under `mappings._meta.analysis_fingerprint`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_analysis_fingerprint.py`:

```python
"""Tests for the analysis fingerprint that detects scoring against stale tokens.

The failure this guards is invisible by construction: the subfield still
exists and still has data, so every other check passes while the scores are
computed from tokens the current analyzers would no longer produce. These tests
pin the two properties that make the guard work — insensitive to key order and
formatting, sensitive to any change in analyzer behavior.
"""

from utils.analysis_fingerprint import fingerprint_analysis


def settings_with(analysis):
    return {"index": {"number_of_shards": 1, "analysis": analysis}}


def test_same_analysis_hashes_the_same_regardless_of_key_order():
    a = settings_with({"filter": {"x": {"type": "stop"}}, "analyzer": {"y": {"tokenizer": "standard"}}})
    b = settings_with({"analyzer": {"y": {"tokenizer": "standard"}}, "filter": {"x": {"type": "stop"}}})
    assert fingerprint_analysis(a) == fingerprint_analysis(b)


def test_changing_a_synonym_changes_the_hash():
    a = settings_with({"filter": {"s": {"type": "synonym", "synonyms": ["st, street"]}}})
    b = settings_with({"filter": {"s": {"type": "synonym", "synonyms": ["st => street"]}}})
    assert fingerprint_analysis(a) != fingerprint_analysis(b)


def test_unrelated_index_settings_do_not_change_the_hash():
    # Shard count has no effect on tokens, so bumping it must not look like an
    # analyzer change — otherwise the warning cries wolf and gets ignored.
    a = {"index": {"number_of_shards": 1, "analysis": {"filter": {"x": {"type": "stop"}}}}}
    b = {"index": {"number_of_shards": 5, "analysis": {"filter": {"x": {"type": "stop"}}}}}
    assert fingerprint_analysis(a) == fingerprint_analysis(b)


def test_missing_analysis_block_is_none_not_a_hash_of_nothing():
    # None means "this index declares no analyzers, there is nothing to
    # compare"; a hash would make every analyzer-free index look mismatched
    # against every other one.
    assert fingerprint_analysis({"index": {"number_of_shards": 1}}) is None
    assert fingerprint_analysis({}) is None
    assert fingerprint_analysis(None) is None


def test_analysis_at_the_top_level_is_accepted():
    nested = {"index": {"analysis": {"filter": {"x": {"type": "stop"}}}}}
    flat = {"analysis": {"filter": {"x": {"type": "stop"}}}}
    assert fingerprint_analysis(flat) == fingerprint_analysis(nested)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_analysis_fingerprint.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'utils.analysis_fingerprint'`.

- [ ] **Step 3: Write the implementation**

Create `utils/analysis_fingerprint.py`:

```python
"""Stable hash of an index's analysis settings, so a sweep can tell whether the
tokens it scores were produced by the configuration currently on disk.

Exists because phase_entity_match can verify that a scored subfield EXISTS but
not that it was built by the current analyzers. Change a synonym list, skip the
reindex, and every address score is computed from stale tokens with no error
anywhere — the silent-wrong-output failure this repo keeps hitting. Callers use
this to make that mismatch visible; it is deliberately not a mechanism for
preventing it.
"""

import hashlib
import json


def fingerprint_analysis(settings):
    """Hash the analysis block of an index-settings payload; None when absent.

    Takes the plain dict handed to indices.create rather than the SimpleNamespace
    config, because that dict is the exact structure that reached Elasticsearch —
    hashing anything earlier in the pipeline would let a serialization change move
    the fingerprint without any analyzer changing, which is the one thing a
    staleness check must never do.

    Returns None rather than a hash of an empty block so that "declares no
    analyzers" stays distinguishable from "declares an empty analysis block".
    Conflating them would make every analyzer-free index compare equal to every
    other one and report a false match.
    """
    if not settings:
        return None
    analysis = (settings.get("index") or {}).get("analysis") or settings.get("analysis")
    if not analysis:
        return None
    canonical = json.dumps(analysis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_analysis_fingerprint.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Stamp the fingerprint at index creation**

In `phase_providers/phase_index_creation.py`, add `analysis_fingerprint` to the existing import:

```python
from utils import analysis_fingerprint, elasticsearch_utils, file_utils
```

Replace the `indiciesClient.create(...)` call (currently at lines 56-60) with:

```python
            settings = self.get_index_settings()
            # Recorded at creation because this is the only moment the settings
            # that built the index and the index itself are both in hand. A
            # later reader can compare it against the config on disk and find
            # out whether the tokens it is about to score are current.
            fingerprint = analysis_fingerprint.fingerprint_analysis(settings)
            create_args = {"index": phase_config.index, "settings": settings}
            if fingerprint:
                create_args["mappings"] = {
                    "_meta": {"analysis_fingerprint": fingerprint}
                }

            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.create(**create_args)
                self.logger.info(
                    "Created index {} with analysis fingerprint {} returned {}".format(
                        phase_config.index, fingerprint, r
                    )
                )
```

Leave the `except BadRequestError` handler and the `put_alias` block below it untouched. `phase_index_mappings` later calls `put_mapping(properties=...)`, which does not touch `_meta`, so the stamp survives.

- [ ] **Step 6: Verify the stamp lands on a real index**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection --phase=index-create
curl -s "localhost:9200/chameleon-candidates-*/_mapping" | .venv/bin/python -m json.tool | grep -A 3 '_meta'
```

Expected: an `analysis_fingerprint` with a 16-character hex value. `chameleon-detection` is used here rather than `carriers` because its index-create is cheap and creates no data.

Note: `chameleon-detection/index-settings.json` does not exist, so `get_index_settings()` returns `None` and no fingerprint is stamped. That is the correct behavior and confirms the `if fingerprint` guard — to see a real fingerprint, check the carriers index after Task 7's reload instead.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
git add utils/analysis_fingerprint.py tests/test_analysis_fingerprint.py phase_providers/phase_index_creation.py
git commit -m "Stamp an analysis fingerprint into index _meta at creation

Records a hash of the analysis block when the index is built, so a later
sweep can tell whether the tokens it scores came from the configuration
currently on disk. Nothing reads it yet."
```

---

### Task 4: Log, without blocking, when the source index has stale tokens

**Files:**

- Modify: `phase_providers/phase_entity_match.py:145-184`
- Modify: `DOT-Commercial/configuration/chameleon-detection/entity-match.json`
- Modify: `tests/test_analysis_fingerprint.py`

**Interfaces:**

- Consumes: `utils.analysis_fingerprint.fingerprint_analysis(settings) -> str | None` from Task 3, and the `mappings._meta.analysis_fingerprint` value it stamps.
- Produces: a new optional `source_settings_step` key in `entity-match.json`. Nothing later depends on it.

The check is advisory by design. Sweeping an older index on purpose is legitimate — comparing two runs, reproducing a past result — so a hard stop would block real work. What is not acceptable is doing it unknowingly.

- [ ] **Step 1: Add the config pointer**

`entity-match.json` names its source index but nothing tells it which step's settings built that index. Add the key at the top level of `DOT-Commercial/configuration/chameleon-detection/entity-match.json`, directly after `"source_index"`:

```json
  "source_index": "carriers-000001",
  "source_settings_step": "carriers",
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_analysis_fingerprint.py`:

```python
import logging

from phase_providers.phase_entity_match import PhaseEntityMatch


def match_phase():
    return PhaseEntityMatch(es=None, project="DOT-Commercial", one_step="x", project_config=None)


def test_mismatched_fingerprint_logs_an_error_and_returns_true(caplog):
    # Returns True because the sweep must still run: an operator comparing
    # against an older index is doing something legitimate. Only silence is
    # unacceptable.
    with caplog.at_level(logging.ERROR):
        result = match_phase()._check_analysis_fingerprint("carriers-000001", "aaaa", "bbbb")
    assert result is True
    assert "aaaa" in caplog.text and "bbbb" in caplog.text


def test_matching_fingerprint_logs_no_error(caplog):
    with caplog.at_level(logging.ERROR):
        match_phase()._check_analysis_fingerprint("carriers-000001", "aaaa", "aaaa")
    assert caplog.text == ""


def test_index_predating_the_stamp_warns_rather_than_claiming_a_mismatch(caplog):
    # An index built before the fingerprint existed carries no _meta. That is
    # unknown, not wrong — reporting it as a mismatch would train the operator
    # to ignore the message.
    with caplog.at_level(logging.WARNING):
        match_phase()._check_analysis_fingerprint("carriers-000001", None, "bbbb")
    assert "no analysis fingerprint" in caplog.text.lower()
    assert "does not match" not in caplog.text.lower()
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_analysis_fingerprint.py -v
```

Expected: FAIL with `AttributeError: 'PhaseEntityMatch' object has no attribute '_check_analysis_fingerprint'`.

- [ ] **Step 4: Implement the comparison**

In `phase_providers/phase_entity_match.py`, add `analysis_fingerprint` to the existing utils import:

```python
from utils import analysis_fingerprint, elasticsearch_utils, file_utils, id_utils
```

Add these two methods to `PhaseEntityMatch`, directly after `_preflight`:

```python
    def _check_analysis_fingerprint(self, source_index, stored, expected):
        """Report, without blocking, that the index's tokens predate the analyzers.

        Deliberately advisory and always returns True. Sweeping an older index on
        purpose is legitimate — comparing two runs, reproducing a past result —
        so refusing would block real work. What is unacceptable is doing it
        unknowingly: every name and address score would be derived from tokens
        the current configuration would no longer produce, and no other check in
        _preflight can see that, because the subfields still exist and still
        hold data.

        A missing stored value means the index predates the stamp, which is
        unknown rather than wrong. Reporting that as a mismatch would train the
        operator to ignore the message that matters.
        """
        if expected is None:
            return True
        if stored is None:
            self.logger.warning(
                "Source index {} carries no analysis fingerprint, so it cannot be "
                "checked against the analyzers on disk (expected {}). It predates "
                "this check; recreate and reload it to enable the "
                "comparison.".format(source_index, expected)
            )
            return True
        if stored != expected:
            self.logger.error(
                "Source index {} was built with analysis fingerprint {} but the "
                "configuration on disk is {}. Every token-based score in this "
                "sweep comes from the OLDER analyzers. Recreate and reload the "
                "index to score against current config; continuing "
                "anyway.".format(source_index, stored, expected)
            )
        return True

    def _expected_analysis_fingerprint(self, config):
        """Fingerprint of the index-settings.json that should have built the source index.

        entity-match.json runs in its own step, so it cannot find the source
        index's settings without being told which step owns them — hence the
        optional source_settings_step key. Absent, the check is skipped rather
        than guessed at, so projects that never adopt the key are unaffected.
        """
        step = getattr(config, "source_settings_step", None)
        if not step:
            self.logger.debug("No source_settings_step configured; skipping fingerprint check")
            return None
        settings_config = file_utils.load_from_project_file(
            self.project, self.project_config.configurationDir, step, "index-settings.json"
        )
        if not settings_config or not getattr(settings_config, "settings", None):
            return None
        settings = json.loads(json.dumps(settings_config.settings, default=vars))
        return analysis_fingerprint.fingerprint_analysis(settings)
```

Add `import json` to the module's imports, sorted with the others (`import datetime`, `import json`, `import logging`, `import uuid`).

- [ ] **Step 5: Call it from `_preflight`**

`_preflight` already fetches the mapping at line 164. Change its signature and add the check. Replace the `mapping = ...` block and the `properties` loop with:

```python
        mapping = self.es.indices.get_mapping(index=source_index)
        properties = {}
        stored_fingerprint = None
        for index_mapping in mapping.body.values():
            properties = index_mapping.get("mappings", {}).get("properties", {})
            stored_fingerprint = (
                index_mapping.get("mappings", {}).get("_meta", {}).get("analysis_fingerprint")
            )
            break

        self._check_analysis_fingerprint(source_index, stored_fingerprint, expected_fingerprint)
```

Change the signature to `def _preflight(self, source_index, required_subfields, expected_fingerprint):` and the call site at line 90 to:

```python
        if not self._preflight(
            source_index, finder.scored_subfields(), self._expected_analysis_fingerprint(config)
        ):
            return
```

Leave the `missing` subfield check and its `return False` exactly as they are — that one still blocks, because a missing subfield means the sweep cannot produce correct output at all.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_analysis_fingerprint.py -v
.venv/bin/python -m pytest
```

Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add phase_providers/phase_entity_match.py tests/test_analysis_fingerprint.py DOT-Commercial/configuration/chameleon-detection/entity-match.json
git commit -m "Log when entity-match sweeps an index built by older analyzers

_preflight could confirm a scored subfield exists but not that it was built
by the current analyzers, so changing a synonym list and skipping the reindex
produced silently wrong scores. The check is advisory and never blocks:
sweeping an older index deliberately is legitimate, doing it unknowingly is
not."
```

---

### Task 5: Promote the measurement harness into `scripts/`

**Files:**

- Create: `scripts/measure_address_analyzers.py`

**Interfaces:**

- Consumes: nothing from earlier tasks; it reads analyzer definitions from the on-disk settings file and pairs from a results index.
- Produces: a command-line report. Task 7 runs it as the acceptance gate.

- [ ] **Step 1: Write the script**

Create `scripts/measure_address_analyzers.py`. It replays real emitted pairs through the previous and current analyzer configurations and reports the score delta, which is the only way to see what a config change actually did:

```python
"""Replay emitted candidate pairs through two street analyzer configurations.

Exists because an analyzer change cannot be reviewed by reading it: the effect
on scoring depends on how real address text distributes, and the spec's central
claim — that contraction improves precision — was established with this and
would otherwise be an assertion. Run before and after a change to the street
analyzers; it is the acceptance gate for that work.

Reads the CURRENT analyzers from the on-disk settings file, so it never drifts
from what ships. The baseline is passed in as a git revision of the same file,
which keeps the comparison honest without pinning a copy of the old config here.

One limitation that must be stated with any result: the sample is drawn from
pairs the BASELINE configuration already emitted, so it measures precision
changes but structurally cannot show recall gains — a pair the old analyzer
never surfaced is not in the index to sample. Only a full re-sweep shows those.
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from elasticsearch import Elasticsearch

from matching.tokens import containment

# Mirrors AddressSignal's own arithmetic. Kept in sync by hand rather than
# imported because the signal needs a CarrierDoc and a ScoringContext, neither
# of which exists here; if AddressSignal's formula changes, this must follow.
FUZZY_SCALE = 0.7
CROSS_STATE_FUZZY_PENALTY = 0.5
SETTINGS_PATH = "DOT-Commercial/configuration/carriers/index-settings.json"


def analyzer_settings(revision=None):
    """Settings dict from the working tree, or from a git revision for the baseline."""
    if revision is None:
        with open(SETTINGS_PATH) as handle:
            return json.load(handle)["settings"]
    blob = subprocess.run(
        ["git", "show", "{}:{}".format(revision, SETTINGS_PATH)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(blob)["settings"]


def build_index(client, index, settings):
    client.options(ignore_status=404).indices.delete(index=index)
    client.indices.create(index=index, settings=settings)


def analyze_all(client, index, analyzer, texts):
    """Token sets for every text, keyed by text. Threaded because this is thousands of calls."""

    def one(text):
        response = client.indices.analyze(index=index, analyzer=analyzer, text=text)
        return frozenset(token["token"] for token in response["tokens"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(texts, pool.map(one, texts), strict=True))


def score(exact_a, exact_b, fuzzy_a, fuzzy_b, same_state):
    if exact_a and exact_a == exact_b:
        return 1.0
    result = containment(fuzzy_a, fuzzy_b) * FUZZY_SCALE
    if not same_state:
        result *= CROSS_STATE_FUZZY_PENALTY
    return result


def fetch_pairs(client, index, size, seed):
    response = client.search(
        index=index,
        size=size,
        source=[
            "predecessor.phy_street",
            "successor.phy_street",
            "predecessor.phy_state",
            "successor.phy_state",
            "total_score",
        ],
        query={
            "function_score": {
                "query": {
                    "bool": {
                        "must": [
                            {"exists": {"field": "predecessor.phy_street"}},
                            {"exists": {"field": "successor.phy_street"}},
                        ]
                    }
                },
                "random_score": {"seed": seed, "field": "_seq_no"},
            }
        },
        track_total_hits=False,
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs-index", required=True, help="A chameleon-candidates-* index")
    parser.add_argument("--baseline", required=True, help="git revision holding the OLD settings")
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--floor", type=float, default=0.35, help="scoring.min_total_score")
    parser.add_argument("--weight", type=float, default=0.20, help="address signal weight")
    parser.add_argument("--total-weight", type=float, default=0.94, help="sum of all signal weights")
    args = parser.parse_args()

    client = Elasticsearch(
        hosts=[{"host": "localhost", "port": 9200, "scheme": "http"}], request_timeout=120
    )
    pairs = fetch_pairs(client, args.pairs_index, args.size, args.seed)
    if not pairs:
        print("No pairs with both streets populated; nothing to measure.", file=sys.stderr)
        return 1
    streets = sorted({p["predecessor"]["phy_street"] for p in pairs}
                     | {p["successor"]["phy_street"] for p in pairs})
    print("pairs {}, unique streets {}".format(len(pairs), len(streets)), file=sys.stderr)

    cache = {}
    for label, revision in (("old", args.baseline), ("new", None)):
        index = "test-address-measure-{}".format(label)
        build_index(client, index, analyzer_settings(revision))
        for analyzer in ("street_clean", "street_tokens"):
            cache[(label, analyzer)] = analyze_all(client, index, analyzer, streets)
        client.options(ignore_status=404).indices.delete(index=index)

    rows = []
    for pair in pairs:
        a = pair["predecessor"]["phy_street"]
        b = pair["successor"]["phy_street"]
        state_a = pair["predecessor"].get("phy_state")
        same_state = bool(state_a) and state_a == pair["successor"].get("phy_state")
        scores = {
            label: score(
                cache[(label, "street_clean")][a],
                cache[(label, "street_clean")][b],
                cache[(label, "street_tokens")][a],
                cache[(label, "street_tokens")][b],
                same_state,
            )
            for label in ("old", "new")
        }
        rows.append((scores["new"] - scores["old"], pair.get("total_score")))

    changed = [r for r in rows if abs(r[0]) > 1e-9]
    up = [r for r in changed if r[0] > 0]
    down = [r for r in changed if r[0] < 0]
    print("\nchanged {}/{} ({:.1f}%)".format(len(changed), len(rows), 100 * len(changed) / len(rows)))
    if up:
        print("  up   {} (mean {:+.4f})".format(len(up), sum(r[0] for r in up) / len(up)))
    if down:
        print("  down {} (mean {:+.4f})".format(len(down), sum(r[0] for r in down) / len(down)))

    crossed_up = crossed_down = 0
    for delta, total in rows:
        if total is None:
            continue
        shifted = total + delta * args.weight / args.total_weight
        if total < args.floor <= shifted:
            crossed_up += 1
        elif shifted < args.floor <= total:
            crossed_down += 1
    print("approx pairs crossing the {} floor: +{} / -{}".format(args.floor, crossed_up, crossed_down))
    print(
        "\nNOTE: sampled from pairs the BASELINE already emitted, so this shows "
        "precision change only. Recall gains require a full re-sweep."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the pre-change baseline**

The baseline revision is the commit before Task 1 landed. Find it and run:

```bash
BASE=$(git log --format=%H --all -1 -- docs/superpowers/specs/2026-08-04-address-synonym-normalization-design.md)
.venv/bin/python scripts/measure_address_analyzers.py \
  --pairs-index chameleon-candidates-2026.08.02-000001 --baseline "$BASE"
```

Expected, within a few pairs of these figures from the spec:

```
changed 797/2000 (39.9%)
  up   7 (mean +0.0294)
  down 790 (mean -0.0736)
approx pairs crossing the 0.35 floor: +0 / -46
```

If `changed` is near zero, the working tree still has the old analyzers and Task 1 was not applied. If it is far above 45%, a stop-list or synonym entry differs from Task 1.

- [ ] **Step 3: Lint and commit**

```bash
.venv/bin/python -m ruff check .
git add scripts/measure_address_analyzers.py
git commit -m "Add the address analyzer measurement harness

Replays emitted pairs through two analyzer configurations so a street config
change can be reviewed by its effect rather than by reading it. Reports
precision change only; the sample cannot contain pairs the baseline never
emitted."
```

---

### Task 6: Documentation

**Files:**

- Modify: `DOT-Commercial/README.md:19-37` (Open items) and `:89` (street analyzer paragraph)
- Modify: `DOT-Commercial/docs/chameleon-pipeline-explainer.md:194`

**Interfaces:**

- Consumes: the analyzer names and behavior from Task 1, and the measured figures from Task 5.
- Produces: nothing code depends on.

- [ ] **Step 1: Replace the street analyzer paragraph**

`DOT-Commercial/README.md:89` currently begins "**Streets have two subfields because one tokenizer cannot serve both purposes.**" Replace that whole paragraph with:

```markdown
**Streets have two subfields because one tokenizer cannot serve both purposes.** `street_clean` uses a `keyword` tokenizer for exact-after-normalization comparison; `street_tokens` uses a standard tokenizer, drops secondary-unit designators, and contracts street suffixes to a canonical token for fuzzy matching. `street_clean` also carries a `collapse_whitespace` filter: `punct_white` turns each punctuation mark into a space without collapsing the run, so without it `55 CEDAR ST, STE 4` and `55 CEDAR ST STE 4` were different single tokens and identical addresses silently produced zero candidates.

**Suffixes contract to one canonical token rather than expanding to both forms.** `street_suffix_canon` maps `st, str, strt => street`, not `st, street`. Under expansion a matching suffix contributed **2** to both the intersection and the set sizes in `containment`, so the least identifying part of an address counted twice: two unrelated same-state addresses sharing only `AVE` scored **0.420** on a signal weighted 0.20, and score **0.233** after the change. Measured over 2,000 sampled pairs, 40% of address scores move and 790 of them move down.

**Secondary-unit designators are removed, not canonicalized.** `unit_designator_stop` drops `STE`/`APT`/`UNIT`/`RM`/`BLDG` and keeps the unit number, so `STE 200` and `UNIT 200` still match on `200`. Mapping them all to one shared `unit` token was measured and rejected: it handed a free common token to any two addresses that merely both had a suite, lifting an unrelated pair in different states from 0.000 to 0.140. `KEY` is deliberately absent from the stop list because `KEY LARGO` and `KEY WEST` are street names.

**A `po_box_canon` char filter runs before the tokenizer.** The standard tokenizer keeps `P.O` as a single token, so `punct_white` — a _token_ filter — rewrote the period in place and emitted the literal token `p o`, which could never equal `po`. 43,799 `mailing_street` records use a punctuated form against 193,722 plain, and none of them could match across the spelling. A token filter cannot fix this; the repair has to happen before tokenization.
```

- [ ] **Step 2: Add the deferred work as an Open item**

Append to the numbered list in the "Open items" section of `DOT-Commercial/README.md` (the list ending at line 36), using the same `1. **bold claim**` form as its neighbors:

```markdown
1. **`AddressSignal` scores a street as an unordered bag of tokens, so the house number carries no more weight than the word `STREET`.** Two different buildings on one road overlap on everything except one token: `100 MAIN ST` against `200 MAIN ST` scores 0.75 containment, **0.525** after `fuzzy_scale`, on a signal weighted 0.20. Canonical-token contraction reduced but did not remove this, since it shrinks the denominator as well. The fix is to parse the street into house number, street name and secondary unit and score the parts separately, letting a house-number mismatch cap the result rather than cost one token. Deferred from the address synonym normalization work.
```

- [ ] **Step 3: Correct the pipeline explainer**

`DOT-Commercial/docs/chameleon-pipeline-explainer.md:194` reads "fuzzy token form with street-suffix synonyms (`st`→`street`)." Replace that clause with:

```markdown
fuzzy token form that contracts street suffixes to a canonical token
(`st`→`street`), drops secondary-unit designators such as `STE`/`APT`
while keeping the unit number, and normalizes the `P.O. BOX` family.
```

- [ ] **Step 4: Verify no flagged entity was named**

```bash
git diff --cached -- '*.md' | grep -nE '\b[0-9]{5,8}\b|\bDOT ?#' || echo "no bare identifiers"
```

Expected: `no bare identifiers`, or only the measured aggregate counts (`43,799`, `193,722`, `2,000`). Every address in the new text must be synthetic — `100 MAIN ST`, `55 CEDAR ST`. Aggregate counts are explicitly permitted; company names, DOT numbers and real addresses are not.

- [ ] **Step 5: Commit**

```bash
git add DOT-Commercial/README.md DOT-Commercial/docs/chameleon-pipeline-explainer.md
git commit -m "Document the street analyzer changes and the deferred component split

Records why suffixes contract rather than expand, why unit designators are
dropped rather than canonicalized, and why the P.O. BOX repair has to be a
char filter. Adds the house-number weighting problem as an Open item."
```

---

### Task 7: Reindex, re-sweep, and verify against the sanity anchor

**Files:** none modified. This task is operational and produces the evidence that the change works on real data.

**Interfaces:**

- Consumes: everything from Tasks 1-6.
- Produces: a verified `carriers` index and sweep. Nothing depends on it.

This is the only task that touches 2,085,536 documents and it takes a while. The previous `carriers` index is left in place until the sweep is verified.

- [ ] **Step 1: Confirm Elasticsearch is up with the required plugins**

```bash
docker compose -f docker/compose.yml up -d --build
curl -s "localhost:9200/_cat/plugins?h=component" | sort -u
```

Expected: `analysis-icu` and `analysis-phonetic`. Index creation fails outright without them. If the build fails with `docker-credential-desktop: executable file not found`, prefix the command with `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`.

- [ ] **Step 2: Recreate and reload the carriers index**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=carriers
```

Expected: a new `carriers-<today>-000001` index. Confirm the fingerprint from Task 3 landed and the document count is in the same range as the previous index:

```bash
curl -s "localhost:9200/carriers-*/_mapping" | .venv/bin/python -m json.tool | grep -A 3 '_meta'
curl -s "localhost:9200/_cat/indices/carriers-*?h=index,docs.count"
```

- [ ] **Step 3: Confirm the fingerprint check is quiet on a fresh index**

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection 2>&1 | tee /tmp/sweep.log | grep -i "fingerprint"
```

Expected: no error line. If it reports a mismatch immediately after a reload, `source_settings_step` in `entity-match.json` points at the wrong step.

- [ ] **Step 4: Check the sweep against the acceptance gate**

```bash
grep -i "entity-match complete" /tmp/sweep.log
```

Expected: a pair count in the same order of magnitude as the 429,505 recorded in `DOT-Commercial/README.md`. A count near zero means the reload or the analyzers are wrong — investigate before continuing.

- [ ] **Step 5: Verify the sanity anchor still ranks at the top**

`DOT-Commercial/README.md` records that the top of the list is dominated by carriers re-registering under a byte-identical legal name at the same address and phone within days of shutdown. Confirm that shape survived:

```bash
curl -s "localhost:9200/chameleon-candidates-*/_search" -H 'Content-Type: application/json' -d '{
  "size": 20, "sort": [{"total_score": "desc"}],
  "_source": ["total_score", "gap_days", "matched_on"]
}' | .venv/bin/python -m json.tool | grep -E 'total_score|gap_days'
```

Expected: top scores at or above 0.90 with small `gap_days`. **If that shape no longer surfaces, the change is wrong — stop and report rather than adjusting thresholds to compensate.**

Report the counts back rather than pasting carrier identifiers: per `CLAUDE.md`, a matched record's name, DOT number or address must not be written into the repo or a commit message.

- [ ] **Step 6: Re-run the measurement harness against the new results index**

```bash
BASE=$(git log --format=%H -1 -- docs/superpowers/specs/2026-08-04-address-synonym-normalization-design.md)
.venv/bin/python scripts/measure_address_analyzers.py \
  --pairs-index chameleon-candidates-<today>-000001 --baseline "$BASE"
```

Expected: `changed` near 0% — the new index was built with the new analyzers, so replaying it against the current config should show almost no movement. A large delta here means the reload used stale settings.

- [ ] **Step 7: Record the observed figures**

Update the two measured figures in `DOT-Commercial/README.md`'s Open items — the emitted pair count and the reviewable-set size — with what this run produced, if they moved. Then:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
git add DOT-Commercial/README.md
git commit -m "Record measured figures from the post-normalization sweep"
```

If nothing moved, skip the commit and say so.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the three defects and the DOT analyzer design → Task 1; CMS-Providers parity and the `street_suffix_map` deletion → Task 2; the stale-analyzer guard, split across stamping → Task 3 and log-only comparison → Task 4; the acceptance gate → Task 5; the README, explainer and deferred Open item → Task 6; the rollout steps and sanity anchor → Task 7. The spec's Risks section is covered by Task 7 Step 5 (sanity anchor) and Task 1's stop-list note on `KEY`.

**Two deviations from the spec, both flagged in place for the reviewer:**

1. Task 2 Step 3 adds `collapse_whitespace` to CMS-Providers, which the spec did not call for. Justified by the failure `DOT-Commercial/README.md` records; the step says to drop it if the reviewer wants the spec followed literally.
2. Task 4 introduces a `source_settings_step` config key the spec did not name. The spec required the comparison without saying how `phase_entity_match` would find the source index's settings, and it has no existing pointer to them.

**Type consistency.** `fingerprint_analysis(settings) -> str | None` is defined in Task 3 and called with the same shape in Task 4 Step 4. `_check_analysis_fingerprint(self, source_index, stored, expected) -> bool` has the same signature in the Task 4 test and implementation. `_preflight` gains a third parameter in Step 5 and its only call site is updated in the same step.
