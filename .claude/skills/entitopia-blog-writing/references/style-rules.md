# Style rules for the entitopia blog series

The full rules behind the checklist in `SKILL.md`. Each rule carries its reason,
because most rules have edge cases and the reason decides them. The series plan,
audiences and section budgets live in
`docs/blog/2026-08-30-blog-series-outline.md`, not in this file.

## Contents

1. Naming real entities
2. Verifying claims
3. Point-in-time figures
4. Terminology
5. Describing mechanisms
6. Openings
7. Headings and titles
8. Terms tables and definitions
9. Register
10. Diagrams and code blocks
11. Cross-article references
12. Attribution
13. Presentation
14. Section order and article shape
15. Altitude: an article is not the README

## 1. Naming real entities

The series draws its examples from public data about real companies, and the
matcher scores those companies for suspected fraud. A score is an analysis, not
a finding: the thresholds are uncalibrated, the sweep truncates, and a high
score routinely reflects a shared filing agent, a corporate parent, or a
placeholder value. A blog post that names a carrier beside "chameleon"
publishes an unverified accusation about an identifiable business, outside the
repo, where no later commit can amend it. CLAUDE.md states the rule; this is
its blog form.

- **Anonymize anything a sweep flagged.** Company names, DOT numbers, street
  addresses, phone numbers and emails from a `chameleon-candidates` pair become
  placeholders in the style the `anonymization-check` skill sets out:
  `<CARRIER-A>`, `<DOT-1>`, `<STREET-A>`, `<PHONE-A>`, `<EMAIL-A>`. Keep what made the example worth telling: "identical legal name,
  same street, re-registered one day after the predecessor's shutdown".
- **Name what is excluded, and say that it is excluded.** A filing service, an
  insurer, or a corporate parent whose email sits on hundreds of carriers is a
  source of false positives being filtered out, and the reader maintaining
  their own ignore list needs to recognize the shape. The sentence has to
  present it that way. The same value described as one suspect is an
  accusation and gets a placeholder.
- **Junk values are free.** `GGGG`, `(000) 000-0000` and `UNKNOWN` are what an
  ignore list has to name to work.
- **Hospital duplicates are not accusations.** A `hospital-duplicates` pair says
  two CMS records may describe one facility. Those may be named, until the
  surrounding text implies wrongdoing.
- **Aggregates identify no one.** "An email address shared by 206 carriers"
  describes a distribution.
- **If an example stops working once the name is gone, the name was doing the
  arguing.** Rewrite the sentence around the pattern rather than reaching for a
  thinner placeholder.
- **Git history is a source with a trap in it.** Commit `972df7e`'s message
  names two flagged entities and predates the rule. Paraphrase it.
- **The scanner gathers evidence; the ruling is yours.** Run the scan in the
  Verification block and rule on each hit as the `anonymization-check` skill
  says, then read the prose for what a pattern cannot see: a bare two-word
  trade name, a person's name, a city and street without a house number.

## 2. Verifying claims

A reader cannot check a sentence against the repo, so every sentence about a
mechanism, a default, a config value, or a measurement is a claim the article
vouches for.

- **Check mechanisms and constraints in the source before writing them.** Read
  the function, the config, or the phase. Citing what the code enforces is
  stronger than asserting it, and reading it is how a wrong default gets caught
  before it is published.
- **A precise number is not the same as a true one.** The top-level README
  records that "36,788 of 5,647,567 documents dropped" was carried for weeks
  across three files, because it was specific enough to sound measured and
  nobody checked it against a mapping, and was then retracted. A figure is
  verified when its source and how it was taken are known, not when it has
  digits.
- **The outline is a plan, not a source.** Its Backed by column says where to
  look. A draft that copies a figure from the outline's Content column has
  copied a claim about the repo at the time the outline was written.
- **Clarifying a claim must not strengthen it.** A rewrite for plainness states
  the same claim. When cutting or rewording a sentence, check whether it was
  the paragraph's only hedge: removing a caveat can silently promote an
  inferred mechanism to an established one.
