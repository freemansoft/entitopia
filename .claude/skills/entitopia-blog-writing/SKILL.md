---
name: entitopia-blog-writing
description: >
  Use when working on the entitopia blog series in docs/blog/: reviewing an
  article for style, substance, complexity or wording; checking whether a
  draft is still accurate against the current repo or cluster; tightening a
  title, heading or sentence; answering what a passage means or whether a
  reader would follow it; making a diagram clearer; asking how long an article
  is; carrying a changed README figure or hazard into the drafts; starting a
  new article; updating the series outline; or preparing an article for
  publication. Use it even when the user quotes one sentence from a draft and
  asks a question about it.
---

# Writing and reviewing the entitopia blog series

The articles in `docs/blog/` explain what this repository learned about entity
resolution with Elasticsearch: the schema, the matcher's tuning, the
configuration-driven framework, and working with an LLM. The reader is a
developer outside this repo who arrived from a search result and has never
heard of FMCSA or chameleon carriers. Two consequences drive most of what
follows: every article has to stand alone, and every sentence is a claim the
reader will take at face value because they cannot check it against the code.

Four sources govern the work, and they do not overlap:

| Source                                                        | Owns                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The repo and the live cluster                                 | Every figure and every mechanism. The READMEs, the design spec, the code, the commit history, and `http://localhost:9200`. When a draft and the repo disagree, the repo wins; when the repo's prose and a fresh measurement disagree, the measurement wins and the repo gets updated. |
| `docs/blog/2026-08-30-blog-series-outline.md`                 | The series plan: the articles, each one's audience and thesis, what each owns, the section budgets, the production notes, the open items, and the source index.                                                                                                                       |
| CLAUDE.md "Never name a real entity that the matcher flagged" | Which names may appear. The `anonymization-check` skill runs it.                                                                                                                                                                                                                      |
| This skill                                                    | How to write, review, verify, and publish. The full rules, with reasons and examples, are in `references/style-rules.md`.                                                                                                                                                             |

The outline's **House style** section predates this skill. It has no rule
against em dashes (its own prose uses them), it allows a table only for a
measured comparison, and it asks for numbered sections that none of the drafts
use. Where the two disagree, this skill wins, because it carries the reasons.
A human reading the outline cannot see that precedence, so the House style
section should be cut to a pointer here.

## Setup for any pass

1. **Branch first.** Create `docs/blog-<article>-<pass>` (for example
   `docs/blog-02-accuracy-pass`) from `main` before the first edit. Never
   commit or push until the user has **read the changed prose itself** and
   said to proceed. A `git diff` is the wrong artifact here and
   `git diff --stat` is worse: a diff of a rewritten section is unreadable, and
   a line count says nothing about whether the writing is any good. Print the
   new text. This holds even when the request sounds like it covers the whole
   job, including an instruction like "commit these and then write the
   articles".
2. **Read the whole article** and its section of the series outline: audience,
   thesis, the section table and its **Backed by** column, and the production
   notes.
3. **Calibrate on the most recently reworked draft.** `git log -- docs/blog/`
   shows which articles have had a register or accuracy pass. Match those
   rather than an older draft. Until one has, calibrate on the style rules
   alone: the 2026-08-30 drafts predate this skill and carry em dashes, no
   links, and sentence means near 18.
4. **Read `references/style-rules.md`.** It is the rulebook. The checklist at
   the end of this file is only an index into it.
5. **Record the starting prose word count** with the command under
   Verification, so any length claim has a before and an after.

## Modes

### Review

Triggered by requests like "examine this article for style, substance,
complexity and wording". Produce findings; edit only if asked.

**Read the draft twice, and let something else read it once.** The first pass
hunts defects against the checklist. The second reads it straight through as a
reader who arrived from a search result, and that pass is where the problems
no checklist item names turn up: an incident told three times before its cause
arrives, a term doing two jobs, three extracts that no table attributes. A
review assembled only from the checklist finds violations and misses shape.

