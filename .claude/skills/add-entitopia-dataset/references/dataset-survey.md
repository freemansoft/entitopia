# Surveying a candidate dataset

Decide two things before writing any configuration: **can this dataset support succession detection at all**, and **which of its fields are strong enough to weight**.

## What a fraud-detection dataset needs

Entitopia looks for an entity that terminated and a near-identical one that appeared shortly after. That needs three ingredients, and a dataset missing the second is only useful as a corroborating source, not a primary one.

| Ingredient                | Why                                                                                                     | Examples from the reference projects                                                          |
| ------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Identity fields**       | The similarity leg. Things an operator must change to hide but finds inconvenient to change.            | legal name, DBA name, street, city, phone, fax, email                                         |
| **Lifecycle dates**       | The timing leg. Without a termination _and_ a creation date you can find duplicates but not succession. | `add_date` (registration), `oos_date` (ordered out of service), authority grant/revoke events |
| **Shared infrastructure** | The strongest signal when present, because it is expensive to fake.                                     | vehicle VINs, equipment IDs, licence numbers                                                  |
| **A join key**            | Links this dataset to the entity index.                                                                 | `dot_number`, `NPI`, `Ind_PAC_ID`                                                             |

A dataset with identity fields but no dates still earns its place — it enriches the entity record. But the _timing_ signal has to come from somewhere, and if no dataset in the project carries lifecycle events, the project can only do duplicate detection, not fraud detection. Say so plainly rather than shipping a matcher that cannot distinguish the two.

## Run the profiler first

```bash
.venv/bin/python .claude/skills/add-entitopia-dataset/scripts/profile_dataset.py <path.csv>
.venv/bin/python .claude/skills/add-entitopia-dataset/scripts/profile_dataset.py <path.csv> --key col_a --key col_b
```

Use `--rows N` to sample a huge file during exploration, but **re-run a full scan before trusting a uniqueness result** — a key can be unique across the first 400,000 rows and collide at row 900,000.

## Reading the output

### The WARNINGS section

Everything here is a silent-corruption risk, not a style note. Each entry names the consequence. Act on all of them before writing `index-mappings.json`; see `mapping-hazards.md` for the fixes.

### Cardinality decides whether a field can carry a signal

This is the judgement most often got wrong, so the profiler splits columns explicitly:

- **High cardinality → fingerprint.** Can carry a match on its own. Names, streets, phones, VINs.
- **Low cardinality → filter.** Can select a population or corroborate a match, but never establish one.

The trap is a field that _sounds_ like a fingerprint. One dataset's "legal process agent" was described in its own documentation as harder to fake than a business address — the reasoning being that unrelated entities sharing a legal agent is suspicious. Measurement said otherwise:

```text
86 distinct values across 1.43M rows
most common at 9.4%
two unrelated records collide ~7% of the time by chance
```

It is a filing-services industry with fewer than a hundred vendors. The field carries which vendor an entity paid, not anything about the entity. It survived as a rarity-weighted corroborating signal at weight 0.04 rather than the strong signal it appeared to be.

**The check: distinct count, top-value share, and chance-collision rate.** The profiler computes all three. If two random records share a value more than a percent or so of the time, that field cannot carry a match.

When a low-cardinality field is worth keeping, weight it by **rarity** rather than uniformly — sharing a rare value is meaningful, sharing a dominant one is not. See `matching-signals.md` for the IDF weighting, including the formula error that made this backwards on the first attempt.

### Sparsity changes signal semantics

A field that is 23% blank is usable, but the signal reading it must treat blank as **not evaluable**, never as a match. Two records that both lack a value have not agreed on anything. This is the `None` vs `0.0` distinction in `matching-signals.md`, and getting it wrong means every sparse record looks similar to every other sparse record.

## Deciding the document ID

Every dataset needs a deterministic `_id`, or a rerun appends instead of overwrites and silently duplicates the whole dataset.

Prefer the **minimal** key that is genuinely unique — a longer key works but obscures which columns actually carry identity. The profiler distinguishes two failure modes, and they call for opposite responses:

- **Real collisions** — rows that differ but share the key. The key is wrong. Add a distinguishing column.
- **Byte-identical rows** — the source itself contains exact duplicates. A composite key correctly collapses them into one document, which is almost always what you want. One reference dataset has 10,510 of these; no key can separate them because there is nothing to separate.

Worked examples across both reference projects:

| Dataset                  | Key                                 | Why                                                                            |
| ------------------------ | ----------------------------------- | ------------------------------------------------------------------------------ |
| `hospitals`              | `Facility ID`                       | naturally unique                                                               |
| `facillity-affiliations` | `Ind_PAC_ID` + certification number | a 2-column key is unique; the obvious 3-column alternative collided on 47 rows |
| `doctors-clinicians`     | enrolment + org + address           | no 1- or 2-column key is unique; the best pair still collided on 49,795 rows   |
| `out-of-service-orders`  | 5-column composite                  | no natural key; verified 100% unique on the full 394,963 rows                  |
| `auth-history`           | all 9 columns                       | the source has 10,510 exact duplicates, deliberately collapsed                 |

## Writing it down

Record what you measured in the project's README — the cardinality numbers, the key choice and its uniqueness evidence, and any signal you rejected and why. The next person will otherwise re-derive it, and the rejected signals are the expensive part. The BOC-3 finding above only exists because someone wrote down a number that contradicted the documentation.