- **Scores are uncalibrated.** Nothing in this project has been fitted against
  known outcomes, so a 0.9 does not mean 90% likely. No sentence, heading or
  caption may let a reader take a score as a probability or a pair as a
  confirmed match.
- **A definition must fit every row of the table it labels.** Test each row. A
  necessary condition restated as a sufficient one breaks the row it does not
  fit.
- **Keep measured and inferred apart, in the text and when answering questions
  about it.** Say which part was counted, which part is an explanation of the
  count, and what was never run. The wording carries the same split:
  "appears to", "on the evidence of one sweep".
- **When an article compares two configurations, enumerate what differs.** This
  is the one check a sentence-by-sentence pass cannot make, because the claim is
  an absence: no sentence in the draft points at the thing nobody mentioned.
  For two sweeps, list the matcher config, the analyzer settings, the source
  extract, the index generation each read, and `max_candidates`. The analysis
  fingerprint covers tokenization config only, so two sweeps with matching
  fingerprints can still differ in weights, pipelines and field types. Each
  difference is either named in the article or is an unmeasured confound that
  earns a sentence. The `evaluate-sweep` skill compares two sweeps the way the
  repo does; use its comparison rather than one improvised for the article.
- **A figure is not only a number.** A claim about what a run logged, what the
  cluster returned, or which index an alias named traces to its source the same
  way a count does, and those claims are the ones a check by numeric token
  misses.
- **Report a confirmed narrow hypothesis as confirmed, and the net result
  separately.** Raising `max_candidates` from 100 to 500 found no new top-tier
  pairs and added mid-tier recall. Neither makes the change "worthless" or "a
  win", and a heading that implies either misreports both.

## 3. Point-in-time figures

Every count in this series is one extract. FMCSA and CMS republish on their own
schedules, the repo reloads its data, and the drafts were written against an
extract that has since been replaced. The README's "A note on the numbers in
this document" is the house position.

- **Attribute each count to its extract.** Once per article, a sentence names
  the extract date the counts come from and says they are evidence of
  magnitude, not values to expect on the reader's download.
- **A count from the cluster is checked against the cluster.** When the draft
  and the current data disagree because the extract changed, either keep the
  old figure with its date or take the new one with its date. Never mix
  extracts in one table without saying which row is which.
- **Never promote a point-in-time count into an invariant**, and never imply a
  threshold tuned here will hold on the reader's data.
- **When a measurement disagrees with the repo's prose, trust the
  measurement** and tell the user the README needs updating as well. The blog
  is downstream of the repo, not a second copy of it.
- **Quote the newest run; the README keeps the older ones.** The DOT sweep has
  been re-run on more than one extract and after weight and retrieval changes.
  An article quotes the newest run of each measurement and links the README
  for the history. An older run stays in the article only where the change
  between runs is the finding, as a before-and-after with both dated.
- **One unit per quantity, and `n of m` for fractions.** Write a count the same
  way every time it appears (5,647,567 or "about 5.6 million", not both), and
  give a fraction as "91,439 of 2,085,534" with its percentage in parentheses
  if the percentage carries the point.

## 4. Terminology

Most rewriting is a term doing two jobs at once, or two terms doing one job.

- **Name the thing rather than pointing at it.** The author resolves every
  referent instantly, because the code is in front of them; the reader has only
  the sentence. Four shapes recur:

  - A pronoun or demonstrative standing for a noun more than a clause away.
    "That setting" names no setting; the config calls it `max_candidates`.
  - A category noun with its domain left off, where the article spans more than
    one. "The field" in a paragraph that has a CSV column, an Elasticsearch
    field and a multi-field subfield.
  - A figure whose antecedent lives only in a heading, after the sentence that
    repeated it was cut.
  - A positional reference to something that has a name. "The filter described
    above" is `collapse_whitespace`.

  This cannot be grepped, because pronouns are ordinary English and only some
  are unresolvable. It is found by reading, and best by someone who cannot see
  the repository.