- **Open with three or four lines of orientation.** What the article does well,
  and the few things keeping it from reading like the most recently reworked
  draft. Then the ranked findings.
- **Rank by harm.** A name the matcher flagged comes first, because the repo is
  public and a leak cannot be fully withdrawn. Then claims the repo or cluster
  does not support, including figures that were true of an older extract and
  are stated as current. Then contradictions (a definition that does not fit
  its table, a cross-reference that no longer resolves), then register
  violations, then structure and wording.
- **Make each finding actionable.** Cite the line, quote the text, say how it
  misleads a reader, and propose the fix.
- **On an article that compares two configurations, diff the arms before
  reading the prose.** Two sweeps, two analyzers, `max_candidates` 100 against
  500, a weight before and after: open what produced each figure and list every
  input that differs, not just the one the article is about. See "When an
  article compares two configurations" in the style rules.
- **Propose the shape, not only the faults.** Where the order is wrong, give the
  section order you would use, one line per section saying what it holds and
  what moves into it. See Section order and article shape in the style rules.
- **Count a recurring habit instead of fixing one instance of it.** When a tic
  appears more than twice, say how many times and list the lines.
- **Rewrite the worst two or three passages in full.** The densest paragraph and
  any headings you would change earn finished prose, because a described fix for
  a tangled paragraph is not checkable. Everything else stays a described fix.
- **Forecast the length** when the article is over its target. Say what the
  proposed changes would leave it at, and which cost nothing because they move
  prose into table rows.
- **When reviewing edits made earlier in the session, separate the errors those
  edits introduced from pre-existing ones**, and say which are which.
- **Show what was checked.** Paste the output of the Verification commands.
- **End by asking which findings to apply.** Flag any fix that depends on a
  source outside the repo, such as Elastic's documentation or an agency's data
  dictionary, before applying it.

### Check a draft is still accurate

Triggered by "are these still valid", "has the repo moved under this draft",
or any review of a draft older than the code it describes. The drafts were
written against one commit and one data extract; the repo keeps moving.

- **Find the draft's baseline.** `git log --format='%h %ad %s' -- <article>`
  gives the commit it was last touched in. `git log <that>..main --oneline`
  lists everything since.
- **Walk the outline's Backed by column.** For each section, open the named
  file, commit, or README section at `main` and check that it still says what
  the draft says. A renamed function, a hazard renumbered, a README section
  moved, a fixed open item, or a count from a reloaded extract are each a
  finding.
- **Re-measure what can be re-measured.** A count drawn from the cluster is
  checked against the cluster, not against the README that once quoted it.
  When the number moved because the extract changed, the draft either dates
  the old figure to its extract or takes the new one, and says which.
- **Report per article: still true, true but stale, no longer true.** "Stale"
  means the claim holds but its figure, file path, or wording no longer matches
  the repo. Put the no-longer-true list first.

### Clarify a passage

Triggered by "what does this mean?", "is it clear to the reader that...", or a
quoted sentence with a question.

Answer from the source, not from the draft: read the function, the config, the
README section, or query the cluster. Then check whether the article gives an
outside reader the same answer. A user who had to ask is evidence that it does
not, so say what is missing and offer the edit.

When the user's message stops mid-sentence, ask for the rest instead of
answering a guessed completion.

### Revise

- **Treat each request as an instance of a rule.** "Say the entity-match phase,
  not the phase" is the rule "name the actor precisely"; apply it everywhere it
  holds **in the files the request named**.
- **An edit that reaches a file the request did not name gets confirmed
  first.** Say what you found and what you would change, and leave the file
  alone until the user answers. This covers the outline, the READMEs, and any
  sibling article. Two exceptions, because omitting them breaks the change that
  was asked for: the outline row quoting a title you just changed, and a
  cross-reference your own edit invalidated.
- **When a rule would repeat a word many times,** confirm the scope before
  applying it, such as every occurrence against the first mention in each
  section.
- **Wording edits never change a figure, and clarifying a claim never
  strengthens it.** Keep every hedge the paragraph had.
- **A title change updates the outline's article table.** Grep the repo for the
  old title.
