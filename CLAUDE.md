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

## Everything runs from `.venv`

Never invoke bare `python3` or `pip3`. Use `.venv/bin/python` for every command
— tests, scripts, and `execute_project.py` alike. The host machine's Python
must not affect this project.

```bash
bash dependencies.sh                     # creates .venv, installs into it
.venv/bin/python -m pytest
.venv/bin/python execute_project.py --project=DOT-Commercial
```

`requirements.txt` pins every version, direct and transitive. Add dependencies
there, pinned — never `pip install` ad hoc into the venv.

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

If the build fails with `docker-credential-desktop: executable file not found`,
prepend Docker Desktop's bin directory to PATH for the command:
`export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`.