- **Where two of a thing exist, never name one of them bare.** This series has
  two stages (retrieval and scoring), two phonetic encoders (double metaphone
  and Beider-Morse), two street subfields, two projects, and usually two
  sweeps being compared. A reader just told there are two has no way to pick,
  and the writer cannot see it because they know which one they meant.

  Before a register pass, list what the article has two or more of. Each of
  those nouns then gets a qualifier at every use. Two exceptions, both narrow:
  a sentence defining the category itself stays bare, and a pronoun in the
  sentence immediately after an unambiguous mention is fine. Count the senses,
  not the words: "seed" as a verb for retrieval and "seed" as the seed query's
  clause are two senses in one paragraph.

- **Never write a bare "model".** Say "embedding model", "language model" or
  "LLM", or name the thing ("the scorer", "the weights"). A reader of a
  technical post in this decade reads a bare "model" as an LLM, so "choose a
  different model" in a paragraph about vector search sends them to the wrong
  technology.
- **One name per concept, taken from the code, config or Glossary.** Blocking,
  seeding and retrieval name one stage; pick the repo's term ("retrieval", with
  "classically called blocking" once) and keep it. The top-level README's
  Glossary is the vocabulary.
- **Qualify a vendor- or agency-specific mechanism.** "An Elasticsearch enrich
  policy", "an FMCSA census record", "a CMS facility ID". Use the qualified form
  at the first mention in each `##` section, then the short form.
- **Name the actor precisely.** "The entity-match phase", "the ingest
  pipeline", "the profiler", "Elasticsearch", in a series that also has a
  loader, a driver and a review page. "The system" names nothing.
- **Say what kind of thing a term is.** An analyzer, a multi-field, a phase, a
  step, a signal, a guard. A reader who cannot tell whether something lives in
  the mapping, the pipeline, or the Python scorer cannot follow a claim about it.
- **Restate with the defined vocabulary.** A guard defined as `conclusive` is
  not called "the override" two paragraphs later.
- **Keep `None` and `0.0` apart in prose too.** "Not evaluable" and "evaluated,
  no similarity" are the distinction the scorer rests on; "the signal scored
  nothing" flattens it.

## 5. Describing mechanisms

- **Give direction and data.** Say what the query carries, what comes back,
  which field holds the result, and in what form. "The matcher finds
  candidates" lets a reader picture anything; "the seed query asks
  Elasticsearch for records sharing a phonetic name token or an exact phone,
  and returns at most `max_candidates` of them by relevance" can be checked.
- **Say where a thing runs.** Analysis runs in Elasticsearch at index time;
  scoring runs in Python after retrieval; an ingest pipeline runs in the
  cluster during the bulk load. Most confusion in these drafts is a reader
  placing a step on the wrong side of that line.
- **Say why a baseline or comparison was chosen, and what rules out the
  alternative.**
- **Say whether alternatives are exclusive, and where each one applies.** A
  diagram with two branches reads as a fallback unless the text says only one
  is taken.

## 6. Openings

**Every article is standalone.** A reader arrives from a search result or a
single link, not from the first article, and the articles are read in no fixed order.
Nothing may depend on another article having been read: each one says what the
project does, what data it reads, and what it produces, and expands a term the
first time that article uses it.