- **After moving or deleting a section,** re-derive its heading and re-check
  every "next section", "above", and "below".
- **Run the full Verification before reporting done**, and report the numbers.

### Carry repo changes into the blog

The drafts drift silently when the repo moves. When a README figure, a hazard
number, a section heading, a function name, or a config default changes, grep
the drafts for it (`grep -n '<figure or name>' docs/blog/*.md`) and update them
in the same change, or tell the user which drafts it reaches. Update
attributions and qualifiers to what was measured, not to what a plan predicted.
An addition to an article near its cap is paid for with a trim in the same
edit.

### Draft a new article

Check the outline first. If another article already owns the topic, the new one
links to it rather than re-explaining it.

The outline's section table for the article is the drafting scaffold: a section
list with a word budget each, the content, and what backs it. Before drafting,
add what the table lacks:

- The figures each section quotes, with the arithmetic behind any denominator
  spelled out so the drafter cannot guess it, and the extract each was measured
  on.
- The tables the article needs, with their rows named.
- The traps: for each figure that is easy to misread, a sentence saying what not
  to write. Uncalibrated scores read as probabilities, and point-in-time counts
  read as invariants, are this series' two standing traps.
- Every real entity a section would name, ruled on in advance: excluded
  (named), duplicate-only (may be named), or flagged (placeholder).
- Where the three attributions go, and which visuals are needed.

Then draft in this order, because each step constrains the next:

1. **Name the finding in one sentence.** Everything in the article supports it.
   If it takes two sentences, that is either two articles or one article and a
   deferral.
2. **Collect the figures and copy the source tables in verbatim.** Every figure
   carries its dataset and extract and names what produced it: a script, a
   query, a commit. Table rows do not count against the length cap, so a table
   is cheaper than the prose that would replace it.
3. **Lay out the sections, one finding each.** Section order is part of the
   argument, so read Section order and article shape in the style rules before
   fixing the order.
4. **Draft the body before the opening.** Tables first, then the commentary
   each table needs, then the intro, and the title last, because the title has
   to survive the paraphrase test against what the draft actually says.
5. **Add a terms table** if the article uses more than a few terms a reader
   outside the repo would not know, and define each of them only there. The
   markup is under Terms tables and definitions in the style rules. The
   top-level README's Glossary is the vocabulary to draw from.
6. **Review the draft against the checklist below** and run the verification
   commands, before showing it to anyone. Send it for a referent pass as well,
   to an agent holding the draft and no repository: the checks above cannot
   catch a reference that resolves only for someone who has the source.
7. **Register it** in the outline's article table, and update any sibling
   article whose deferral now points at it.

### Prepare for publication

The series is published on Blogger. `scripts/blog_to_blogger.py` converts an
article to HTML for Blogger's HTML view; its docstring says why each rule
exists, and `tests/test_blog_to_blogger.py` pins them. For another platform,
ask the user how it is converted before running anything.

```bash
.venv/bin/python scripts/blog_to_blogger.py docs/blog/<article>.md \
  --diagrams-dir <scratchpad>/<article> > <scratchpad>/<article>/post.html
```

- **Convert from the merged article, pinned to the merge commit.** The script
  pins `blob/main` links to `HEAD` by default; pass `--commit` with the merge
  commit when converting from a branch, so a reader following a figure's link
  sees the figure the article quotes.
- **Check the output before handing it over:** no `style`, `class` or `id`
  attribute, no newline inside a table, list or `<details>` (Blogger shows
  each as `<br>`), every tag balanced, and no Markdown left outside `<pre>`.
  The script prints the post title to stderr; it goes in Blogger's title
  field, not the body.
- **Tell the user how to paste it:** into Blogger's HTML view, replacing each
  `<p>[DIAGRAM n GOES HERE]</p>` with the uploaded PNG of `diagram-<n>.mmd`,
  and publish from the HTML view. The Compose view re-serializes the post,
  which is the usual way a pasted table gets flattened.
