# Atomic alias swap on index creation

Date: 2026-08-05
Status: proposed, not yet implemented

## Problem

`README.md:77` states that this project "creates date-stamped indexes
(`carriers-2026.08.02-000001`) behind a stable **alias** (`carriers-000001`), so
a reload can build a new index and swap without downtime." Both mermaid diagrams
in `DOT-Commercial/README.md` (the "Index Data" and "Flow" sections) draw the
relationship as one alias pointing at one index, and `CMS-Providers/README.md`
does the same for its three indexes.

**No code performs the swap.** `phase_index_creation.py` calls
`put_alias(index=<new dated index>, name=<alias>)`, which is purely additive.
There is no `delete_alias` call and no `update_aliases` action anywhere in the
codebase. Elasticsearch does not detach an alias from its previous index on its
own, so every reload adds another index to the alias and none are ever removed.

Measured on 2026-08-05, immediately after a second `carriers` reload:

```
carriers-000001 -> carriers-2026.08.05-000001    2,085,534 docs
carriers-000001 -> carriers-2026.08.01-000001    2,085,536 docs

GET /carriers-000001/_count  ->  4,171,070
```

Every carrier was returned twice through the alias. This is not a partial
outage: it is silently doubled data on the single name that every consumer
reads.

### Why it matters more than duplicate counts

`entity-match.json` sets `source_index` to the alias `carriers-000001`, so a
sweep reads whatever the alias resolves to. Three consequences compound:

1. **Mixed analyzers.** After an analyzer change, the two indexes behind the
   alias hold tokens produced by _different_ configurations. The sweep scores a
   mixture of them. This is precisely the silent-wrong-output failure the
   analysis fingerprint work was built to catch, arriving through a door that
   work does not cover.

2. **The fingerprint check inspects an arbitrary index.**
   `phase_entity_match._preflight` does
   `for index_mapping in mapping.body.values(): ...; break` — it takes whichever
   index the mapping response lists first. With two indexes behind the alias
   both the new fingerprint comparison and the pre-existing missing-subfield
   check examine one index chosen by response ordering, not by intent.

3. **Every carrier becomes its own candidate successor.** `CandidateFinder`
   excludes the predecessor via `must_not` on `_id`, but the same carrier in the
   other index is a different `_id`. A duplicated corpus therefore manufactures
   a perfect-scoring pair for every swept predecessor.

The defect predates the address synonym normalization work and is independent
of it. It surfaced there because that was the first change requiring a reload
after an analyzer change.

## Scope

In scope: making index creation swap the alias atomically, in both projects,
for every step that declares one.

Out of scope: retention or deletion of superseded indexes. Nothing here deletes
an index — the old one simply stops answering to the alias. Choosing how long
to keep it, and pruning it, is a separate operational decision that should not
be bundled into a correctness fix.

Also out of scope: changing `_preflight`'s `break`-on-first-index behavior.
Once the alias resolves to exactly one index, that loop is correct. Fixing it
defensively would be adding a guard against a state this spec eliminates.

## Design

Replace the `put_alias` call in `PhaseindexCreate.handle()` with a single
`update_aliases` call carrying one `remove` action per index currently holding
the alias, plus one `add` for the new index.

```python
existing = self.es.options(ignore_status=404).indices.get_alias(name=phase_config.alias)
actions = [
    {"remove": {"index": index_name, "alias": phase_config.alias}}
    for index_name in (existing.body or {})
    if index_name != phase_config.index
]
actions.append({"add": {"index": phase_config.index, "alias": phase_config.alias}})
self.es.indices.update_aliases(actions=actions)
```

Three properties this must have, each load-bearing:

- **One call, not two.** `update_aliases` applies its whole action list
  atomically. Removing first and adding second would leave a window in which the
  alias resolves to nothing, and a concurrent reader would see an index-not-found
  error rather than stale data. A reload is meant to be invisible to readers,
  which is the entire justification for the alias existing.
- **First creation is not a special case.** When the alias does not exist yet,
  `get_alias` 404s, the remove list is empty, and the call degrades to a plain
  add. No branch is needed and none should be written.
- **Re-running the same step on the same day is a no-op.** `{now/d}` resolves to
  the same index name, the `if index_name != phase_config.index` filter drops
  the self-remove, and the add is idempotent. Without that filter the action
  list would remove and re-add the same index in one transaction — which
  Elasticsearch accepts, but which would read as though something changed.

The existing `except BadRequestError` handler around the alias call stays, and
must continue to log a warning rather than abort: an alias failure after a
successful index creation should not discard the loaded data.

### Blast radius

Every step declaring an `alias` in `index-config.json` — eight in
DOT-Commercial (`carriers`, `crashes`, `inspections`, `inspections-per-unit`,
`auth-history`, `out-of-service-orders`, `boc3-agents`, `chameleon-detection`)
and three in CMS-Providers. The behavior change is uniform and is the behavior
the documentation already claims, so no config changes.

Existing clusters carry the accumulated aliases already. The first run after
this lands cleans up whichever step it runs, because the remove list is built
from live state rather than from an assumption that exactly one index holds the
alias. No migration script is needed. An operator who wants to fix it before
reloading can run the equivalent `_aliases` call by hand.

## Testing

- **Unit, no Elasticsearch:** a fake client capturing the `update_aliases`
  action list, asserting (a) first creation with no existing alias produces a
  single `add`, (b) one existing index produces exactly one `remove` plus one
  `add`, (c) two accumulated indexes produce two `remove`s plus one `add`, and
  (d) re-running against the same index name produces an `add` with no
  self-`remove`.
- **Integration against live Elasticsearch,** skipped when unreachable: create
  two indexes in sequence through the phase, then assert `get_alias` resolves to
  exactly one index and a `_count` through the alias equals a `_count` against
  the concrete index. The count equality is the assertion that would have caught
  this defect — an alias-membership check alone can pass while the numbers are
  wrong.
- `.venv/bin/python -m ruff check .` prints `All checks passed!`.

## Documentation

`README.md:77` and the alias depictions in both `DOT-Commercial/README.md`
diagrams and `CMS-Providers/README.md` become true once this lands, so they need
no edit. The Open item recording the defect is removed in the same change that
fixes it.

## Risks

- **A step that legitimately wants a multi-index alias would break.** None
  exists today: every `index-config.json` pairs exactly one index pattern with
  one alias, and every consumer reads the alias expecting one dataset. If a
  fan-out alias is ever wanted, it needs its own config key rather than relying
  on accumulation, which is indistinguishable from the bug.
- **Superseded indexes keep consuming disk** until someone prunes them, and this
  change makes them less visible by removing them from the alias. That trade is
  deliberate — correctness first — but it means retention should be picked up
  soon rather than left indefinitely.
