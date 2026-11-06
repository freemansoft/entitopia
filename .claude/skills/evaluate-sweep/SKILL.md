---
name: evaluate-sweep
description: Judge a finished entitopia matching sweep (DOT-Commercial chameleon-detection, CMS-Providers hospital-duplicates, or any project with an entity-match step) against a baseline, under expectations declared before the run. It gates on guarded metrics, then runs the project's validation and review-sample scripts, and records the measured numbers, anonymized. Invoke with /evaluate-sweep after re-running a sweep to test a config, weight, analyzer or selector change.
disable-model-invocation: true
---

# Evaluate a sweep

A sweep always finishes green and always prints confident counts. Whether the
change under test actually improved anything is a separate question, and this
repo has answered it wrong before in quiet ways. Comparing an alias against
itself printed all-zero deltas. A field rename made a metric read zero on one
side. A signal rename let a guarded metric print "ok" over a 0 → 208 jump.
This skill runs the checks in the order the precision plan established, and
stops at the first one that fails.

The invariant throughout: **expectations are declared before the result is
seen, and a failed check is investigated, never re-declared.** Loosening an
expectation after the fact turns the guardrail into a rubber stamp.

## Before you start: pin the inputs

Ask the user for whatever isn't already clear:

1. **Project and step**, for example `DOT-Commercial` / `chameleon-detection`
   or `CMS-Providers` / `hospital-duplicates`. The metrics are defined in
   `<project>/configuration/<step>/metrics.json`.
2. **Candidate index:** the concrete index the new sweep wrote, such as
   `chameleon-candidates-2026.08.17-000001`. List the options with
   `curl -s 'http://localhost:9200/_cat/indices/<prefix>-*?v&s=index'`.
3. **Baseline:** prefer the previous sweep's concrete index, because only an
   index can be checked for drift. A record file (`--baseline-record`) or the
   `baseline` declared in `metrics.json` is the fallback once that index has
   been pruned. DOT-Commercial logs each task's result index in
   `DOT-Commercial/data/precision/result-indexes.txt`.
4. **Expectations:** a JSON file mapping metric names to `must_not_fall`,
   `must_not_rise`, `within_10pct`, `stable_within_10pct` or `informational`.
   `within_10pct` is one-sided: it allows any rise and fails only on a fall of
   more than 10%. Use `stable_within_10pct` when a large move in either
   direction would be suspect. Keep it under
   `<project>/data/precision/` (gitignored). If none exists for this change,
   draft one with the user **before running anything that prints the
   candidate's numbers**. Give each metric a one-line reason tied to what the
   change should do. For example: a filter meant to remove bad predecessors
   should drop `pairs`, so that metric is `informational`, while the
   byte-identical-name shape is real by construction, so `canary` is
   `must_not_fall`. Use `DOT-Commercial/data/precision/expect-*.json` as worked
   examples.

If the sweep hasn't run yet, stop here: running it is a cluster write and the
user's call. Point out the same-day merge hazard first. A sweep run on a date
that already has `<prefix>-<today>-000001` merges into it and unions two
configs.

## The chain

Run the steps in order. A non-zero exit at any step ends the chain. Report
what failed and why, and don't skip ahead to recording.

### 1. Guarded comparison (the gate)

```bash
.venv/bin/python .claude/skills/evaluate-sweep/scripts/guarded_compare.py \
  --project <project> --step <step> \
  --baseline-index <baseline> --candidate-index <candidate> \
  --expectations <project>/data/precision/expect-<change>.json
```

It reuses the repo's own `run_metrics` projection and `utils.sweep_compare`,
so every metric means exactly what `metrics.json` says. Before comparing, it
refuses three traps:

- An alias resolving to the same index as the baseline.
- An expectation naming a metric the project doesn't define.
- **Schema drift:** a metric reading a field, or naming a signal type, that
  exists on only one side.