- **Keep the output out of the tree.** Write it to the scratchpad; a generated
  page is transient and is never committed.

Whatever the platform:

- **Run the anonymization check on the final text** (see Verification) and on
  the generated HTML with `--paths`, and rule on every finding as the
  `anonymization-check` skill says. A published post is outside the repo and
  cannot be amended by a commit.
- **Paraphrase commit `972df7e`, never quote it.** Its message names two flagged
  entities and predates the naming rule; rewriting history was ruled out
  (outline production note 1).
- **Render each mermaid fence to a PNG for any platform that does not run
  mermaid,** and leave the fence in the draft as the editable source. Copy
  the fence body to a `.mmd` file in the scratchpad and render it with
  `npx --yes @mermaid-js/mermaid-cli@12.0.0 -i diagram.mmd -o diagram.png -s 2 -b white`
  (`-s 2` for a sharp image on high-density screens, `-b white` so the diagram
  does not vanish on a dark theme). Its first run downloads a headless
  Chromium, so say so before running it. Re-render whenever the fence changes,
  and look at each PNG before handing it over: a label that fits in the
  Markdown preview can be clipped in the image. Do not commit the rendered
  image unless the user asks.
- **A terms table's `<details>` block** needs a platform that keeps raw HTML.
  Blogger's HTML view keeps it, and the fold works on the published post;
  check the preview on any other platform.
- **Pin the figure links.** Re-point links to README sections and docs that
  carry figures to the commit the figures were read at (see Attribution in the
  style rules).
- **State the extract once.** Every article carries one point-in-time sentence
  (outline production note 2), naming the extract date its counts come from.

## Using subagents

Most of this work parallelizes, because articles are independent files and the
analyses of one article are independent of each other. Fan out when the work
splits cleanly, and keep the judgment in one place.

**Worth delegating:**

- **One article per agent** for an accuracy check against `main`, or when a
  repo change touches several drafts.
- **One analysis per agent** on a single long article: claims and figures
  against the repo and the cluster, structure and section order, register and
  wording. These read the same file and produce separate findings.
- **A referent pass, run by an agent with no repository access.** Give it the
  draft text and nothing else, and ask one question: for every pronoun,
  demonstrative and bare category noun, what would a reader substitute? The
  author cannot run this on their own draft, because they resolve each
  reference from context they are carrying and the reader is not. Brief it to
  report, not to edit, and to quote each sentence.
- **A second opinion on a high-stakes article**, one reviewer with this skill
  and one without it. They find different things.

**Keep with the coordinator:**

- Deciding which findings to apply, and asking the user.
- Every edit to a file in the repo. A subagent that writes to a shared draft
  races the next one; give each agent a scratch copy, or have it report rather
  than edit.
- The anonymization ruling. The scanner gathers evidence; the ruling on whether
  a sentence accuses anyone stays with the coordinator.
- The verification block, run once over the finished state.
- Commits, which need the user anyway.

**Briefing an agent:** name the exact files, say the repo is read-only and
where to write output, and say which analysis it owns so two agents do not
produce the same list. Tell it never to paste a carrier's name, DOT number,
address, phone or email from the cluster into its report. A subagent's report
is a claim, not a result: check what it says against the repo or the cluster
before repeating it to the user or acting on it.

## Verification

Run from the repo root. Set `A` to the article and `BASE` to the branch point.
Paste the output when reporting; a summary without numbers is not a check.

````bash
A=docs/blog/2026-08-30-02-tuning-an-entity-matcher.md; BASE=main

# Length, with and without tables. "Without tables" is the prose the length
# rules govern: aim for the outline's target, justify past 2,000 in the
# outline, hard cap 3,000. "With tables" adds the table cells back, which is
# what the reader scrolls: soft ceiling 3,500. Code blocks are reported
# separately; mermaid source counts nowhere, since it publishes as a PNG.
# URLs, table separator rows and the closing `## Source` section count
# nowhere: the Source section is the same boilerplate in every article.
.venv/bin/python - "$A" <<'EOF'
import re, sys
def words(s):
    s = re.sub(r'\(https?://[^)]*\)', '', s)
    return len(re.sub(r'https?://\S+', '', s).split())
