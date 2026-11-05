# Designing a schema for fuzzy matching in Elasticsearch

## The problem

You have two records that describe the same real-world thing, and no field in either
one matches the other exactly. The name is spelled differently, or abbreviated. The
address differs by a comma. There is no shared key, because the records came from two
systems that never agreed on one.

This is entity resolution, and it turns up wherever data is loosely coupled: customer
master data, supplier deduplication, provider directories, sanctions screening. The
worked example here is a public-records project that looks for a business shut down by
a regulator and reopening under a new registration — but nothing below depends on that.
The shape is the same whenever "is this the same thing?" cannot be answered with a join.

The claim of this article is narrow: **the schema is the matcher.** By the time you
write scoring code, every decision that matters about how text gets compared has
already been made in the mapping.

_Every figure below is a point-in-time measurement against one extract of one public
dataset. They are evidence that a problem was real and roughly how big it was, not
values to expect on your own data._

## Why not vector search

Elasticsearch lets you declare the equivalences. `ste` means `suite`, `st` means
`street`, this list of suffixes is noise. The equivalence relation is yours — written
down, reviewable in one file, changeable when the data surprises you.

Vector similarity is whatever the embedding model learned. There is no guarantee that
the abbreviations you actually care about sit close together in that space, and no way
to assert that they should: you cannot add `ste, suite` to a model's weights the way
you add it to a synonym filter. When it gets an equivalence wrong, your recourse is to
pick a different model.

The unresolved question is what you would even embed. One field, or the whole record
serialized into a string? Name alone, or name plus address plus phone? That choice
determines what similarity means.

If the unit is a whole name or a whole address, an engine whose matching rules you
declare and can inspect is the better fit.

## The finding that reordered the work

Before any of the analyzer work below, this project spent time evaluating whether the
analyzers it already had were the right ones. That evaluation turned up something more
useful than a verdict: the phonetic analyzers had been configured on every name field
for months, and nothing had ever queried them. There was an indexing layer and no query
layer at all.

That reordered the work: no claim that one encoder beats another is measurable until
something searches. Build the baseline first, then upgrade the encoder. Do it the other
way around and an unmeasured analyzer sits in a mapping for months looking correct.

## One field, four indexings

Multi-fields let one source value be indexed several ways at once. This is the central
move.

```json
"legal_name": {
  "type": "text",
  "fields": {
    "keyword":     { "type": "keyword" },
    "clean":       { "type": "text", "analyzer": "name_clean" },
    "phonetic":    { "type": "text", "analyzer": "name_phonetic" },
    "phonetic_bm": { "type": "text", "analyzer": "name_phonetic_bm" }
  }
}
```

One name, four views: verbatim, normalized-but-complete, and two phonetic encodings. A
matcher compares whichever view suits the question — exact for a strict identity check,
cleaned for literal token overlap, phonetic for sound-alike collision — without
re-analyzing anything at query time.

The reasonable alternative is a `keyword` field with a `normalizer`, which folds the
value into one canonical form. This project uses none, anywhere. A normalizer produces
a single token, and the goal here is several _different_ tokenizations of the same
text, compared independently and weighted separately. Canonicalizing early throws away
the disagreement between views, and the disagreement is the signal.

## Two phonetic encoders, not one

The name fields carry two phonetic encodings, weighted independently. They fail
differently, which is the point.

| Input     | `double_metaphone` | `beider_morse` |
| --------- | ------------------ | -------------- |
| `SMITH`   | `SM0 XMT`          | `zmit`         |
| `SMYTH`   | `SM0 XMT`          | —              |
| `SCHMIDT` | `XMT SMT`          | `zmit`         |

Double-metaphone collides spelling variants exactly: `SMITH` and `SMYTH` produce
identical codes. Beider-Morse collides across languages: `SMITH` and `SCHMIDT` both
encode to `zmit`, which double-metaphone only partially matches. Drop either arm and
you lose a class of near-miss name.

Two settings are load-bearing.

`double_metaphone` replaced plain `metaphone`. It emits a primary _and_ an alternate
encoding rather than one, and `max_code_len` is raised from its default of 4 to 6 —
four characters over-collide on company-name tokens, where many words share their
opening consonants.

`beider_morse` is pinned to `["english","spanish"]` rather than left to guess. Language
guessing on short corporate tokens is unstable, and unstable guessing makes the index
non-reproducible between runs, which is worse than guessing wrong consistently.

One behaviour to expect rather than debug: Beider-Morse emits multiple tokens only for
names ambiguous across the configured languages. Measured against a live cluster,
`GONZALEZ` yields four tokens and `SMITH` yields one. A single token is correct output,
not a sign the filter is unwired.

## Stop the suffixes before encoding, and only there

Nearly every company name in a corpus like this ends in the same handful of words:
`LLC`, `INC`, `TRUCKING`, `LOGISTICS`, `TRANSPORT`. They carry no identifying
information and they appear in most records.

```json
"name_phonetic": {
  "tokenizer": "standard",
  "filter": ["icu_normalizer", "icu_folding", "punct_white",
             "carrier_suffix_stop", "phonetic_dm"]
}
```

The stop filter runs _before_ the phonetic filter, so suffixes never reach the encoder,
and it runs only in the two phonetic analyzers. `name_clean` keeps the full name.

Why stop them at all: scoring happens in application code over token sets, not through
a BM25 query, so there is no inverse-document-frequency term quietly discounting common
words. Left in, they dominate every comparison — two unrelated companies agree on `LLC`
and `TRUCKING` and score as a partial match.

