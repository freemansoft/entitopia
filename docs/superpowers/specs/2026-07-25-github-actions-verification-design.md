# GitHub Actions Verification — Design

## Purpose

entitopia currently has no CI. This adds a PR-triggered GitHub Actions workflow that performs static verification (no test suite, no Elasticsearch dependency) to catch basic problems before merge.

## Scope

Static checks only. No unit or integration tests are added as part of this work — the codebase has no test suite yet, and adding one is a separate future effort. This design covers syntax/compile validation, linting, format checking, type checking, and dependency vulnerability scanning.

## Architecture

A single workflow file, `.github/workflows/verify.yml`, triggered on `pull_request` events targeting `main`. It defines four independent jobs that run in parallel:

- `import-check`
- `lint`
- `typecheck`
- `audit`

Each job checks out the repo, sets up Python 3.12 (matching `.python-version`) with pip caching, installs only what it needs, and runs its check. Jobs do not depend on each other.

## Jobs

### `import-check` (blocking)

Runs `python -m compileall -q .` to compile every `.py` file in the repo. Catches syntax errors and basic breakage. Requires no dependencies to be installed and no Elasticsearch cluster.

### `lint` (blocking)

Installs Ruff and runs:
- `ruff check .` (lint)
- `ruff format --check .` (format)

No `pyproject.toml` config is added initially — Ruff's default rule set (`E4`, `E7`, `E9`, `F`) catches real errors (unused imports, undefined names, syntax issues) without imposing undecided style choices.

### `typecheck` (non-blocking)

Installs mypy and runs `mypy .` with default (loose) settings. The codebase has no type hints yet, so this is expected to report issues. The job is allowed to genuinely fail — it is intentionally left out of branch protection's required checks rather than forced green via `continue-on-error`, so its status remains honest while not gating merges.

### `audit` (non-blocking)

Installs `pip-audit` and runs it against `requirements.txt` to surface known-vulnerable dependency versions. Same non-blocking approach as `typecheck`: real pass/fail shown, not required for merge.

## Tooling notes

- None of these tools (`ruff`, `mypy`, `pip-audit`) are added to `requirements.txt`, which is the project's runtime dependency list. Each job installs its own tool directly with a loose version pin for reproducibility.
- Python version: single version (3.12), matching `.python-version`. No matrix across 3.11/3.12.

## Branch protection (manual follow-up, outside this workflow file)

Marking `import-check` and `lint` as merge-blocking requires configuring GitHub branch protection on `main` to list those two job names as required status checks. This is a repository setting, not something the workflow YAML alone controls, and is called out here as a manual step to take after the workflow is merged (via the GitHub UI or `gh api`) — not part of the implementation plan itself.

## Validation

After the workflow file is added, open a throwaway PR (e.g., a trivial whitespace change) and confirm:
- All four jobs appear in the PR checks list
- `import-check` and `lint` pass
- `typecheck` and `audit` run and report their real status without blocking the PR

This is the acceptance bar for the work being complete.

## Explicitly out of scope

- Any unit or integration tests
- Spinning up Elasticsearch in CI
- Automatic triggering on direct pushes to `main` (PR-only for now)
- Enforcing `typecheck` or `audit` as required checks