raw = open(sys.argv[1], encoding="utf-8").read()
raw = re.split(r'^## Source\s*$', raw, flags=re.M)[0]
body = re.sub(r'```mermaid.*?```', '', raw, flags=re.S)
code = sum(words(b) - 2 for b in re.findall(r'```.*?```', body, flags=re.S))
body = re.sub(r'```.*?```', '', body, flags=re.S)
rows = [r for r in re.findall(r'^\|.*$', body, flags=re.M)
        if not re.fullmatch(r'[|\s:-]+', r)]
tables = sum(words(r.replace('|', ' ')) for r in rows)
prose = words(re.sub(r'^\|.*$', '', body, flags=re.M))
print(f'without tables: {prose}   tables: {tables}   '
      f'with tables: {prose + tables}   code blocks: {code}')
EOF

# Figures: compare numeric token counts, not presence. A trim can drop a figure
# that a presence check misses because the number appears elsewhere. Every
# decrease must be one you meant.
.venv/bin/python - "$A" "$BASE" <<'EOF'
import re, subprocess, sys
from collections import Counter
path, base = sys.argv[1], sys.argv[2]
old = subprocess.run(['git', 'show', f'{base}:{path}'],
                     capture_output=True, text=True).stdout
new = open(path, encoding="utf-8").read()
tok = lambda s: Counter(re.findall(r'\d+(?:[.,/:x]\d+)*', s))
print('decreased:', dict(tok(old) - tok(new)))
print('increased:', dict(tok(new) - tok(old)))
EOF

# Names the matcher flagged. Needs the cluster up; exit 2 means "not checked",
# never "clean". Rule on each finding as the anonymization-check skill says.
.venv/bin/python .claude/skills/anonymization-check/scripts/scan_identifiers.py --paths "$A"

# Terms table folded, if the article has one: the heading is followed by the
# <details> wrapper.
grep -q '^## Terms used in this article' "$A" && \
  { grep -A2 '^## Terms used in this article' "$A" | grep -q '<details>' \
    || echo 'terms table not folded'; }

# Em dashes outside table rows. Edits reintroduce them, so count after every pass.
grep -v '^|' "$A" | grep -o '—' | wc -l

# First-person singular. Review each hit: a quoted question may use "I".
grep -nwE 'I|me|my' "$A"

# Sentence length: aim for a mean near 14, repair anything past 25. The count
# past 25 matters more than the mean, which hides a few 40-word sentences.
.venv/bin/python - "$A" <<'EOF'
import re, statistics, sys
t = open(sys.argv[1], encoding="utf-8").read()
t = re.sub(r'```.*?```', '', t, flags=re.S)
t = re.sub(r'^\|.*$', '', t, flags=re.M)
t = re.sub(r'^#.*$', '', t, flags=re.M)
t = re.sub(r'\(https?://[^)]*\)', '', t)
sents = []
# Split on list items too, or an unpunctuated bullet list reads as one sentence.
for para in re.split(r'\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)', t):
    for x in re.split(r'(?<=[.!?])[*`"\)]*\s+', para):
        x = ' '.join(x.split())
        if len(x.split()) > 2:
            sents.append(x)
lens = [len(x.split()) for x in sents]
print(f'sentences {len(lens)}  mean {statistics.mean(lens):.1f}  '
      f'median {statistics.median(lens):.0f}  '
      f'over 20: {sum(n > 20 for n in lens)}  over 25: {sum(n > 25 for n in lens)}')
for n, x in sorted(zip(lens, sents), reverse=True)[:10]:
    if n > 25:
        print(n, x[:90])
EOF