**Open with the problem in general terms, then the domain.** A reader doing
customer MDM should recognize their own problem in the first paragraph, before
trucking or hospitals appear (the outline's lead rule).

**At least one intro paragraph sits between the `#` title and the first `##`
heading.** It is the background: two records, no shared key, the public data
this repo reads, and the question the article answers. Filing that paragraph
under the first `##` puts general background beneath a specific heading and
leaves the title standing alone above nothing. Pose the question as a question,
or let the finding answer it; do not announce it with "This article asks".

**The setup the finding depends on belongs in the intro**, even ahead of a terms
table. Two or three sentences is enough; the finding follows in the next
paragraph.

## 7. Headings and titles

- **A heading states a finding, not a verdict, and gets re-derived when the
  section beneath it changes.**
- **One finding per heading.** Two findings joined by "and" become two sections,
  or the second clause moves into the section's first sentence.
- **State the finding at the altitude a stranger can reuse.** "Seeding sets the
  recall ceiling" is reusable; "`max_candidates` 500 found 13% more mid-tier
  pairs" is the instance, and belongs in the section's first sentence. The tell
  is sibling comparison: a heading that names a config key while its siblings
  read generic is an instance wearing a heading's clothes. An incident section
  is the exception, because the incident is the finding.
- **A heading is read without the section's definitions.** Readers meet it while
  skimming, before any term the section defines. If a heading's key noun
  requires the body to resolve, say the plain thing the noun stands for.
- **No clause that reads as an extra entity.** "Retrieval and scoring read the
  same values, against the ignore list" reads as three things compared, though
  the ignore list is what both stages consult. "Retrieval and scoring both
  consult the ignore list" does not.
- **Concrete words, not constructions that read as machine-written.** Two
  patterns do: a gerund subject acting on an abstract noun, and a personified
  abstraction ("The schema hides what it does not remove"). Name the thing
  instead. The same test applies to sentences.
- **A heading is active, and names the thing that acts.** A passive participle
  usually marks the spot: _used_, _built_, _measured_, _taken_. A
  `## Terms used in this article` heading is the standing exception, because it
  labels a table rather than stating a finding.
- **A heading does not repeat its section's first sentence.** When a heading is
  rewritten to state the finding, rewrite the first sentence so it adds
  information.
- **Titles get a paraphrase test.** Write down how a hurried reader would
  restate the title, then fix whatever the title lets them believe that the
  article does not support. "The knobs that actually moved recall" invites "we
  measured recall", which needs labelled outcomes this project does not have;
  check what the article measured before keeping it. Dramatic verbs fail the
  register as well.
- **A title change updates the outline's article table.** Grep the repo for the
  old title.

## 8. Terms tables and definitions

- **Every article carries a "Terms used in this article" table** after the
  opening, before the first finding. Table rows do not count against the length cap, so the table is also
  cheap. Draw definitions from the README Glossary and link it.
- **Fold the table, not the heading.** The table sits in a `<details>` block
  whose `<summary>` reads `Show the terms`, and the heading stays outside the
  block, so the section is still in the outline while the rows take no space
  until the reader opens them. Leave a blank line after `<summary>` and before
  `</details>`, or many Markdown renderers emit the rows as text instead of a
  table:

  ```markdown
  ## Terms used in this article

  <details>
  <summary>Show the terms</summary>

  | Term | What it means here |
  | ---- | ------------------ |

  </details>
  ```

- **Write the table for a technical reader who does not know Elasticsearch.**
  Every Elasticsearch term the article uses gets a row: index, mapping,
  analyzer, tokenizer, filter, subfield, `keyword`, `fuzziness`, `_analyze`, and
  any API or setting the prose names. So does every term from search or
  matching theory (inverse document frequency, AUC, recall). General
  programming terms (query, field, string) do not. Warn where an Elasticsearch
  meaning differs from the everyday one: an Elasticsearch index is closer to a
  database table than to a database index.
- **Define each term once.** A term defined in the terms table is not defined
  again in the body.
- **After moving or deleting a section, check every "next section", "above", and
  "below".**

## 9. Register

The outline's Register rules, in this skill's form: state results plainly, replace
superlatives with the figure they stand for, reserve bold for a section's
load-bearing claim and for figures, hedge inferred mechanisms, and end on the
last factual sentence.

**Name the failed alternative.** "The obvious approach did not work" is worthless
without naming it and saying how it failed. Most of the value in this series is
in the failures, so they get specifics: the formula, the pattern, the measured
consequence. Report negative results as plainly as wins; they must not read as
apologies.

**Write a technical blog post, not a research paper.** One idea per sentence at
the lengths below. A paper hides the actor behind a passive ("the documents
were dropped"); a post names it ("Elasticsearch rejected the whole document").
Do not name it as a person.

**No passive voice.** Every clause names who or what acts. "Beider-Morse is
pinned to English" becomes "The project pins Beider-Morse to English"; "the
run can be repeated" becomes "anyone can repeat the run". A reduced passive
counts too: "set to 6, chosen because" hides who chose. Headings follow the
same rule: "Words ... must be stopped" becomes "Stop the words ...". When the
actor is unknown or does not matter, rebuild the sentence around the thing
that changed ("the share fell from 59% to 0%"), not around a passive verb. A
be-verb before an adjective that ends in -ed ("may be related") is not a
passive, but the check below flags it; reword it rather than argue with the
check.

**Open every sentence on its subject.** No sentence starts with a conjunction
(And, But, Or, So, Yet, Also) or a preposition (For, On, In, At, Against,
Across, With, Under, After, Before, By, From, To, Of). "And it finds likely
matches" becomes "It also finds likely matches"; "On 600 hospital names, the
script scored" becomes "The script scored ... on 600 hospital names"; "Against
random pairs, all six score" becomes "All six score ... against random pairs".
A leading phrase makes the reader hold a condition before they know what it
qualifies, and a leading "And" reads as speech, not prose.

**Lead a paragraph with its subject, not a subordinate clause.** A paragraph that
opens on "Because", "Since", "While", "Although", "When" or "Given" makes the
reader carry a condition before they know what it qualifies, and the main
clause that follows almost always arrives with a pronoun doing the work. The
same applies mid-paragraph when the leading clause runs past about eight words.
The rule covers every clause that stands in front of the subject:

- **A purpose infinitive.** "To catch a renamed column, the profiler compares
  ..." becomes "The profiler compares ..., which catches a renamed column." Do
  not fix it with a passive, which trades one demoted actor for another.
- **A conditional.** "If the policy is stale, enrichment drops to ..." becomes
  "A stale policy drops enrichment to ...".
- **A noun clause as the subject.** "Where the value sits decides ..." becomes
  "The position of the value decides ...".
- **The author's own procedure as a gerund subject.** A gerund naming the
  system's behavior is fine ("Seeding on a common value floods the candidate
  set"). One naming what the author had to do is procedure, and the measured
  thing belongs in the subject instead.

**Put the actor in the subject, not an abstract noun in front of a copula.**
"Retrieval is where recall is lost" and "X is why the count drops" are passives
wearing an active verb. Rewrite with the concrete actor and a plain verb: "The
seed query loses the pair before scoring sees it". A "there is", "it is ...
that" or "X is where / why / what" opening fails the same test.

**Sentence length: aim for 14 words, split anything past 25.** The
Flesch-Kincaid reading tests treat 20 words as the upper end of comfortable, so
a mean near 14 leaves room for the occasional longer sentence. A sentence past
25 words is nearly always two ideas wanting a period between them, and the
usual cause is a definition wedged between a subject and its verb.

**At most two clauses per sentence.** One main clause, plus at most one
subordinate or joined clause. A third clause, joined by a comma and "and", a
semicolon, "which", "while" or "because", is the second idea the 25-word
ceiling exists to catch, and it hides inside short sentences too: "X kept two
spaces where Y had one, and under a keyword tokenizer those are different
tokens" is 21 words and three clauses. Split it at the second join. The check
in `SKILL.md` counts joins, so it flags some noun clauses that are not
separate ideas ("measures which forms to use"); split those anyway when it
reads as easily.

One kind of sentence earns the length: a short list, where the words come from
items rather than clauses and no item needs explaining. Outside that, treat 25
as a split point rather than a judgment call.

**A list whose items want explaining belongs in a table or in bullets.** Inside
a sentence each item gets a comma and nothing else, so a reader who needs to
know what an item means has nowhere to look. Keep the inline form only where the
items are bare figures or names that speak for themselves. Between a table and
bullets, prefer the table (section 13): bullets are for items that differ in
shape.

Splitting adds a word or three each time, so this rule and the length target
pull against each other. The ceiling wins. Take the length out of content, by
dropping a finding or deferring detail to the README, never by packing two
ideas back into one sentence to save three words.

**No first-person singular.** No "I", "me", or "my" anywhere in an article. An
article drafted with an agent's help cannot say "I measured" without the reader
wondering whether the author or the model is speaking. "We" and "our" are
allowed, sparingly, for project history. Prefer the thing that acted over
either pronoun: the profiler measured the column, the sweep emitted the pair,
the loader logged the failure. Imperatives are fine ("run `_analyze`"), and
"you" is fine where the reader is the one acting.

**No em dashes as punctuation.** A dash setting off a clause reads to many
readers as a machine-authored tell. Restructure the sentence rather than
dropping a comma into the same slot: a paired aside becomes commas or a
sentence of its own, and a trailing dash becomes a period or a colon depending
on whether what follows restates or expands. A definition wedged between a
subject and its verb moves out to its own sentence. Editing passes reintroduce
dashes easily, so count them after every pass. The exception is a table cell,
where a dash may stand in for a value that does not exist; prefer `n/a` where it
reads correctly.

**No amplifiers and no closing flourish.** Cut "actually", "exactly", "just",
"even", "specifically", "materially", "without exception". Cut a sentence built
for effect rather than information, and end the section on its last factual
sentence. Do not repeat a sentence verbatim in two sections. No paragraph about
the future of entity resolution.

Three paper habits to convert on sight:

- **The agentless passive.** Rewrite with the phase, the query, the pipeline,
  the profiler, or Elasticsearch as the subject.
- **Announcing the article's question.** "This article asks" becomes the
  question itself, or the finding.
- **The defensive pair.** "checked rather than assumed", "measured rather than
  inferred". Keep the hedge, drop the contrast: say what was checked. A pair
  that carries real information, such as "uncalibrated confidence, not
  probability", may stay.

Habits to cut on sight:

- **Rebutting an objection no reader raised.** "not a framing choice", "three
  parts, not two". Assert the thing; the contrast is imaginary.
- **Narrating the article's own rhetoric.** "The obvious doubt is worth
  answering here". Answer the doubt; do not announce that you are about to.
- **Announcing the setup instead of giving it.** "The frame, briefly". Open the
  paragraph with the setup itself.
- **An ordinal implying an enumeration the text never made.** "a fourth kind".
  Name the thing instead.

## 10. Diagrams and code blocks

- **Every article carries at least one diagram,** drawn as a mermaid fence in
  the draft. It shows the article's central mechanism: the stages a record
  passes through, or the decision a reader has to make. A reader skimming the
  page should be able to tell what the article is about from the diagram
  alone. The fence is the editable source; the published post shows a PNG
  rendered from it (SKILL.md, Prepare for publication).
- **A diagram says what the prose says, in the prose's names.** Label a stage
  "retrieval", not with a function name, and do not label a phase with a
  command-line flag.
- **Draw exclusive alternatives as a branch.** Use a decision node with one
  labelled edge per alternative, and say in the node or the lead-in that only
  one is taken. A box with two arrows out reads as a pipeline or a fallback.
- **Every arrow means "then".** A reader follows arrows as routes. A list or
  setting that several steps consult is not a route: show it as the step that
  builds it, then name it inside each step that uses it ("Drop ignored
  values", "skipping ignored values"). An arrow from a list into two stages
  labelled "skipped by the search" and "skipped by the scorer" read as two
  alternative paths, and a reader asked how a pair could be reported down one
  without the other.
- **Every decision node has an exit for each answer.** A diamond with one way
  out hides what happens to the rejects. Give the "no" edge its own end, such
  as "Pair dropped".
- **Draw one order, and draw it from the code.** A diagram shows the order the
  system runs in. Numbering its boxes in another order, such as the order to
  tune them, put a "2" above a "1" and read as a mistake; give the other order
  its own list in the prose. Check the run order against the code, not the
  prose: Article 2's first diagram put all three guards after the weighted
  average, and `matching/scorer.py` runs two of them before it.
- **Show each stage's input.** A stage whose only incoming arrow is a filter
  reads as fed by the filter. Article 2's search had no input until the
  diagram showed where the shut-down carriers came from.
- **Wrap a wide diagram into stacked rows.** Use `flowchart TB` with each row a
  `subgraph` carrying `direction LR`, and draw arrows only between subgraphs.
  Mermaid ignores a subgraph's direction when an edge joins a node inside it
  to a node outside it, and the rows collapse into one column. A serpentine
  layout (`direction RL` on alternate rows) renders, but a right-to-left row
  reads backwards, so prefer every row left to right.
- **Reuse the repo's diagrams before drawing new ones.** The top-level README
  carries the retrieval, scoring and exclusion mermaid and the phase diagram;
  the explainer carries the pipeline flowchart (outline production note 4). A
  copied diagram is updated with its original.
- **Show the fragment that carries the point**, not the whole file: the one
  multi-field mapping, the one filter definition, the one `_analyze` call and
  its tokens. At most one code or config block per two sections, except where
  the outline budgets more.
- **Every config fragment is copied from `main`**, not retyped, and its file is
  linked beside it. A retyped fragment drifts from the repo the first time the
  config changes.
- **Image and chart placeholders are HTML comments** describing what the visual
  should show, including any data it needs.

## 11. Cross-article references

The outline decides _what_ defers. How a deferral is written is a separate
matter.

- **A deferral is a sentence with a verb, not an equation.** "which values to
  ignore is the tuning article" equates a question with a document. Write "The
  tuning article measures which shared values to ignore."
- **Verify a claim about a sibling article against that article.** A pointer is a
  factual claim.
- **Do not close a section or an article with a bare deferral paragraph.** End on
  the last factual sentence.

## 12. Attribution

Three placements per article, because a reader who stops early should still be
able to reach the source:

1. **First screen.** Name and link the repo,
   `https://github.com/freemansoft/entitopia`.
2. **Beside the artifact under discussion.** A reproduced table or config
   fragment needs its source link next to it, not in the closing paragraph.
3. **Close, in a `## Source` section.** Every article ends with a `## Source`
   section after its takeaway. It links the repository,
   `https://github.com/freemansoft/entitopia`, and the README or doc the
   figures came from, as absolute URLs.

**All links are absolute GitHub URLs.** Repo-relative paths break the moment an
article leaves the repository. This applies to inline artifact names too:
`matching/signals.py` and `DOT-Commercial/configuration/carriers/index-mappings.json`
are links, not bare filenames.

**Two link targets, and the distinction applies only after publication.** While
the articles are drafts, links track `/blob/main/` so the drafts follow the repo
as it changes. At publication, re-point links to READMEs and docs that carry
figures to the commit the figures were read at, every article to the same
commit, so a quoted figure still resolves after the README moves on. Links to
code and config keep tracking `/blob/main/`, because a reader following them
wants the current file. A commit hash quoted in prose links to
`/commit/<hash>`.

## 13. Presentation

- **Length: the outline sets each article's target; justify past 2,000, hard cap
  3,000.** These limits apply to the count without tables, which excludes
  fenced code and diagram blocks, table rows, link URLs, and the closing
  `## Source` section. Measure with the
  command in `SKILL.md`, not `wc -w` on the raw file, and report both counts,
  with and without tables. An
  article past 2,000 needs its reason recorded in its outline entry. One that
  outgrows the cap gets trimmed or split, with the outline updated if split.
- **Watch the count with tables too, with a soft ceiling near 3,500.** The count
  without tables understates what the reader scrolls. Prose can
  be trimmed to target while the article gets longer on screen, which is why
  both numbers are worth reading.
- **Use a table wherever one works, instead of bullets or a prose list.** A
  table works when the items share attributes: each has a name and the same
  two or more facts, such as a figure, a source, a verdict or a fix. One row per
  item, one column per attribute, and the reader compares down a column instead
  of re-reading each bullet. It also works anywhere a section compares more
  than two things or enumerates measured values. Keep bullets for items that
  differ in shape, or where each needs a sentence of its own that no column
  could hold; keep numbered lists for steps in order.
- **A table section runs intro, table, then commentary.** The intro is one or two
  sentences saying what the table holds and what to look for in it. After the
  table, the rows that carry a finding get a paragraph each. A row the reader
  will stop on, an outlier or a reversed sign, never stands without an account.
- **Keep every table, including one the README also carries.** The primary table
  for a claim belongs in the article, beside the paragraph it supports. What
  defers to the README is hard prose detail, not the tables that carry the
  finding. A figure that appears in both places has to be updated in both.
- **The article carries the story, the README carries the depth.** The article
  states what was measured, how it was measured, and why that answers the
  question, and then points. Method is not depth: cutting how a measurement was
  made leaves a reader who cannot judge the figures that follow.
- **Converting prose into a table costs nothing against the cap,** because the
  word count excludes table rows.
- **A wide source table needs fitting to blog width.** Split it into mini-tables
  placed with the prose that discusses them, or shorten the widest column and
  move its content into prose. Do not trim the column the figures live in.
- **End on a takeaway the reader can act on.** Each article closes on a checklist
  or an ordered list against their own data (the outline's three-part spine),
  and the list ends on its last item, not a summary sentence.

## 14. Section order and article shape

A reader meets the sections in the order they are written, so the order is part
of the argument.

- **One thread per section.** A section carrying two findings joined by "because"
  or "and" is two sections, or one section and a sentence that belongs
  elsewhere. A section that carries none is a transition and becomes a sentence
  in its neighbour.
- **The mechanism comes before the verdict.** A section that opens on "It was
  not" before the cause is known asks the reader to accept a conclusion they
  have no way to check yet.
- **Tell an incident once.** Where it appears in the intro and again in the first
  section, the intro keeps the setup and the section keeps the account. Across
  the series, an incident told in two articles serves a different point in
  each, or one of them links.
- **Group the sections so the close echoes the body.** A takeaway list orders
  its items the way the body ordered the findings behind them.
- **Give each table its configuration** when the article spans more than one:
  project, extract, and the config that produced it.
- **A definition sits where the reader first needs it.**
- **A section that outgrows its budget has usually split its own subject.** The
  outline's rule: 30% over means two sections or the wrong article; far under
  means the evidence is thinner than the outline implied, so check the source
  before padding.

When reviewing rather than drafting, structure findings are worth as much as
wording findings and are easier to leave vague. Name the order you would use and
what each section would then hold.

## 15. Altitude: an article is not the README

An article carries the highlights, the decision points, and the controls: what
a reader needs to know, why it matters, and what was held constant so they can
trust it. The READMEs, the design spec and the explainer carry everything else,
and the article links to them (outline production note 3).

- **A decision table is a highlight; the forensics behind it are not.** Keep the
  table that separates two causes or two configurations. Move per-run counts,
  index generation names, and log lines behind a link. The test: if a figure
  changes what the reader does, it stays; if it only proves the author did the
  work, it links.
- **State the control, not the procedure.** "The analyzer settings and the
  extract stayed constant" earns its words; the order the steps ran in does
  not, unless order is the variable.
- **The nature and depth of the checks is a highlight, not forensics.** A reader
  deciding whether to trust a sweep's output needs to know what each guard
  checks and what it cannot see, more than any single figure.
- **Every detail cut this way needs the link that absorbs it.** A cut without a
  link orphans the claim; "the README has the details" with no anchor is a
  defect.
