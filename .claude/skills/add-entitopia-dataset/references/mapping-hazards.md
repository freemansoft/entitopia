# Mapping hazards

Every failure here has happened in this project against real data. Each one reported success while producing wrong output. They are grouped by symptom so you can recognize the one you are currently looking at.

## The root cause behind most of them

Elasticsearch infers a field's type from the first document that carries it. Under `parallel_bulk`'s concurrency, _which_ document arrives first is not deterministic — so the inferred type can differ between runs of the same load. Once inferred, a non-conforming value raises `document_parsing_exception` and **the entire document is rejected**, not just that field.

`phase_index_populate` counts and logs those failures rather than raising, so a lossy run still exits zero.

**The rule: pin every field you will query, join on, score, or aggregate.** Identifiers and codes are `keyword` even when they look numeric — you never do arithmetic on them, and `keyword` cannot be mis-inferred.

## Symptom: the load reports fewer documents than the CSV has rows

**Cause: a column mixes numeric and non-numeric values.**

An ID column inferred as `float` from the first rows it saw; later rows containing `'NONE'` or `'S00000030887'` then failed. **36,788 of 5,647,567 documents dropped.** Deterministic and lossy on every run, though _which_ rows drop varies with thread ordering.

A ZIP column inferred as `long`; alphanumeric ZIPs like `'7221120ND'` dropped 62 rows.

```json
"insp_carrier_state_id": { "type": "keyword" }
```

The profiler flags this as `MIXED TYPES` with example offenders.

## Symptom: codes lost their leading zeros

**Cause: a code column inferred as a numeric type.**

Puerto Rico ZIP `00602` became `602`. Zero-padded identifiers like `'00085526'` became `85526`.

This one is sneakier than it looks, because it can happen **before Elasticsearch ever sees the data** — `pd.read_csv` will infer `int64` for an all-numeric column and strip the zeros in the loader. Check the dtype, not just the mapping:

```python
import pandas as pd
d = pd.read_csv(path, nrows=200000)
print(d["phy_zip"].dtype, d["phy_zip"].head(3).tolist())
```

If pandas gives `object`, the strings survive to Elasticsearch and a `keyword` mapping preserves them. If it gives `int64`, the zeros are already gone and the mapping cannot recover them.

**A `keyword` mapping does not protect you from this.** Confirmed live in this project: `Facility ID` is mapped `keyword`, and CMS facility IDs are zero-padded six digits — yet `010001` indexes as `10001`. The mapping faithfully stores what it is handed, and it is handed an integer. Because that column is also the `id_field`, every document ID is wrong, and any join against another source that kept the padding fails.

The lesson generalizes: **a mapping constrains what Elasticsearch does with a value, not what the loader did to it before.** When a column is an identifier, check the pandas dtype as well as the mapping.

## Symptom: an enrichment policy matches zero documents

**Cause: the join field has different types on the two sides.**

`crashes.dot_number` was inferred `float` while `carriers.dot_number` was `keyword`. A join between them matched nothing — no error, just an empty result and carrier documents missing an entire enriched section.

Pin the join field to the same type in **both** indexes. Where the source data carries a decimal-formatted integer like `'3240797.0'`, coerce it in an ingest script:

- A `convert` processor with `type: long` **fails silently** here — Elasticsearch calls `Long.parseLong()`, which throws on a decimal-point string.
- A Painless `(long)` cast does not have that limitation.

## Symptom: a `term`/`terms` query matches zero rows, but the value is obviously present

**Cause: the field is `text`, not `keyword`.**

A bare string dynamically maps as `text` with a `.keyword` multi-field — not `keyword` outright. `term`/`terms` run against the exact-value field, and standard analysis lowercases the indexed token, so a query for `"ACTIVE"` never matches the indexed `"active"`.

This zeroed all four entity selectors at once. Reproduced directly:

```text
terms on out_of_service_orders.status  -> 0 hits
terms on ...status.keyword             -> 1 hit
```

Two further traps in the same area:

- The dynamic `.keyword` sub-field carries `ignore_above: 256`, so a long value silently stops being indexed there.
- **Enriched fields inherit the _target_ index's mapping, not the source's.** A field mapped `keyword` in its own index becomes dynamic `text` once an enrich processor writes it onto another document. Map every enriched field explicitly on the target.

## Symptom: dates are wrong by a century, or documents vanish

**Cause: a legacy date format, and the obvious fix being worse than the bug.**

Source data carries Oracle-style `01-JUN-74`. Elasticsearch's dynamic date detection does not recognize it, so the field silently lands as text.

Mapping it as `dd-MMM-yy` is **worse than leaving it broken**: Java's `yy` pattern pivots to 2000–2099, so a 1974 registration resolves to **2074**. In a project whose whole premise is timing, that is a wrong answer rather than a missing one.

### The pattern that works

1. **Map the field ISO-only.** Do not list the legacy format in the mapping — a document bypassing the pipeline must fail loudly, not silently resolve to the wrong century.

   ```json
   "add_date": { "type": "date", "format": "strict_date_optional_time||yyyy-MM-dd" }
   ```

2. **Convert in a Painless `script` processor** with an explicit century pivot. Pick the pivot from the data's real range — registrations reaching back to the 1970s mean two-digit years above ~30 are 19xx.

3. **Pass through values that are already ISO.** Without this the pipeline is not idempotent: after one pass the field _is_ ISO, so re-indexing the same document strips it. Reindexing is routine, and the loss is silent.

4. **Validate the result is a real calendar date by constructing it**, not by matching digits. A regex accepts `9999-99-99` and `2021-02-29`; `LocalDate.of` rejects both and gets leap years right.

   ```painless
   try {
       LocalDate.of(Integer.parseInt(parts[0]), Integer.parseInt(parts[1]), Integer.parseInt(parts[2]));
       return;                       // already valid ISO — leave it alone
   } catch (Exception e) {
       ctx.remove('add_date');       // shape-valid but impossible — drop the field
       return;
   }
   ```

   This matters because an invalid value reaching a strict `date` field throws `document_parsing_exception` and costs the **whole record**.

5. **Use `on_failure` to remove the field, not `ignore_failure: true`.** With `ignore_failure`, a script exception leaves the raw legacy value in place, which then fails at index time and drops the document — the exact outcome the script existed to prevent. Note the handler must be nested inside the processor's own config map; a sibling key is rejected.

### When to skip all of that

If a date is only ever read client-side, map it **`keyword`**. ISO strings sort and range-query correctly because they compare lexicographically, and a keyword field cannot reject a document. Several datasets here do exactly this, with parsing done in Python.

## Symptom: analyzers appear configured but do nothing

**Cause: the mapping names a column that does not exist.**

Elasticsearch accepts a mapping for a field that never appears in the data. It applies nothing and dynamic-maps the real column as plain `text`. All three datasets in one project shipped this way after the upstream provider renamed its columns — the phonetic and cleaning analyzers looked correct and had never run.

Verify against the data, not the config:

```bash
curl -s "http://localhost:9200/<index>/_mapping/field/<field>"
curl -s -XPOST "http://localhost:9200/<index>/_analyze" -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic","text":"SMITH TRUCKING LLC"}'
```

## Symptom: a "full" load is suspiciously small

**Cause: `num_rows` left at a validation sample.**

A cap of `50000` in `index-config.json` truncates silently. Worse, if that index then feeds an enrichment policy, the truncation propagates into everything enriched from it. Set `num_rows: null` for full loads.

## Checklist before writing `index-mappings.json`

- Run the profiler; resolve every WARNING.
- Pin every identifier and code as `keyword`, including ones that look numeric.
- Pin every field an enrich policy will write onto this index.
- Decide each date field: ISO-mapped with a conversion script, or `keyword` and parsed client-side.
- Confirm the column names exist in the actual CSV header.
- Check `num_rows` is `null`.
- After loading, compare the indexed count against the CSV row count. A gap is documents dropped, not rounding.
