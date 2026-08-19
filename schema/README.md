# Configuration schemas

JSON Schemas for the configuration files entitopia itself defines. **These are framework code, shared by every project** — they live at the repository root rather than inside `DOT-Commercial/` or `CMS-Providers/` because the shape of a config file is the framework's business, while the values in it are the project's.

Adding a project means writing configuration that satisfies these. It does not mean writing or editing them.

## What each one covers

| Schema                            | File it validates                 | Scope                                                                     |
| --------------------------------- | --------------------------------- | ------------------------------------------------------------------------- |
| `configuration.schema.json`       | `<project>/configuration.json`    | Steps, and which phases each runs                                         |
| `index-config.schema.json`        | `<step>/index-config.json`        | Alias, index name, source CSV, document key, row caps                     |
| `entity-match.schema.json`        | `<step>/entity-match.json`        | The analysis: entity, lifecycle, population, candidates, signals, scoring |
| `metrics.schema.json`             | `<step>/metrics.json`             | What a project measures about its own scored pairs                        |
| `pipelines.schema.json`           | `<step>/pipelines.json`           | Envelope only — see below                                                 |
| `enrichment-policies.schema.json` | `<step>/enrichment-policies.json` | Envelope, plus the `match` body entitopia's own phase reads               |
| `index-mappings.schema.json`      | `<step>/index-mappings.json`      | Envelope only                                                             |
| `index-settings.schema.json`      | `<step>/index-settings.json`      | Envelope only                                                             |

`entity-match.schema.json` is by far the largest, because it is the file an operator onboarding a dataset actually spends time in. Read it first.

## The two decisions these encode

### Unknown keys are errors

Every schema sets `additionalProperties: false` wherever entitopia owns the vocabulary. This is the load-bearing decision, not a stylistic one.

A permissive schema accepts a renamed key silently: the value is ignored, the setting falls back to its default, and nothing reports it. That is this repository's recurring failure — configuration that parses and is inert — and it is the failure the whole `validate` phase exists to convert into a loud one. When `max_shared_carriers` became `max_shared_entities`, a permissive schema would have let the old spelling through and quietly restored the default limit.

So a typo is a failure, and that is the point. If a legitimate key is being rejected, the schema is missing it — add it here rather than working around it.

### Elasticsearch's DSL is not schematized

`index-mappings`, `index-settings`, and the `processors` list inside `pipelines` are validated as **envelopes only**: entitopia checks the keys it reads, and passes everything else through untouched.

That is deliberate. What is inside them is Elasticsearch's own DSL — owned elsewhere, versioned independently, and already rejected loudly by the cluster, which this project made fatal. A schema over that interior would go stale on the next server upgrade, start rejecting valid configuration, and teach operators that a validation failure is something to work around. One validator nobody trusts is worse than none.

The rule: **schematize what entitopia invented; let Elasticsearch reject what Elasticsearch invented.**

This does mean a mapping can be well-formed and still wrong — an analyzer naming a column the source has since renamed is silently inert. No schema can see that. It is what the `validate` phase's third tier is for, which asks the live index whether the fields and subfields a config names actually exist.

## How they are used

Automatically, by the `validate` phase, as its first of three tiers:

```bash
.venv/bin/python execute_project.py --project=DOT-Commercial --step=chameleon-detection --phase=validate
```

The phase runs schema validation, then cross-file coherence, then live-index checks, stopping at the first tier that finds anything — a later tier's findings would mostly be consequences of an earlier failure. Add `validate` to a step's `phases` list ahead of the work it guards.

Directly, from Python:

```python
from utils import config_schema

config_schema.validate_file("entity-match", "DOT-Commercial/configuration/chameleon-detection/entity-match.json")
# -> [] when valid, otherwise a list of messages naming the file and the dotted key path
```

`validate_file` and `validate_mapping` return messages rather than raising, and return **every** problem rather than the first. Fixing configuration is iterative, and a validator that stops at the first error turns a five-mistake file into five runs.

In an editor, by pointing your JSON language server at the relevant schema — VS Code's `json.schemas` setting, keyed on a path glob like `**/configuration/*/entity-match.json`. This is most of the value of these being real schema files rather than validation code: it gives completion and inline errors while the config is being written, rather than when it is run.

## Two guards that must not be removed

Two schemas restate a vocabulary that also exists in Python, and both are pinned by a test so they cannot drift:

- **`entity-match.schema.json`'s signal-type enum** against `matching.signals.SIGNAL_TYPES` — `tests/test_entity_match_schema.py`.
- **`metrics.schema.json`'s predicate menu** against `utils.metric_predicates.PREDICATES` — `tests/test_metrics_schema.py`.

Drift in either direction is a real defect. A type registered in code but missing from the schema means the validator rejects a legitimate config, which is how operators learn to distrust it. One present in the schema but missing from code fails at run time instead of at validation, which is the whole thing this is meant to prevent.

`tests/test_config_schema_shipped.py` additionally asserts that every schema in this directory is reachable from some filename, so a schema nothing validates against cannot sit here looking like coverage.

## Adding a schema for a new config kind

1. Write `schema/<kind>.schema.json`. Set `additionalProperties: false` on every object entitopia owns.
2. Add the filename to `_KIND_BY_FILENAME` in `tests/test_config_schema_shipped.py`, or the reachability test will fail — as it is meant to.
3. Add it to `STEP_CONFIG_FILES` in `phase_providers/phase_validate.py` so the phase picks it up.
4. **Validate every shipped config against it before committing.** If a file that has been running in production fails, the schema is wrong, not the file. That check has already corrected two schemas here: `index-config` required a `source` that indexes written by a phase legitimately lack, and `enrichment-policies` was assumed to be an object when it is a top-level array.

## Related

- [`docs/adding-a-dataset.md`](../docs/adding-a-dataset.md) — the judgement calls that configuration cannot make for you
- [`README.md`](../README.md) § Common data-loading hazards — the failure modes these schemas cover the first layer of
- `utils/config_coherence.py` — tier 2, the cross-file checks a schema structurally cannot make
- `utils/config_liveness.py` — tier 3, what the config claims about an index, asked of the index