# Clauses: at most two per sentence. Counts joins (a comma before and/but/so/
# which/while/because, a semicolon, a colon before lowercase, or a
# subordinating word); two or more joins means three clauses. Split at the
# second join. Some noun clauses are flagged too; split those when it reads
# as easily.
.venv/bin/python - "$A" <<'EOF'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
t = re.sub(r'```.*?```', '', t, flags=re.S); t = re.sub(r'^\|.*$', '', t, flags=re.M)
t = re.sub(r'^#.*$', '', t, flags=re.M); t = re.sub(r'<[^>]+>', '', t)
t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t); t = re.sub(r'`[^`]*`', 'X', t)
JOIN = re.compile(r";|:\s+[a-z]|,\s+(?:and|but|or|so|yet|while|which|where|when|because|though|although|since|then)\b"
                  r"|\s(?:because|although|though|whereas|unless|until|while|which|whose|where)\s", re.I)
sents = []
for para in re.split(r'\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)', t):
    for x in re.split(r'(?<=[.!?])[*"\)]*\s+', para):
        x = ' '.join(x.split())
        if len(x.split()) > 2: sents.append(x)
hits = [(len(JOIN.findall(x)), x) for x in sents]
over = [h for h in hits if h[0] >= 2]
print(f'sentences {len(sents)}  with 3+ clauses (2+ joins): {len(over)}')
for n, x in over: print(f'  {n + 1} clauses: {x[:120]}')
EOF

# The reversal tic ("It was not.", "They are not."). Count before fixing one.
grep -nE '\b(It|They|That|This|The [a-z]+) (is|are|was|were) not\.' "$A"

# Sentences opening on a conjunction or a preposition, and passive verbs. Every
# hit is a rewrite: put the subject first, and name who or what acts. A
# be-verb before an -ed adjective ("may be related") is a false positive;
# reword it anyway so the check stays quiet.
.venv/bin/python - "$A" <<'EOF'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
t = re.sub(r'```.*?```', '', t, flags=re.S)
t = re.sub(r'^\|.*$', '', t, flags=re.M)
t = re.sub(r'<[^>]+>', '', t)
t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
OPENERS = set('''and but or nor so yet also about above across after against
along among around as at before behind below beside between beyond by despite
during for from in inside into like near of off on onto over past per since
through to toward under until upon via with within without'''.split())
IRREGULAR = set('''built made set kept held known shown given taken done found
seen written chosen run read left sent paid put told brought cut driven drawn
hidden meant broken'''.split())
sents = []
for para in re.split(r'\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)', t):
    if para.lstrip().startswith('#'):
        sents.append(' '.join(para.lstrip('# ').split()))
        continue
    for x in re.split(r'(?<=[.!?])[*`"\)]*\s+', para):
        x = ' '.join(x.split())
        if len(x.split()) > 2:
            sents.append(x)
