# Blog series outline: entity resolution with Elasticsearch

Planning document for a three-article series drawn from this repository. It is not
itself an article. Each article outline below names the repo file, commit, or
measurement that backs each section, so a drafter can verify a claim without
re-deriving it, and gives a word budget per section so the draft has a shape before
it has sentences.

## Why a series and not one article

The repo already documents its own decisions well, but all of it is **reference
material in final state**: READMEs, a glossary, design specs, a theory-of-operation
doc. What does not exist anywhere is the **narrative**: the order things happened in,
what was tried and abandoned, and why the current shape is the shape. That story lives
only in the git log, and it is the part that transfers to someone outside this project.

Three audiences want three different slices of it, and one article cannot serve them
without burying two of the three. Fewer, harder-hitting articles beat more of them:
an article stays in the series only while it carries evidence no other article does.

**Article 1 was re-scoped on 2026-11-08.** The first draft toured one mapping and
restated the analyzer reference the DOT README already publishes. The new Article 1
answers the question readers search for, how to tell that two listings are the same
business when nothing matches exactly, and backs it with a measured comparison of
six matchers. The draft was rewritten in place and renamed from
`2026-08-30-01-schema-for-fuzzy-matching.md` to
`2026-08-30-01-finding-the-same-business.md`.

**The LLM article was dropped on 2026-11-08.** Its evidence was the thinnest of the
four, and the series is stronger as three articles about matching. Its draft has been
removed from the tree.

## Framing

The series is about **identity resolution in loosely coupled data**: records from
different sources describing the same real-world entity, with no shared key and no
ground truth. Fraud detection is one application of that machinery, used throughout as
the worked example rather than as the thesis. A reader running a provider directory, a
customer MDM, or a supplier deduplication project should be able to follow the whole
series without caring about trucking.

| #   | Working title                                                                            | Primary audience                    | Target words | One-line thesis                                                                                                                                  |
| --- | ---------------------------------------------------------------------------------------- | ----------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Use Elasticsearch to match businesses when the data differs deliberately or accidentally | Search engineers, ER / MDM builders | 1,725        | Pick a matcher by the kind of difference you expect, and test it against the look-alikes your search returns, not random pairs.                  |
| 2   | Tuning an entity matcher: fix the candidate search before the weights                    | ER / MDM data engineers             | 2,100        | Search first, then score the results: the candidate search and the ignored values decide what the matcher finds, and the weights moved it least. |
| 3   | Building a general entity matcher: test it on a second dataset                           | Data and platform engineers         | 1,775        | A second dataset is the cheapest test of a general approach: it found four defects the first hid, and every failure reported success.            |

Each article stands alone and cross-links to the others. Series total: ~5,600 words.

### Published

Each post was checked word for word against the repo on 2026-11-10. "Text matches"
is the commit whose article text the post carries, and "Links pinned to" is the commit
its GitHub links point at, so the figures a reader follows stay the ones quoted. Each
post ends with a revision-history line that the repo copy does not carry.

