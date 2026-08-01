# Verification

Configuration that parses can be entirely inert. Elasticsearch accepts a mapping for a column that does not exist, a policy rebuild that silently no-ops, and a query that matches nothing — all without error. These checks are how you find out what actually happened.

Cluster is `http://localhost:9200`, no auth, per `docker/compose.yml`.

## The habit

**After every mapping, analyzer, pipeline, or policy change, ask the cluster what it did.** Not whether the config was accepted — what it did. The gap between those two is where every bug in this project has lived.

Mappings and analyzers are immutable on a live index, so the loop is: edit config → **delete the index** → re-run `index-create` / `index-map` → reload. Verifying against an index that predates your change proves nothing. Deleting is expected, not a failure.

## After a mapping change

```bash
curl -s "http://localhost:9200/<index>/_mapping/field/<field>"
```

Confirm the type is what you pinned. A response showing `"type": "text"` with a `.keyword` sub-field means the field is **dynamically mapped** — your pin did not take, usually because the column name in the mapping does not match the CSV header.

List what exists and how big it is:

```bash
curl -s "http://localhost:9200/_cat/indices?v"
```

## After a load

Compare the indexed count to the source row count:

```bash
curl -s "http://localhost:9200/<index>/_count"
wc -l <source.csv>
```

A gap means documents were **rejected**, not rounded. `phase_index_populate` logs and counts failures rather than raising, so the run still exits zero. Any gap deserves a look at the logged `document_parsing_exception` messages — they name the offending field.

## After an analyzer change

Two different questions, and the second matters more.

**Does it tokenize as intended:**

```bash
curl -s -XPOST "http://localhost:9200/<index>/_analyze" -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic","text":"SMITH TRUCKING LLC"}'
```

Expect tokens for `SMITH` only if a corporate-suffix stop filter is wired in. Three tokens' worth of output means it is not.

**Do the pairs you expect to collide actually collide** — this is the real test, because an analyzer can produce plausible tokens and still fail to match the things you built it for:

```bash
for w in SMITH SMYTH SCHMIDT; do
  curl -s -XPOST "http://localhost:9200/<index>/_analyze" -H 'Content-Type: application/json' \
    -d "{\"analyzer\":\"name_phonetic\",\"text\":\"$w\"}"
done
```

Compare the token sets directly. Same for address punctuation variants — all of `"55 CEDAR ST, STE 4"`, `"55 CEDAR ST STE 4"`, `"55  CEDAR   ST STE 4"` must yield the **identical** single token from the keyword-tokenized subfield.

## After a pipeline change

Dry-run before loading millions of rows. Include the ugly cases, not just the happy path:

```bash
curl -s -XPOST "http://localhost:9200/_ingest/pipeline/<name>/_simulate" \
  -H 'Content-Type: application/json' \
  -d '{"docs":[
    {"_source":{"add_date":"01-JUN-74"}},
    {"_source":{"add_date":"23-JAN-02"}},
    {"_source":{"add_date":"2020-05-05"}},
    {"_source":{"add_date":""}},
    {"_source":{"add_date":"garbage"}},
    {"_source":{"add_date":"9999-99-99"}},
    {"_source":{"add_date":"2021-02-29"}}]}'
```

What to look for:

- The century pivot resolves correctly — `01-JUN-74` → `1974-06-01`, not `2074`.
- An **already-converted** value survives unchanged. If it does not, the pipeline is not idempotent and reindexing silently strips the field.
- Blank and garbage drop the **field**, leaving the document intact.
- Shape-valid but impossible dates (`9999-99-99`, `2021-02-29`) are dropped too — a regex accepts them and they then cost the whole document at index time.

**Confirm the registered pipeline matches the file** before concluding anything, or you are testing the previous version:

```bash
curl -s "http://localhost:9200/_ingest/pipeline/<name>"
```

## After an enrichment policy rebuild

```bash
curl -s "http://localhost:9200/.enrich-<policy-name>*/_count"
```

Compare against the source index count. A snapshot far smaller than its source is stale, or the source was truncated by a leftover `num_rows` cap.

Watch the rebuild log for `Failed to delete enrichment policy due to conflict`. That warning means the policy was **not** rebuilt and everything downstream is running on the old snapshot. Delete the referencing pipeline first and retry.

Then confirm the data actually landed on a target document:

```bash
curl -s "http://localhost:9200/<target-index>/_search" -H 'Content-Type: application/json' \
  -d '{"size":1,"query":{"exists":{"field":"<enriched.path>"}},"_source":["<enriched.path>"]}'
```

Zero hits means the enrichment produced nothing, whatever the policy reported.

## Before trusting a selector query

Any `term`/`terms` query against an enriched or dynamically-mapped field deserves a direct check, because matching zero is indistinguishable from "nothing qualifies":

```bash
curl -s "http://localhost:9200/<index>/_count" -H 'Content-Type: application/json' \
  -d '{"query":{"terms":{"<field>":["ACTIVE"]}}}'
curl -s "http://localhost:9200/<index>/_count" -H 'Content-Type: application/json' \
  -d '{"query":{"terms":{"<field>.keyword":["ACTIVE"]}}}'
```

If the second returns hits and the first does not, the field is `text` and needs pinning. This is how a four-selector outage was found.

## End-to-end, with synthetic data

Unit tests cover the scoring math without a cluster. What they cannot cover is whether the _wiring_ is right — field paths, subfield names, enrichment reach.

Build a dozen synthetic documents including one pair that should match on a known signal, index them **through the real pipeline** rather than hand-inserting, run the sweep, and check:

- The sweep finds predecessors at all. Zero predecessors with no error is the classic selector-mapping failure.
- The expected pair is emitted, with the expected signals in its fired list.
- Each contribution equals `weight × score` against the config.
- A pair that should be rejected _is_ rejected — a temporal-only match with no identity signal is a good negative test.

Then delete the synthetic documents and confirm the indices are empty again. Leaving them behind poisons the next person's verification.

## Reading a run summary

The sweep reports predecessors processed, candidates examined, pairs emitted, truncations, and errors. Three things to check every time:

- **Zero pairs is a warning, never a success.** If the summary is quiet about it, that is a bug in the summary.
- **Truncation counts** mean candidate queries hit their ceiling and real matches may have been cut off.
- **Errors** are counted rather than raised so one bad document cannot kill a long sweep — which means a non-zero count needs looking at rather than ignoring.