for x in sents:
    first = re.sub(r'^[^A-Za-z]+', '', x).split(' ')[0].lower()
    if first in OPENERS:
        print('OPENER: ', x[:100])
    m = re.search(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(\w+)\b", x, re.I)
    if m and (m.group(1).lower().endswith('ed') or m.group(1).lower() in IRREGULAR):
        print('PASSIVE:', m.group(0), '|', x[:90])
EOF

# Paragraphs opening on a clause before the subject: a subordinate clause, a
# purpose infinitive ("To check ..."), or a noun clause as subject ("Where X
# sits decides ..."). Read each paragraph opening on an -ing word too.
grep -nE '^(Because|Since|While|Although|When|Given|If|After|Once|To|Where|Whether|What|How) ' "$A"
grep -nE '^[A-Z][a-z]+ing ' "$A" | grep -vE '^[0-9]+:(Nothing|Everything|Something|Anything) '

# One of a pair named bare. Set PAIRS to what this article has two or more of
# (listed before the register pass); each hit is a use to qualify or justify.
PAIRS='stage|encoder|subfield|sweep'
grep -nE "\b(the|a|that|this) ($PAIRS)\b" "$A"

# Attribution. The link checks below pass vacuously on an article with no
# links, so count first: fewer than 3 links to the repo means the first-screen,
# beside-the-artifact and closing placements cannot all be there.
grep -c 'github.com/freemansoft/entitopia' "$A"

# Line-wrapped link destinations. CommonMark does not allow a newline inside a
# URL, so a wrapped link renders as literal text. Prettier does not repair it.
grep -nE '\]\(https?://[^)]*$' "$A"

# Repo-relative links, which break once the article leaves the repo.
grep -nE '\]\((\.\./|\./|[a-zA-Z_-]+/)[^)]*\)' "$A"

# Markdown format gate, the same version CI runs.
npx --yes prettier@3.3.3 --check "$A"
````

## Checklist

One line per rule. The section named in parentheses in
`references/style-rules.md` holds the rule, its reason, and examples.

**Names** (Naming real entities)

- [ ] No flagged entity named: no carrier name, DOT number, address, phone or
      email from a sweep pair. Placeholders in the `<CARRIER-A>` style.
- [ ] Excluded entities (filing services, insurers, corporate parents) named
      only as sources of false positives being filtered out.
- [ ] Commit `972df7e` paraphrased, never quoted.

**Claims** (Verifying claims; Point-in-time figures)

- [ ] Mechanisms, defaults, and enforced constraints checked in the code or
      config at `main`, not taken from the draft or the outline.
- [ ] Figures checked against the README, the cluster, or the commit that
      produced them; each count attributed to its extract.
- [ ] Scores described as uncalibrated, never as probabilities.
- [ ] Every definition fits every row of the table it labels.
- [ ] Measured and inferred kept apart; no hedge lost in an edit.

**Terms** (Terminology; Describing mechanisms)

- [ ] One name per concept, taken from the code, config, or README Glossary.
- [ ] Every pronoun, demonstrative and bare category noun resolves for a reader
      with no repo access.
- [ ] Where the article has two of something, neither is ever named bare.
- [ ] Vendor- or agency-specific mechanisms qualified at the first mention in
      each `##` section.
- [ ] Actors named precisely; each term says what kind of thing it is.
- [ ] Mechanisms described by direction and data; exclusive alternatives stated.

**Structure** (Openings; Headings and titles; Terms tables and definitions;
Section order and article shape; Altitude)

- [ ] Framing paragraph between the title and the first `##`, with the setup
      the finding depends on.
- [ ] Each heading states one finding in concrete words and matches its section.
- [ ] Section order carries the argument: one thread per section, mechanism
      before verdict, no incident told twice.
- [ ] Title survives the paraphrase test; the outline's article table matches it.
- [ ] Word count reported with and without tables: without tables near the
      outline's target, any excess past 2,000 justified in the outline; with
      tables under about 3,500.
- [ ] Altitude: highlights, decision points and controls stay; per-run detail
      moves behind a link to the README or doc that holds it, and every such
      cut names the link that absorbs it.
- [ ] Each term defined once; every cross-reference resolves.
- [ ] A terms table, folded in `<details>` with the heading outside, covering every
      Elasticsearch and matching term the article uses.
- [ ] Takeaway is a list the reader can act on, and ends on a fact.

**Register** (Register)

- [ ] No em dashes, first-person singular, amplifiers, or closing flourish.
- [ ] No passive voice, in sentences or headings: every clause names its actor.
- [ ] No sentence opens on a conjunction (And, But, So) or a preposition (For,
      On, Against), and no paragraph opens on a clause before its subject.
- [ ] No "X is where / why / what" copula sentence standing in for an actor and
      a verb.
- [ ] Sentence mean near 14 words, and nothing past 25 that does not earn it.
- [ ] At most two clauses per sentence: one main, plus one subordinate or joined.

**Visuals, tables, sources** (Diagrams and code blocks; Presentation;
Attribution; Cross-article references)

- [ ] At least one diagram, of the article's central mechanism.
- [ ] Diagrams use the prose's names and draw exclusive branches as branches.
- [ ] Code and config blocks show the fragment that carries the point.
- [ ] Table sections run intro, table, commentary; no table cut.
- [ ] Ends with a `## Source` section linking the GitHub repo.
- [ ] Repo on the first screen, source beside each table, absolute GitHub URLs
      throughout, repo and source both linked at the close.
