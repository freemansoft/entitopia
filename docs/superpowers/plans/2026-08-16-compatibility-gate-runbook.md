# Compatibility gate — result

The gate for [the matcher generalization](2026-08-16-matcher-generalization.md),
run 2026-08-17. **Passed on both checks: the refactor moved nothing.**

## What was compared

|                      |                                                                                 |
| -------------------- | ------------------------------------------------------------------------------- |
| Source index         | `carriers-2026.08.13-000001` (via alias `carriers-000001`), 2,085,534 docs      |
| Analysis fingerprint | `0595ca890d9ec6fb` — the value every figure in `DOT-Commercial/README.md` cites |
| Baseline pairs       | `chameleon-candidates-2026.08.13-000001`, 75,537 docs                           |
| Candidate pairs      | `chameleon-candidates-2026.08.17-000001`, 75,537 docs                           |
| Baseline metrics     | `DOT-Commercial/data/precision/baseline-post-reload.json`                       |
| Code under test      | `1549c32`..`7365a24` — rollout steps 1–6                                        |

The source index was **not** reloaded between the two sweeps, which is the
condition that makes the comparison mean anything. The README's two outstanding
reload items are still outstanding; doing either first would have conflated this
refactor with the `dot_number` and composite-`_id` re-keying already queued.

## Commands

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection

.venv/bin/python scripts/compare_pair_ids.py \
    --baseline-index chameleon-candidates-2026.08.13-000001 \
    --candidate-index chameleon-candidates-2026.08.17-000001
```

Metric equality was checked by direct comparison against the committed baseline,
**not** through `utils.sweep_compare.compare()`. That engine judges whether an
intentional change moved the right metrics in the right direction, and its
vocabulary — `must_not_fall`, `must_not_rise`, `within_10pct`, `informational` —
cannot express "must not change at all", so a real regression could have cleared
it wearing `informational`.

## Sweep summary

```
48,540 predecessors, 22,316,064 candidates examined,
75,537 pairs emitted, 75,537 indexed, 43,111 truncated candidate sets, 0 errors
```

Runtime 2h09m (08:53:17 → 11:02:33). The predecessor count (48,540) and
truncation count (43,111) both match what `DOT-Commercial/README.md` records for
the baseline run, so the population selected by the config-defined
`out-of-service` selector is the same population the hardcoded one selected.

## Check 1 — all eleven metrics exactly equal

| metric                  | baseline | candidate |
| ----------------------- | -------- | --------- |
| canary                  | 11       | 11        |
| coherent_ge_070         | 584      | 584       |
| coherent_share_ge_070   | 1.0      | 1.0       |
| identical_name_triage   | 145      | 145       |
| pairs                   | 75537    | 75537     |
| pairs_ge_070            | 584      | 584       |
| predecessors_with_pairs | 23040    | 23040     |
| triage_bounded          | 197      | 197       |
| triage_unbounded        | 302      | 302       |
| vin_only                | 1        | 1         |
| vin_only_identity       | 208      | 208       |

## Check 2 — pair id sets identical

```
baseline  chameleon-candidates-2026.08.13-000001: 75,537 pairs
candidate chameleon-candidates-2026.08.17-000001: 75,537 pairs

IDENTICAL: both populations contain exactly the same pair ids
```

Exit 0. This is the check the metrics cannot make: eleven aggregate counts all
agree just as readily when a pair is lost and another gained inside the same
score band, and this is what rules that out. Composite ids are
label-independent by construction, so the comparison stays valid across the
`dot_number` → `entity_key` rename.

## What did change, deliberately

The pair **document** gained fields; the pair **population** did not. Confirmed
on a sampled pair from the new index:

- Each side carries `entity_key` alongside the project-labelled `dot_number`,
  and the two hold the same value.
- Each signal contribution carries `fields`, the config paths it reads.
- The `shared-token` contribution carries `signal_name: "vin-overlap"`, the
  project's own label, which is what replaces the deleted type name for a
  reader trying to tell what the evidence was.
- `matched_on` is unchanged and still keyed by type.

Neither check can see these: metrics are counts, and ids are composed from the
entity keys alone. That is why the sampled-document inspection is part of the
gate rather than an afterthought — a summary field that quietly stopped being
emitted would pass both checks.

## Known gap found while verifying

**`temporal` emits `"fields": []`.** Its two date paths moved into the
`lifecycle` block, and `fields_read()` only reads signal-level config keys, so a
reader of a single pair cannot tell which dates its `gap_days` was measured
between. The information exists in `entity-match.json`, but the whole argument
for per-contribution provenance is that a pair is read on its own, without the
config in hand.

Not fixed here: doing so means either teaching `fields_read()` about the
lifecycle block or emitting the lifecycle paths at document level, and both are
changes to the emitted document that should not land inside the commit range the
gate just certified. Recorded as an open item instead.
