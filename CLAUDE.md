# entitopia — working conventions

## Comments and docstrings: explain why, never what

Every function, class, and module gets a comment describing **why it exists and
what it is used for**. Do not narrate the steps the code takes — the code
already says that, and a step-by-step comment goes stale the moment anyone
edits the body.

Write down the thing a reader cannot recover from the code: the reason this
exists, the caller whose need it serves, the constraint that forced this shape,
and the consequence of getting it wrong.

**Good — states purpose, caller, and the reason for the shape:**

```python
def blended_overlap(a: set[str], b: set[str]) -> float | None:
    """Half jaccard, half containment. None when either set is empty.

    Pure jaccard would punish name abbreviation, which is one of the evasion
    tactics being hunted. Pure containment would treat any subset as a perfect
    match. Blending keeps full overlap ranked above a subset match while still
    scoring abbreviation highly.
    """
```

**Bad — narrates steps the code already shows:**

```python
def blended_overlap(a, b):
    """Checks if either set is empty and returns None. Otherwise computes
    jaccard, then computes containment, then multiplies each by 0.5 and
    adds them together."""
```

Things worth capturing, when they apply:

- Why this exists at all, and who calls it.
- Why it is built this way rather than the obvious alternative, especially when
  the obvious alternative was tried and failed. Name the failure.
- Values that are load-bearing rather than arbitrary, and what breaks if they
  change. `CENTURY_PIVOT = 30` exists because FMCSA registrations reach back to
  the 1970s, so `01-JUN-74` must resolve to 1974 — Java's `yy` pattern would
  make it 2074.
- Distinctions a reader would otherwise flatten. Returning `None` rather than
  `0.0` from a signal means "not evaluable" versus "evaluated, no similarity";
  conflating them penalizes a carrier for missing data.
- Silent failure modes. This codebase's recurring bug is a phase that logs
  success while producing quietly wrong output, so anything guarding against
  that should say so.

Inline comments follow the same rule: reserve them for the non-obvious. A
comment restating the line below it is noise.

## Never name a real entity that the matcher flagged

This project reads public data about real companies and scores them for
suspected fraud. A score is **an analysis, not a finding** — the thresholds are
uncalibrated, the sweep truncates, and a high score routinely reflects a shared
filing agent, a corporate parent, or a placeholder value rather than wrongdoing.
Writing a real company's name next to the phrase "chameleon carrier" in a repo
publishes an unverified accusation about an identifiable business.

So in committed code, comments, docstrings, config, documentation, commit
messages, and PR text: **do not name a flagged entity**. That covers company
names, DOT numbers, addresses, phone numbers, and email addresses belonging to
records the sweep matched.

Replace them with an obviously synthetic placeholder that preserves whatever
made the example worth writing down:

```markdown
Bad: ACME HAULING LLC -> ACME HAULING LLC, +1 day
Good: <CARRIER-A> -> <CARRIER-A> (identical legal name), +1 day
```

The "bad" line above uses an invented name for the same reason — an example of
the rule must not violate it.

The pattern is the reusable part — identical name, same address, one day after
shutdown — and it survives anonymization intact. If an example stops being
useful once the name is removed, the name was doing argumentative work it
should not have been doing.

Four things this does **not** forbid, because none of them accuses anyone:

- Data-quality values that are junk by nature: the literal VIN `GGGG`, the
  phone `(000) 000-0000`, `UNKNOWN`. Record these freely — they are exactly
  what an `ignore_values` list has to name to work.
- Aggregate counts: "an email address shared by 206 carriers" describes a
  distribution, not a company.
- **Entities named because they are being _excluded_.** A filing service, an
  insurance agency, a permit broker, or a corporate parent whose contact
  details sit on hundreds of unrelated carriers is documented as a **source of
  false positives being filtered out** — the opposite of an allegation, and
  unavoidable if an operator is to maintain the ignore list at all. Name the
  shared value and say why it is not identifying.
- **Entities matched only by a duplicate-detection sweep.** A pair in
  `hospital-duplicates` says two records may describe one facility, not that
  anyone did wrong, so real CMS hospital IDs, names and addresses may appear in
  fixtures, tests and docs. That stops holding the moment the surrounding text
  implies wrongdoing; then the rule applies as to any flagged entity.

The test is which side of the filter the name sits on. An entity the matcher
**flagged** must be anonymized; an entity whose identifier the matcher
**ignores** should be named, because the next person maintaining that list
needs to recognize it.

Verified examples against live data are still expected; run them, keep the
measured numbers, and anonymize the identifiers on the way into the repo.

## Python must be free of linter warnings

`ruff` is the linter, configured in `pyproject.toml`. Code is not done until
this prints `All checks passed!`:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff check . --fix     # apply the mechanical fixes
```

The rule set is pinned explicitly rather than inherited from ruff's defaults,
which shift between releases — otherwise a clean checkout stops being clean
after an upgrade.

When a rule fires on something deliberate, do not silence it silently. Either
fix the code, or add an exemption **with a comment saying why**, at the
narrowest scope that works: a targeted `# noqa: RULE` on the line for a
one-off, `per-file-ignores` for a file-wide reason, and a global `ignore` only
when the rule genuinely conflicts with a project-wide decision. Every exemption
currently in `pyproject.toml` carries its reasoning.

