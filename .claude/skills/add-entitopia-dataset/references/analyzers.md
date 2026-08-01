# Analyzers for similarity matching

Name and address analysis is where most of the matching accuracy lives. Every choice below was measured against a live cluster; the measurements are included because they are what justify the choice, and because a future change should be checked the same way.

Requires the `analysis-icu` and `analysis-phonetic` plugins. Index creation fails outright without them — a loud failure, not a silent one.

## Names

### Use two phonetic encoders, weighted separately

`double_metaphone` and `beider_morse` catch different classes of evasion. Measured:

| Input     | `double_metaphone` | `beider_morse` |
| --------- | ------------------ | -------------- |
| `SMITH`   | `SM0 XMT`          | `zmit`         |
| `SMYTH`   | `SM0 XMT` ✅       | —              |
| `SCHMIDT` | `XMT SMT`          | `zmit` ✅      |

Double-metaphone collides **spelling variants** exactly. Beider-Morse collides **cross-language variants** that double-metaphone only partially matches. Dropping either arm loses a class of name change, so both are indexed as separate subfields and weighted independently in the matching config.

### Prefer `double_metaphone` over plain `metaphone`

`metaphone` is the weakest encoder in the plugin. `double_metaphone` emits a primary _and_ an alternate encoding, which is what makes the `SMITH`/`SCHMIDT` partial overlap above possible at all.

Raise `max_code_len` from its default of 4 to **6**. Four characters over-collide badly on company-name tokens.

### Pin Beider-Morse's language set

```json
"phonetic_bm": {
  "type": "phonetic",
  "encoder": "beider_morse",
  "rule_type": "approx",
  "name_type": "generic",
  "languageset": ["english", "spanish"]
}
```

Left to guess, Beider-Morse infers a language per token. On short corporate tokens that inference is unstable, which makes the index non-reproducible between runs. Pin the set to the languages actually present in your data.

`rule_type: approx` is the fuzzy-matching ruleset and is the point of this arm; `exact` defeats it.

Two behaviours worth knowing before you write a verification check:

- It emits multiple tokens **only** for names ambiguous across the configured languages. `GONZALEZ` yields four; `SMITH` and `RODRIGUEZ` yield one each. A single token is correct output, not a wiring failure.
- `replace` is not supported by this encoder — it always adds tokens rather than replacing them, so this will be the largest subfield in the index.

### Strip corporate suffixes before phonetic encoding

Nearly every company name ends in `LLC`, `INC`, `TRUCKING`, `TRANSPORT`, `LOGISTICS`. Because scoring happens in Python rather than through BM25, there is **no IDF to discount them automatically** — left in place they dominate every comparison, and two unrelated companies look similar because both are an LLC.

Put a `stop` filter ahead of the phonetic filter, in the phonetic analyzers only. Keep `.clean` unfiltered so the full name is still available.

Two consequences to handle rather than discover:

- A company named literally `TRUCKING LLC` reduces to **zero tokens**. The scorer must treat an empty token stream as "no signal", never as "no match".
- Stripping suffixes makes most names reduce to **one token**, which turns a set-overlap score effectively binary. That interacts badly with weighting several name signals — see `matching-signals.md`.

### Blend containment with Jaccard, do not use Jaccard alone

Abbreviation is a deliberate evasion tactic. `SMITH TRUCKING LLC` → `SMITH LLC` scores Jaccard 0.5 but containment 1.0. Pure Jaccard punishes exactly the behaviour you are hunting; pure containment treats any subset as perfect. Blending keeps full overlap ranked above a subset match while still scoring abbreviation highly.

## Addresses

### Two subfields, because one tokenizer cannot do both jobs

- **`street_clean`** — `keyword` tokenizer, for exact-after-normalization comparison. The whole address becomes one token.
- **`street_tokens`** — `standard` tokenizer plus street-suffix synonyms, for fuzzy comparison.

A keyword tokenizer structurally cannot do fuzzy matching: `123 MAIN ST` and `123 MAIN STREET STE 4` are simply different strings to it. Both subfields are needed.

### Collapse whitespace in the keyword subfield

This one cost real recall and produced no error. A `pattern_replace` filter turning punctuation into spaces does not collapse the resulting runs, and `trim` only strips the ends. So:

```text
"55 CEDAR ST, STE 4"  -> ["55 cedar st  ste 4"]   (two spaces)
"55 CEDAR ST STE 4"   -> ["55 cedar st ste 4"]
```

Since this is a single keyword token feeding candidate retrieval, two records at the **same address** differing only by a comma produced **zero candidates** — never scored, nothing logged, run reported success.

Add a `\s+` → `" "` filter after the punctuation filter and before `trim`. Verify with the punctuation variants:

```bash
for s in "55 CEDAR ST, STE 4" "55 CEDAR ST STE 4" "55  CEDAR   ST STE 4" "55 CEDAR ST. STE 4"; do
  curl -s -XPOST "http://localhost:9200/<index>/_analyze" -H 'Content-Type: application/json' \
    -d "{\"analyzer\":\"street_clean\",\"text\":\"$s\"}"
done
```

All four must produce the identical single token.

### Use a term-level synonym filter for street suffixes, not a regex

An earlier attempt used `pattern_replace` with the pattern `(st)` → `street`. Unanchored, that maps `stone` → `streetone` and `street` → `streetreet`. It happened to be harmless only because no analyzer ever referenced it.

Use `synonym` with explicit term mappings — `st, street` / `ave, avenue` / `ste, suite` — which operates on whole tokens.

## Phones and other identifiers

Strip to digits with a `pattern_replace` over a `keyword` tokenizer so formatting differences vanish.

Then **reject placeholders in code, not in the analyzer.** Real data contains `0000000000` and `(000) 000-0000`; left alone they cluster thousands of unrelated entities into one apparent match. Reject repeated-digit values and anything shorter than a plausible number before comparing.

## Verifying analyzer work

Configuration that parses can be completely inert — a mapping naming a column that does not exist applies nothing and reports no error. After any analyzer change:

```bash
# does the corporate-suffix stop actually fire?
curl -s -XPOST "http://localhost:9200/<index>/_analyze" -H 'Content-Type: application/json' \
  -d '{"analyzer":"name_phonetic","text":"SMITH TRUCKING LLC"}'
# expect tokens for SMITH only
```

The check that matters most is not "does it produce tokens" but **"do the pairs I expect to collide actually collide"**. Analyze two names you believe are the same entity and confirm their token sets overlap. That is what the encoder is for, and it is the only way to know a change helped.
