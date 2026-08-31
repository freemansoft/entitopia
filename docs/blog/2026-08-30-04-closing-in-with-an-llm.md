# Closing in on a data model with an LLM

## What the commit ratio does not tell you

As of the commit this series branched from, seventy-one of a hundred and fifty
commits here carried a co-authorship line — a count this article's own commit
already moves, arguing for dating every figure on its own. The ratio invites
the wrong conclusion regardless: it is not "an LLM wrote a fraud detector,"
and the pattern is narrower than the ratio suggests.

What the model was actually good for was a smaller thing done repeatedly:
turning questions that could be settled by measurement into measurements, and
writing down what came back. What it was bad at is more interesting, because
the failures had a shape, and the shape is what made them catchable.

_Every figure below is a point-in-time measurement against one extract of one
public dataset — evidence that a problem was real and roughly how big it was,
not a value to expect on your own data._

## The loop: measure, write it down, trust the measurement over the document

The working cycle was the same every time. Measure something about the data.
Write the result down next to the configuration it describes. Let that document
be the source of truth for the next session. And when a fresh measurement
disagrees with the document, **trust the measurement and update the document.**

That last rule is doing more work than it appears to. An LLM will happily
recall a number from a document it read ten minutes ago and present it as a
current fact. The countermeasure is not to distrust the model but to make
re-measuring cheap and to state explicitly, in the document, that the document
loses ties. Every count in this repository carries a note saying it is a
point-in-time figure against one extract, never a value to assert against in a
test.

It also explains why the docs read the way they do — dense with numbers, every
claim carrying the measurement behind it. They are written to be re-read by
something that has no memory of writing them.

## Research before design, and getting a cheap "no"

The work opened with a question rather than a plan: would vector or neural
search do this better than phonetic and ICU analyzers?

The answer was no. The more useful part was that the premise needed correcting
in three ways, one of them a factual error in the project's own earlier design
spec — a claim that a managed search service could not install the phonetic
plugin, which was simply wrong and had been sitting there shaping decisions.

A cheap, well-evidenced "no" before any code is written is the win, not the
consolation prize. The alternative is a month of migration work justified by an
assumption nobody checked. This is the task shape LLMs are genuinely strong at:
broad, checkable, bounded research where the output is a decision rather than
an artifact.

## Why, never what

One convention shaped the codebase more than any other. Every function, class
and module gets a comment explaining **why it exists** — never what it does.

```python
def blended_overlap(a: set[str], b: set[str]) -> float | None:
    """Half jaccard, half containment. None when either set is empty.

    Pure jaccard would punish name abbreviation, which is one of the evasion
    tactics being hunted. Pure containment would treat any subset as a perfect
    match. Blending keeps full overlap ranked above a subset match while still
    scoring abbreviation highly.
    """
```

The bad version narrates the steps: checks if either set is empty, computes
jaccard, computes containment, multiplies each by 0.5. That comment goes stale
the moment anyone edits the body, and it tells a reader nothing the code did
not already say.

This is not a style preference. This codebase's characteristic failure is a
phase that logs success while producing quietly wrong output, so the comments
have to carry the reasoning, the failed alternative, and the consequence of
getting it wrong. It also happens to be exactly what a model needs to not
re-introduce a bug that was already fixed once — a comment naming the
alternative that failed is a fence.

## Three times the plan was wrong, and how it got caught

The instructive failures were not hallucinated APIs. They were confident,
plausible specifications that were internally inconsistent.

**A formula that contradicted its own example.** The design spec specified a
rarity weighting of `1 - count/N` while claiming, in the same bullet, that a
dominant filer would score about 0.07. Those cannot both be true: `1 - 0.094`
is 0.906. The implementer found that the stated formula and its own test
expectations could not both hold, and stopped to ask instead of picking one.
The formula was the wrong half — with 89 distinct values the largest at 9.4%,
`1 - share` compresses the entire population into `[0.906, 1.0]` and the signal
discriminates nothing. Normalized IDF spreads it across `[0.167, 1.0]`.

**A verification step that asserted the wrong result.** A task brief said to
expect several tokens from a Beider-Morse analyzer on the name `SMITH`. That is
wrong — Beider-Morse emits multiple encodings only for names ambiguous across
the configured languages. The expectation blocked the task until the
implementer demonstrated the config was correct by getting four tokens out of
`GONZALEZ` under identical settings, then stopped to ask.