This is not cosmetic. The first run against this codebase found a real bug that
had been sitting in `phase_providers/phase_dispatcher.py` — an `else` branch
referencing an undefined `logger` and a nonexistent attribute, so an
unrecognized phase name crashed instead of logging a clear error.

## Everything runs from `.venv`

Never invoke bare `python3` or `pip3`. Use `.venv/bin/python` for every command
— tests, scripts, and `execute_project.py` alike. The host machine's Python
must not affect this project.

The project runs on Python 3.15 (`.python-version`). Build the venv from it
with `PYTHON=python3.15 bash dependencies.sh`; a `.venv` built on an older
Python is refused by that script, so delete it and rebuild.

```bash
PYTHON=python3.15 bash dependencies.sh   # creates .venv, installs into it
.venv/bin/python -m pytest
.venv/bin/python execute_project.py --project=DOT-Commercial
```

`requirements.txt` pins every version, direct and transitive. Add dependencies
there, pinned — never `pip install` ad hoc into the venv.

## Guards live in tests; keep them running

Most fixes in this repo are guards against a silent failure: a phase that
logged success while writing quietly wrong data. Each guard is pinned by a
regression test whose module docstring tells the incident behind it
(`tests/test_rerun_guards.py`, `tests/test_alias_swap.py`, and so on). The
README's data-loading hazards and the `run-entitopia` re-run table each name
the test that guards them, or say that nothing does.

- **Run the whole suite before every commit**, not just the file you changed:
  `.venv/bin/python -m pytest`. A change in one phase routinely breaks a guard
  in another. The `verify` workflow runs ruff and pytest on every pull request
  (and prettier on the Markdown and JSON it touches). It has no Elasticsearch,
  so tests that need the cluster or a local sweep baseline skip there; run
  with the cluster up locally.
- **Say so in the PR when a change touches a guard.** Deleting, skipping or
  weakening a test, loosening an assertion, or changing a test's fake (a fake
  that answers differently can make a guard pass for the wrong reason) each
  need a line in the PR description naming the guard and why it still holds.
- **A new guard gets a test and an entry.** Write the test first, with a
  module docstring stating the failure it prevents, and name it in the README
  hazard or re-run table row it covers.

## Elasticsearch client calls

Pass explicit keyword arguments, never `body=`. `body` is deprecated in
elasticsearch-py 8.x and removed for several APIs in the pinned 9.4.1.

```python
es.search(index=idx, size=100, query={...}, track_total_hits=False)
es.mtermvectors(index=idx, ids=[...], fields=[...])   # flat, no `parameters` wrapper
```

## Configuration objects are `SimpleNamespace`, not dicts

All JSON config loads through `file_utils.load_from_file`, which uses
`object_hook=lambda d: SimpleNamespace(**d)`. Use attribute access
(`config.signals[0].weight`) and `getattr(obj, "key", default)` for optional
keys.

## Local Elasticsearch

`docker/compose.yml` runs the pinned 9.4.1 server with `analysis-icu` and
`analysis-phonetic`, which the analyzers require — index creation fails
outright without them.

```bash
docker compose -f docker/compose.yml up -d --build
```

The cluster is always at **`http://localhost:9200`**, unauthenticated. Reading
it is always appropriate, though the committed `.claude/settings.json` does not
pre-approve `curl`: a URL-prefix rule would also approve a write whose `-X
DELETE` follows the URL, so read approvals live in each person's
`settings.local.json`. Query it
freely to check mappings, counts and aliases rather than asking or guessing;
several defects in this repo were only visible by looking at the live index
(`tow_away` mapped as `text` so `{"term": {"tow_away": "Y"}}` matched zero
documents, and `dot_number` typed `long` on crashes but `keyword` elsewhere).
A claim about what the data contains should be checked against the cluster
before it is written down.

Writes are a different matter: creating, deleting or reindexing against a
loaded cluster destroys work that takes hours to rebuild, so confirm those
first even though the allowlist does not distinguish them.

Before re-running a step, sweep or measurement script against a loaded
cluster, read the re-run table in the `run-entitopia` skill ("Re-running on a
loaded cluster"). It lists what each re-run overwrites, hides or mixes —
same-day index generations, aliases moving off populated indexes, baselines
and review verdicts overwritten — and the safe move for each.

The container runtime is Colima, not Docker Desktop. Start the VM first, sized
for the 6 GB heap `docker/compose.yml` sets: `colima start --cpu 6 --memory 12`.
Homebrew's `docker` lives in `/opt/homebrew/bin`, which a non-login shell may
not have on PATH (`export PATH="/opt/homebrew/bin:$PATH"`). A build failing with
`docker-credential-desktop: executable file not found` means
`~/.docker/config.json` still names Docker Desktop's `credsStore`; remove that
key.