- **Exit 0:** no guarded metric regressed. Continue.
- **Exit 1, REGRESSED:** stop. Name each regressed metric and its delta, and
  investigate why it moved. Don't edit the expectations. The rollback is an
  atomic alias repoint back to the baseline, which is a cluster write:
  show the user the exact `_aliases` request and run it only on their say-so.
- **Exit 2, CANNOT JUDGE:** stop and relay the reason. For drift, the honest
  fix is a baseline re-swept with the current code. Use `--accept-drift METRIC`
  only when the user has ruled that the drift can't move that metric, and
  report that it was accepted.

### 2. Must-not-change check (only for behaviour-preserving changes)

If the change claims to alter nothing, such as a refactor, a rename or a
performance change, identical aggregate counts don't prove it. A pair lost and
a pair gained in the same band cancel out. Compare the pair id sets:

```bash
.venv/bin/python scripts/compare_pair_ids.py --baseline-index <baseline> --candidate-index <candidate>
```

Any difference fails a no-op change. Stop and report it.

### 3. Validate against reality (where the project has scripts for it)

Step 1 measures the sweep against itself. These measure it against the
project's own definition and against outside data. Today only DOT-Commercial
has them:

```bash
.venv/bin/python DOT-Commercial/scripts/measure_chameleon_shape.py --pairs-index <candidate>
.venv/bin/python DOT-Commercial/scripts/measure_crash_lift.py \
  --pairs-index <candidate> --carriers-index carriers-000001 --crashes-index crashes-000001 --no-write
```

Both are read-only as written and take seconds. Without `--no-write`,
`measure_crash_lift.py` persists its run to the `chameleon-validation-000001`
alias so the figures can be re-derived by `run_id`. That persistence is how the
README's figures stay checkable, so offer it once the user decides to keep
the result. It is a cluster write, so ask first. A null crash lift is a result,
not a failure: the proxy is weak and confounded by fleet size, which is why
the shape check comes first.

For a project with no validation scripts, say so plainly. Don't improvise a
substitute and present it as validation.

### 4. Pull a sample for a human (lifecycle sweeps only)

No metric can measure recall, because there's no list of known true matches.
A stratified sample a person adjudicates is the cheap substitute:

```bash
.venv/bin/python DOT-Commercial/scripts/sample_pairs_for_review.py \
  --pairs-index <candidate> --out DOT-Commercial/data/precision/review-sample-<date>.jsonl
```

The output lands under gitignored `*/data/` because it carries flagged
carriers' names and addresses. **Never quote its rows** in chat summaries
meant for publishing, in docs or in commits. The sampler reads
predecessor/successor fields, so it doesn't work on all-entities sweeps like
CMS hospital-duplicates. Say so rather than adapting it on the fly.

### 5. Record the result

Only after the user decides to keep the candidate:

- Save the candidate's record as the next baseline by re-running step 1 with
  `--write-record <project>/data/precision/baseline-<change>.json`.
- Update the project README with the **measured** figures:
  - the date
  - both concrete index names and the candidate's analysis fingerprint
  - the expectations file used
  - the per-metric before/after table
  - the validation output, quoted with its filters
- Keep the numbers exactly as printed. Aggregate counts identify no one, but
  any example pair must be anonymized (`<CARRIER-A>` and similar).
- Before committing, run the `anonymization-check` skill on the staged diff
  and the commit message.

## Report back

Lead with the verdict, then give the evidence:

```
Verdict: <promote / roll back / cannot judge> — <one-line reason>
Compared: <baseline concrete index or record> -> <candidate concrete index>  (fingerprint <fp>)
Expectations: <file>
Gate: <exit code>; regressed: <metrics or none>; drift: <metrics or none>
Validation: <shape / crash-lift headline numbers, or "none for this project">
Review sample: <path, or "not applicable">
Not done: <writes awaiting the user's say-so: alias repoint, crash-lift persistence, README>
```