**A test that passed for the wrong reason.** A rarity lookup normalized its
input but did a raw dictionary lookup against keys built elsewhere. Production
worked by convention, with nothing enforcing it. A casing disagreement would
make every lookup miss, every value fall back to the "unseen" default, and the
rarity weighting **silently turn itself off** — the exact failure it exists to
prevent, with no error. It also exposed a test asserting a threshold its own
math made unreachable.

The pattern across all three is the same: the plan was specific enough to
contradict itself. A vague spec would have been implemented smoothly and been
wrong. **The value is in writing plans explicit enough to be falsified**, and
in an implementer that stops on a contradiction rather than picking the reading
that lets it continue.

## A skill that was measured and deleted

The most useful negative result was about the tooling rather than the data.

A dataset-onboarding skill was built — 1,189 lines across 8 files — to help
add a new dataset to the project. Then it was evaluated properly: three test
cases, run with and without it. The result was **one marginal win, two ties,
and it was slower on the case it won.**

The reason is worth sitting with. The baselines solved all three cases by
reading the repository's own READMEs and design specs, which already document
the join-key blocker, the type-boundary diagnosis, and the cardinality trap. So
the skill was largely a fourth copy of material that already lived next to the
configuration it described. That is a **drift liability**: rebalance a weight
in the config and the skill's prose still confidently describes the old
behavior.

A second evaluation confirmed it from another angle. Running a
description-optimization loop across 20 trigger queries — 10 that should fire
it, 10 near-misses that should not — the skill fired on **0 of 10** should-fire
queries across five iterations. Its entire score came from correctly staying
silent. The original description beat all five rewrites, which is another way
of saying the wording was never the problem: the model could tell the
repository already answered these questions.

It was cut to 93 lines in one file and converted into an ordinary document.
The one piece with standalone value — the data profiler — moved into the
repository as a script, because its usefulness never depended on a skill being
invoked.

The transferable rule: tooling that restates material already living next to
the code is not neutral. It is a second copy that will drift.

## An LLM inside the product, behind a hard gate

There is one place a model belongs in the pipeline itself, and it comes with a
non-optional human step.

The matcher suppresses values that cannot establish identity. Two mechanisms
cover it: a hand-declared list, and a frequency scan that finds values shared by
more than N records. The scan has a structural blind spot — it can only catch
values that are _common_. A malformed identifier appearing on three records is
still garbage, and no frequency threshold will ever notice.

That gap is exactly what pattern recognition is good at. Pull the **distinct
values** per field with a terms aggregation — not rows, since the interesting
object is the value, not the record, and that bounds the input regardless of
corpus size — and have a model classify each as placeholder, formatting
artifact, or plausible real value, with its reasoning.

Then a human approves before anything merges. This step is not optional, and
the reason is specific: adding a value to the ignore list suppresses evidence
outright. A wrong entry throws no error, fails no test, and shows up in no
report. It silently removes evidence from every future run — the same
silent-failure shape the whole project is built to defend against. A suggestion
generator is safe. A writer is not.

## The rule that has nothing to do with tooling

This project reads public data about real companies and scores them for
suspected fraud. A score is **an analysis, not a finding.** The thresholds are
uncalibrated, the sweep truncates, and a high score routinely reflects a shared
filing agent, a corporate parent, or a placeholder rather than wrongdoing.

So no flagged entity is named anywhere in the repository — code, docs, or
commit messages alike. Names are replaced with obviously synthetic
placeholders that preserve whatever made the example worth recording. The
pattern is the reusable part: identical name, same address, one day after a
shutdown. If an example stops being useful once the name is removed, the name
was doing argumentative work it should not have been doing.

The inverse case is the one people get wrong. An entity whose identifier is
being **excluded** — a filing service or an insurance agency whose contact
details sit on hundreds of unrelated records — _should_ be named, because
whoever maintains that ignore list needs to recognize it. Naming it is the
opposite of an accusation.

The test is which side of the filter the name sits on. Worth adding honestly:
this convention arrived late, and one already-pushed commit message predates
it. It stays as it is — the gap is recorded in the commit that added the rule,
not rewritten out of the history.

## What to delegate, what to verify, what to leave alone

**Delegate** the bounded, checkable work: research questions with a decision at
the end, measurements over a corpus, converting a finding into a document that
the next session can read.

**Verify** anything that produces a number. Not by re-reading the output — by
re-running the measurement. Two published figures in this project had already
drifted from what the underlying data said, and neither would have been caught
by proofreading.

**Leave alone** anything that suppresses evidence, deletes data, or publishes a
claim about an identifiable person or business. Those failures are silent, and
a silent failure is exactly what neither a model nor a reviewer will catch on
the next pass.