| #   | Post date (Blogger) | Post                                                                                                                     | Title on Blogger                                                                                           | Text matches                                                                              | Links pinned to |
| --- | ------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | --------------- |
| 1   | 2026-11-03          | [use-elasticsearch-to-match](https://joe.blog.freemansoft.com/2026/11/use-elasticsearch-to-match.html)                   | Use Elasticsearch to Match Organizations and people when the Data differs Deliberately or Accidentally - 1 | `512922b` (PR #52)                                                                        | `87aae26`       |
| 2   | 2026-11-10          | [tuning-entity-matcher-fix-candidate](https://joe.blog.freemansoft.com/2026/11/tuning-entity-matcher-fix-candidate.html) | Tuning an entity matcher: fix the candidate search before the weights - 2                                  | `6a22331` (PR #57), except the post has no comma in "a corporate parent or a placeholder" | `824a2a7`       |

A change to a published article's text belongs in both places: the post, and the repo
copy here.

---

## House style

The per-article **Register** lines below all point here. Style, voice, structure and
the verification commands live in one place, the `entitopia-blog-writing` skill
(`.claude/skills/entitopia-blog-writing/`), so the rules cannot drift between this
outline and the skill. Where an older line in this outline disagrees with the skill,
the skill wins.

### Using the budgets

The per-section word counts are a drafting constraint, not a target to hit exactly. A
section coming in 30% over is a signal that it is really two sections, or that it
belongs in a different article. A section coming in far under usually means the evidence
for it is thinner than the outline implied. Check the source before padding it.

---

## Article 1 — Use Elasticsearch to match businesses when the data differs deliberately or accidentally

- **Audience:** engineers asked to make Elasticsearch match records that no exact query
  will join (names with variant spellings, abbreviated business names, addresses that
  differ only in punctuation) and who have to choose between phonetic encoders, edit
  distance, n-grams and plain tokens.
- **Target length:** 1,600–1,900 words; the section budgets below sum to 1,725.
- **Register:** house style. The most config-heavy article of the three; budget three
  code blocks, each an `_analyze` call or one mapping fragment.
- **Thesis:** Pick a matcher by the kind of difference you expect between two records of
  the same entity, and measure it against the look-alike names your candidate search
  actually returns. Against random pairs every matcher looks nearly perfect; against
  look-alikes they separate sharply, and no single one wins every kind of edit.
- **The measurement behind sections 3, 4 and 6** (2026-11-08, `carriers-2026.11.06-000001`,
  read-only): 400 sampled legal names, each edited seven evasion-shaped ways on its
  first word, scored against 1,987 look-alike names drawn from the top `match` results
  for each name. Synthetic edits on real names, names only, one seeded sample. Reproduce it
  with `scripts/compare_name_matchers.py`; add `--negatives random` for the random-pair
  figures.

| Section                                                    | Words | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Backed by                                                                                                                                                                                          |
| ---------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. The problem, stated generically                         | 125   | Two records, same real-world entity, no field matches exactly. Separate agencies, separate keys, no join. Fraud, dedupe, MDM, provider directories are the same shape.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `README.md` § Purpose, Glossary                                                                                                                                                                    |
| 2. Why not vector search                                   | 150   | You _declare_ an equivalence in Elasticsearch and review it in one file. A model's similarity is whatever it learned and cannot be asserted into; when it is wrong, the recourse is a different model. And nothing decides what you would embed: one field, or the whole record serialized? Argue from **control and the undefined unit**, not model capability, which decays.                                                                                                                                                                                                                                                                                                                                                                                                                                                      | design spec § "The question that started this"                                                                                                                                                     |
| 3. Random pairs flatter every matcher                      | 250   | The headline. Against random unrelated names, all six matchers scored AUC 0.97–1.00. Against look-alikes the search returns, they spread from 0.72 to 0.93: double-metaphone 0.785, Beider-Morse 0.718, exact tokens 0.755, fuzzy tokens 0.887, whole-name edit distance 0.852, character trigrams 0.932. Phonetic codes collide fully on more than 1% of look-alikes, so name alone cannot decide a pair. A candidate diagram, settled after the rewrite: a decision flow from "what kind of difference do you expect?" to a matcher.                                                                                                                                                                                                                                                                                              | `scripts/compare_name_matchers.py`; `carriers-2026.11.06-000001`                                                                                                                                   |
| 4. Phonetic encoders: what they catch and what breaks them | 225   | Double-metaphone and Beider-Morse each collide pairs the other keeps apart (live `_analyze`: `KNIGHT`/`NIGHT`, `STEPHENS`/`STEVENS` only under double-metaphone; `SMITH`/`SCHMIDT` only under Beider-Morse). Both handle respelling and word order. A one-letter typo breaks them: AUC 0.446 and 0.328 on typos, worse than chance, because the typo changes the code while look-alikes share it. `max_code_len` 4→6 because four characters over-collide on company words; Beider-Morse pinned to english+spanish because language guessing is unstable. On hospital names the encoder barely matters: `double_metaphone` at length 4 led plain `metaphone` by about 0.01 AUC on three seeds, `max_code_len` is silently ignored by plain `metaphone`, and DOT's length 6 scored below the default, so the setting is per dataset. | `DOT-Commercial/README.md` § Name and address analyzers; `index-settings.json`; `scripts/compare_name_matchers.py`; `scripts/compare_phonetic_encoders.py`; `CMS-Providers/README.md` encoder item |
| 5. Strip the words every name shares, before encoding      | 150   | Nearly every name ends in LLC/INC/TRUCKING. Scoring happens in Python, so there is no BM25 IDF to discount them. The stop filter runs before phonetic encoding and only in the phonetic analyzers; `.clean` keeps the full name. A name that is entirely suffixes reduces to zero tokens: "no signal", not "no match". CMS-Providers needed its own list of generic hospital words.                                                                                                                                                                                                                                                                                                                                                                                                                                                 | same; commit `61ff602`                                                                                                                                                                             |
| 6. Edit distance and n-grams: where they win               | 200   | Neither is in the sweep, and the article says so. Whole-name edit distance catches typos and respellings (AUC 0.996, 0.995) and fails word order (0.492, no better than chance). Fuzzy tokens with `AUTO` edits catch typos (0.999) but not abbreviation (0.576). Character trigrams are the best single matcher overall (0.932) yet catch only 13% of edits at a 1% false-match rate. The case for combining: each kind of edit has a matcher that catches it and one that does not.                                                                                                                                                                                                                                                                                                                                               | `scripts/compare_name_matchers.py`                                                                                                                                                                 |
| 7. Addresses need an exact subfield and a fuzzy one        | 175   | `street_clean` (keyword tokenizer) for exact-after-normalization; `street_tokens` for fuzzy. One tokenizer cannot do both. `punct_white` left a run of spaces uncollapsed, so identical addresses retrieved **zero candidates** until `collapse_whitespace`. Suffixes contract to one canonical token: expansion counted them twice, and a pair sharing only `AVE` fell from 0.420 to 0.233; 40% of 2,000 sampled address scores moved.                                                                                                                                                                                                                                                                                                                                                                                             | `DOT-Commercial/README.md` § Name and address analyzers; commit `1b77071`; `scripts/measure_address_analyzers.py`                                                                                  |
| 8. The house-number problem, still open                    | 125   | An address scored as a bag of tokens gives the house number no more weight than `STREET`: `100 MAIN ST` against `200 MAIN ST` scores 0.525 on a signal weighted 0.20. The fix is to parse number, name and unit and score them separately. Published as open, because it is.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | `DOT-Commercial/README.md` open item 4                                                                                                                                                             |
| 9. Identifiers: exact after normalization                  | 125   | Phones, emails and vehicle IDs are compared exactly once normalized, because a one-character difference is a different identifier. The work is in normalization and in excluding shared placeholders, which Article 2 covers; link it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `matching/tokens.py` (`normalize_phone`, `normalize_text_identifier`)                                                                                                                              |
| 10. Verify against the cluster, not the config file        | 125   | Every table in this article came from `_analyze` against the live index, not from reading `index-settings.json`. The DOT README's encoder table was wrong until 2026-11-08: it showed `SMYTH` unmatched by Beider-Morse, and a live call returns the same code as `SMITH`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | commit `d518cbe`                                                                                                                                                                                   |
| 11. Takeaway                                               | 75    | A field-by-field checklist: names, addresses, identifiers, shared values, each with its matcher and the test to run before trusting it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | —                                                                                                                                                                                                  |

---

## Article 2 — Tuning an entity matcher: fix the candidate search before the weights

- **Audience:** developers working on entity matching and MDM-style projects where they
  are looking for subtle or soft relationships: the ones who already have a matcher
  emitting output and cannot tell whether the threshold or the candidate query is what
  is holding them back.
- **Target length:** 1,900–2,300 words. Rewritten 2026-11-09 (PR #54) at 2,279 prose
  words; the Words column below is each section as written, not a budget. The longest
  of the three, because it carries the most measured before/after pairs.
- **Register:** house style, with the numbers doing the arguing. Almost every section
  here has a measured consequence attached. Lead each with the consequence, then the
  mechanism.
- **Thesis:** Search first, then score the results. The candidate search and the
  ignored values decide what the matcher can find and what counts as evidence; changing
  the weights moved the output least, and a weight cannot say "this one fact settles
  it". The article claims no recall figure: measuring recall needs labeled matches the
  project does not have.
- **Figures to re-measure before the next revision:** the current-sweep figures
  (candidate-limit share, score bands, the 200 reviewable pairs, 499 of 819 top-tier
  pairs with no address on the newer carrier, 525 vehicle-only pairs, agent rarity, the
  6.0% agent overlap) come from `chameleon-candidates-2026.11.09-000001`. Re-measure
  them from whatever sweep the alias names. Figures taken from commits and READMEs are
  dated in the text; keep their dates.

| Section                                                            | Words | Content                                                                                                                                                                                                                                                                                                                                                                  | Backed by                                                                                                  |
| ------------------------------------------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Intro                                                              | 210   | The problem in general terms (a missed match, and why lowering the threshold cannot find it), the carrier and hospital data, the two stages, the thesis, a link to Article 1 for field matching, and the data date.                                                                                                                                                      | —                                                                                                          |
| Terms used in this article                                         | —     | Folded table in the article's vocabulary: listing, candidate limit, seed list, floor, top tier, identity signal, conclusive signal, placeholder, filing agent, and the rest.                                                                                                                                                                                             | `README.md` § Glossary                                                                                     |
| 1. Search first, then score the results                            | 75    | A pair the search never returned cannot reach the scorer. The candidate search, the ignored values and the scorer, drawn in run order by a stacked-row mermaid diagram. Section 13 gives the tuning order.                                                                                                                                                               | `README.md` § Inside `entity-match`                                                                        |
| 2. The seed list sets the ceiling on what the matcher finds        | 300   | The seed list and its two opposite mistakes: adding a common value (filing agents shared 6.0% of the time) floods the search; leaving out a rare one hid a class of match (the vehicle seed reached 257 carriers per 400). The test: would you want to see a pair that shared only this value? The casefold seeding bug, caught before it shipped.                       | `entity-match.json` `seed_signals`; commit `52508c1`; the cluster                                          |
| 3. Fetching 500 candidates instead of 100 found no new top matches | 160   | 100 → 500: 4× the runtime, no new top-tier pairs, +13% shared-vehicle pairs below the top tier. Choosing which 500 (gap-window filter, fixed tie order) took truncated carriers 43,111 → 30,989 and top-tier pairs 581 → 819 (November 2026). 30,989 of 48,540 still hit the limit.                                                                                      | `DOT-Commercial/README.md` § Chameleon detection tuning; the sweep log                                     |
| 4. Ignore filler values and values too many listings share         | 135   | Placeholders and correct-but-shared values, as a table: `GGGG` 158, `UNKNOWN` 79, nines 52, `(000) 000-0000` 664, one email on 755, 200 emails on more than 20 each. Why cleaning cannot fix the second kind.                                                                                                                                                            | `README.md` § Inside `entity-match`; the cluster                                                           |
| 5. List the filler values you know, and let a scan find the rest   | 150   | Declared list plus frequency scan, as a table with the per-field thresholds (5 vehicle, 20 phone/fax/email). The 200-value scan limit sitting at its own ceiling; raised to 2,000 it found 661, and the previous sweep had scored 459 as identity evidence. Without the ignored values the vehicle seed reached 4,181 extra carriers per 400 instead of 257 (94% noise). | `phase_providers/phase_entity_match.py`; commits `b0ed914`, `52508c1`                                      |
| 6. Missing data is not disagreement                                | 120   | No value vs 0.0; average over signals with data. Its cost: 499 of 819 top-tier pairs have no address on the newer carrier.                                                                                                                                                                                                                                               | `matching/signals.py` module docstring; the cluster                                                        |
| 7. Score shared name words with a blend of two formulas            | 130   | Jaccard and containment, explained in the body; the blend; `{QUILBY, FREIGHT}` vs `{QUILBY, FREIGHT, LINES}` → 0.67, 1.0, 0.83.                                                                                                                                                                                                                                          | `matching/tokens.py`; `tests/test_tokens.py`                                                               |
| 8. The first rarity formula rated every filing agent as rare       | 165   | `1 - count/N` vs normalized IDF on current data: 87 agents across 519,139 filings, largest 12.0%, so [0.880, 1.0] vs [0.161, 1.0]. The fault was the formula, not the weight. Exploratory testing caught it.                                                                                                                                                             | commit `02c882d`; the cluster                                                                              |
| 9. The independent-evidence check counted names three times        | 165   | The three guards (evidence sources, identity signal, floor) and where they live. `min_signals` counted three name signals as three sources; it now counts sources (8 signals → 6), still sources with data, not ones that matched (2,581 of 3,627 hospital pairs matched on name alone).                                                                                 | `entity-match.json` `scoring`; `matching/scorer.py`; `README.md` open work items                           |
| 10. Averaging cannot say "this one fact settles it"                | 170   | The vehicle signal at its original 0.08 weight: 1.0 on the signal, about 0.11 averaged, none of 3,000 survived; ~0.46 needed to clear alone. `conclusive` bypasses the floor only. Today 525 vehicle-only pairs score 0.17–0.25 at weight 0.16.                                                                                                                          | `matching/scorer.py`; commit `972df7e` (paraphrase only); the cluster                                      |
| 11. The output is a review queue, not a verdict                    | 110   | 128,907 pairs; 118,697 (92.1%) below 0.50 are noise; 819 top tier; 200 after the review filter. Scores are uncalibrated: a ranking for a person, not a probability.                                                                                                                                                                                                      | the cluster; `DOT-Commercial/precision_metrics.py`                                                         |
| 12. Moving weight off names changed little                         | 170   | Name triple-weighted at 0.45 of 0.94; 0.3483 dropped vs 0.5113 reported. Moving 0.15 onto phone/email and vehicle (August, before the search change): top tier 596 → 611, identical-name review pairs 153 → 145, crash check no gain. Never set beside the November 581 → 819.                                                                                           | `DOT-Commercial/README.md` closed item "Name similarity was effectively triple-weighted"; commit `ac385ce` |
| 13. What to tune, in order                                         | 75    | Numbered: candidate search, ignored values, guards, weights. The diagram shows the run order instead.                                                                                                                                                                                                                                                                    | —                                                                                                          |

---

## Article 3 — Building a general entity matcher: test it on a second dataset

- **Audience:** data and platform engineers who own more than one dataset and are
  deciding whether matching is a per-project script or a shared capability, plus anyone
  evaluating whether this repo is worth pointing at their own data.
- **Target length:** 1,600–1,950 words. Rewritten 2026-11-10 at 1,687 prose words (1,108
  more in tables); the Words column below is each section as written, prose then
  tables, not a budget.
- **Register:** house style, with the honesty load carried by the three failure tables.
  An article claiming a general approach has to show where it did not hold, in the same
  voice and detail as the parts that work. Its one first-person sentence, in the intro,
  is the owner's.
- **Thesis:** Adding a dataset should mean writing configuration, not code. The hospital
  project tested that and it partially worked: testing with its data found four defects
  in shared code the carrier data never triggered, and with them fixed the sweep runs
  from four configuration files. Every
  failure in this article shares one signature: **the run reported success, and the
  data was quietly wrong.**
- **What it dropped.** The 2026-08-30 draft's "where the abstraction leaks" section is
  gone; `PopulationSelector` closed it. Its 36,788 rejected-document figure is retracted,
  and tuning advice that Article 2 owns is now a link.
- **Figures to re-check before the next revision:** the filing agent's 89 values and
  about 6% overlap (the repo's prose still says 7%); the 7,347 same-day union as 3,627
  own plus 3,720 left over (`README.md` once said 4,751); row counts per project, about
  25M across seven trucking datasets and 5.6M across three provider datasets.

| Section                                                                       | Words     | Content                                                                                                                                                                                                                                                                                   | Backed by                                                                                                                              |
| ----------------------------------------------------------------------------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Intro                                                                         | 237       | Entity resolution in general terms, the repo and its two projects, the hospital test and its partial result, the signature, links to Articles 1 and 2, and the data date.                                                                                                                 | —                                                                                                                                      |
| Terms used in this article                                                    | — / 497   | Folded table aligned with Article 2's vocabulary: listing, candidate search, seed list, shut-down carrier / newer carrier, guard, regression test, fingerprint vs filter field, and the rest.                                                                                             | `README.md` § Glossary                                                                                                                 |
| Fraud detection and deduplication ask the same question                       | 131       | Both projects ask whether two listings describe the same business: a shut-down carrier and a newer one, or any two hospitals. One question, one set of code. Configuration picks the starting listings, the date window, the fields and the weights; the search and the scoring are code. | `entity-match.json` `population` and `lifecycle` in both projects; `matching/population.py`                                            |
| Seven phases read each step's configuration from JSON                         | 97 / 92   | Phase table including `validate`. Stacked-row mermaid diagram of the carrier project's setup, load and sweep steps, with the checks that now stop each failure and a yes/no exit at each.                                                                                                 | `README.md` § How it works; `phase_providers/`                                                                                         |
| Two datasets test different halves of the framework                           | 70 / 54   | Table: rows loaded, what each matches, what each exercises. One dataset hides its assumptions inside shared code; a second forces the engine-or-dataset question about every line.                                                                                                        | project READMEs                                                                                                                        |
| Configuration picks signals from a fixed set of Python classes                | 333       | The shipped `vin-overlap` block (type `shared-token`, weight 0.16). The registry; `seed_clauses()` and `token_subfields()`; the rename and the deleted old type; `PopulationSelector`; the closed four-clause menu and the flattening defect it prevents.                                 | `entity-match.json`; `matching/signals.py`; `matching/candidates.py`; `matching/population.py`; commit `a10aec8`                       |
| A field with few distinct values cannot identify a business                   | 167       | The profiler and its fingerprint vs filter split. The filing agent: 89 values across 1.43M rows, about 6% chance overlap, now weight 0.04. The 1% rule. Article 2 owns the rarity formula.                                                                                                | `scripts/profile_dataset.py`; `docs/adding-a-dataset.md`; the cluster                                                                  |
| Five loading failures reported success                                        | 106 / 171 | Table: inferred types, two-digit years, renamed columns, stale enrich index (572,000 → 4,000), rejected bulk requests (91,439 of 2,085,534), each with its fix and regression test. The series thread: Article 1's doubled space, Article 2's vehicle seed.                               | `README.md` § Common data-loading hazards                                                                                              |
| The hospital project exposed four defects the carrier project never triggered | 133 / 171 | Table: field names with a space (116 → 4,751), `fuzzy_scale` (532,529 errors), every pair failed, hard-coded `phy_state` (3,698 pairs; 185 scores raised, none lowered). Article 1's hospital comparison predates the fourth fix.                                                         | `README.md` closed work items; commits `5f3ec64`, `8fb7beb`, `57a0543`                                                                 |
| Three kinds of re-run damaged the earlier run                                 | 116 / 123 | Table: same-day union (7,347 = 3,627 + 3,720), the persistent results index, the reload that lost 2,234 and gained 1,793. The BM25 tie mechanism: 89% of searches over 500, 16 lost pairs at 0.60 or more, ties of 104 to 619. Link to Article 2.                                         | `README.md` hazard 8; `tests/test_dated_index_generations.py`, `tests/test_rerun_guards.py`, `tests/test_candidate_retrieval_order.py` |
| What to decide first on a new dataset                                         | 99        | Numbered: population, profile, seed list / ignored values / guards (Article 2's tuning order), `validate` in every step.                                                                                                                                                                  | `docs/adding-a-dataset.md` § Workflow                                                                                                  |
| Check a run before trusting it                                                | 76        | Ask the cluster what the run did, run a second dataset, re-run the first on unchanged data.                                                                                                                                                                                               | —                                                                                                                                      |

---

## Production notes

1. **Anonymization pass before any article ships.** Commit `972df7e`'s _message_ names
   two real flagged entities; it predates the convention, and rewriting published history
   was ruled out. Paraphrase that commit, never quote it. Every article drawing on git
   history gets checked against [CLAUDE.md](../../CLAUDE.md) § "Never name a real entity
   that the matcher flagged": flagged entities anonymized, ignored entities (filing
   services, insurers, corporate parents) named on purpose.
2. **Point-in-time disclaimer, once per article.** Every count is one extract. Source
   agencies republish on their own schedules, so a threshold tuned against one extract can
   behave differently against the next. A number is evidence that a problem was real and
   roughly how big it was, never a value to assert against.
3. **Do not re-derive what the repo already publishes well.** Link the glossary, the
   hazards list, the encoder table and the tuning table rather than restating them at
   length. The articles' value is the sequence and the reasoning, not the reference data.
4. **Reusable assets already in the repo:** the retrieval/scoring/exclusion mermaid in the
   top-level README, the colour-coded pipeline flowchart in the explainer, the phonetic
   encoder `_analyze` table in the DOT README, the tuning-knobs table, and two runnable
   query samples.
5. **Suggested publication order:** 1 → 2 → 3.
6. **Diagrams.** Every article carries at least one mermaid diagram, rendered to PNG for
   publication (the skill's Prepare for publication mode). Each is drawn after its
   article's rewrite (open item 3).

## Open items

1. **Remove a dropped article's draft in the same change that drops it** from the table
   above, so `docs/blog/` never holds a draft nobody plans to publish. The LLM article's
   draft was removed on 2026-11-08.
2. **Rewrite each article** against its entry above, one branch per article. The
   2026-08-30 drafts are source material, not a base to patch: each is rewritten in
   place, keeping what still holds and checking every figure against the repo and the
   live cluster.
3. **Then add each article's diagram** (production note 6), drawn from the rewritten
   text rather than from this plan, because a rewrite can change which mechanism the
   article turns on.
4. **Finish the project changes the articles depend on before publishing any of
   them.** Both are README open work items:

   - **A typo-tolerant name signal.** Article 1 measures that the phonetic encoders
     fall below a coin flip on one-letter typos and recommends a typo-tolerant
     matcher the project does not have. Add the signal, re-sweep, compare by pair
     id, then update Article 1's "today" sentence and takeaway with the measured
     result.
   - **The hard-coded `phy_state` in `AddressSignal`.** Done: fixed and re-swept
     2026-11-08, and Article 3 carries it as the fourth defect.

   Rewrite Articles 2 and 3 after both changes, so they describe the finished
   matcher.

## Source index

| Short name       | Path                                                                     |
| ---------------- | ------------------------------------------------------------------------ |
| top-level README | `README.md`                                                              |
| DOT README       | `DOT-Commercial/README.md`                                               |
| CMS README       | `CMS-Providers/README.md`                                                |
| onboarding guide | `docs/adding-a-dataset.md`                                               |
| design spec      | `docs/superpowers/specs/2026-07-30-chameleon-carrier-matching-design.md` |
| explainer        | `DOT-Commercial/docs/chameleon-pipeline-explainer.md`                    |
| conventions      | `CLAUDE.md`                                                              |
| analyzer config  | `DOT-Commercial/configuration/carriers/index-settings.json`              |
| mapping config   | `DOT-Commercial/configuration/carriers/index-mappings.json`              |
| matcher config   | `DOT-Commercial/configuration/chameleon-detection/entity-match.json`     |