Why only in the phonetic analyzers: something has to retain the complete string for
exact comparison.

The consequence worth stating out loud: a company named literally `TRUCKING LLC`
reduces to zero tokens. That is _no signal_, not _no match_, and the scorer has to
distinguish them.

## Streets need two subfields

An address answers two different questions, and one tokenizer cannot answer both.

`street_clean` uses a `keyword` tokenizer, so the whole street becomes a single token
after normalization — punctuation stripped, whitespace collapsed, case and accents
folded. That answers "are these identical once tidied?"

`street_tokens` uses a `standard` tokenizer plus a synonym filter mapping `st`→`street`,
`ste`→`suite`, `n`→`north`. That answers "do these overlap enough to be the same place
written differently?"

Keeping both lets a matcher rank an exact normalized hit above a fuzzy token overlap,
which is the ordering you want. Collapsing to one subfield forces a choice between them.

## The whitespace bug that retrieved nothing

`punct_white` replaces each punctuation character with a space. It does not collapse
the resulting run.

So `55 CEDAR ST, STE 4` became `55 CEDAR ST  STE 4`, with two spaces, while
`55 CEDAR ST STE 4` stayed as one. Under a `keyword` tokenizer those are two different
single tokens. Two identical addresses produced no candidate at all, and nothing
errored.

The fix is a `collapse_whitespace` filter after `punct_white` in the `street_clean`
chain.

This is the characteristic failure of analyzer work: not a wrong answer but an empty
one, which is indistinguishable from a legitimate negative. Budget verification time
accordingly.

## The dead filter that would have been wrong anyway

An earlier generation of this schema defined a street-suffix filter as a
`pattern_replace` of `(st)` with `street`.

Two things are wrong with it. It was referenced by no analyzer, so it never ran. And
the pattern is unanchored against already-folded text, so had it ever been wired up it
would have rewritten `stone` to `streetone` and `street` to `streetreet`.

Term-level synonyms are the right tool for suffix expansion. A regex over the whole
string is not.

## Pin every field you intend to query

Elasticsearch infers a field's type from the first document that carries it. That
inference happens under concurrency, so it is not deterministic between runs of the
same load. Once made, a non-conforming value raises a parsing exception and **the whole
document is rejected**, not the field.

Measured on one extract: an identifier column that looked numeric was inferred as
`float`, and rows carrying values like `NONE` or `S00000030887` dropped 36,788 of
5,647,567 documents. A ZIP column inferred as `long` dropped alphanumeric ZIPs outright
and mangled leading zeros — `00602` indexed as `602`.

Pin everything you will query, join on, or score. Identifiers and codes are `keyword`
even when they look numeric, because you want `term` queries on them and not
arithmetic.

The subtler case: a field written onto a document by an ingest or enrichment step
inherits the _target_ index's mapping, not the source's. A value that is `keyword`
upstream can arrive as analyzed `text` downstream, at which point an uppercase `term`
query matches nothing.

## Legacy dates, and why the obvious mapping is worse

Source data carries non-ISO dates; this one supplies Oracle-style `01-JUN-74`. Dynamic
date detection does not recognize that, so the field silently becomes text.

The obvious repair is to map it with a `dd-MMM-yy` pattern. That is worse than leaving
it broken. Java's `yy` pivots to 2000–2099, so a 1974 registration indexes as **2074**,
and every range query built on it is quietly wrong with no error anywhere.

The pattern that works:

- Map the field as `date` with **ISO formats only**.
- Convert in a script processor at ingest, applying an explicit century pivot — here,
  a two-digit year above 30 resolves to 19xx.
- **Validate that the result is a real calendar date by constructing it**, not by
  matching digits. `9999-99-99` and `2021-02-29` both pass a shape regex and then fail
  at index time, costing the whole document.
- Attach an `on_failure` handler that _removes the field_, so a script failure drops a
  value instead of a record.

Where a date is only ever read by application code, mapping it `keyword` is safer
still — ISO strings sort and range-compare correctly as text.

## Ask the cluster what it actually did

Configuration that parses can be completely inert. Elasticsearch accepts a mapping for
a field that never appears in your data: it applies nothing and dynamically maps the
real column as plain `text` instead.

Three datasets in this project shipped exactly that way after the upstream agency
renamed its columns. The phonetic and cleaning analyzers were configured, looked
correct in review, and never ran on a single document.

Nothing catches this except asking the live index:

```bash
curl -s localhost:9200/carriers-000001/_analyze \
  -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic","text":"SMITH TRUCKING LLC"}'
# expect tokens for SMITH only — three tokens means the suffix filter is not wired in
```

Pair that with a `_mapping` read on the actual field names and a `_simulate` call
against the ingest pipeline. The discipline is to ask the cluster what it did after
every change, rather than trusting that the config was accepted.

## What to take away

Before writing any matching code:

- Index each identity field several ways. Do not canonicalize early.
- Use two phonetic encoders that fail differently, and weight them separately.
- Stop high-frequency suffixes before encoding, and keep one unstopped view.
- Give addresses both a whole-string subfield and a tokenized one.
- Pin every field you will query. Identifiers are `keyword`.
- Map dates ISO-only, convert at ingest, and validate the calendar rather than the
  shape.
- Verify every analyzer against the live index with `_analyze` before trusting it.
