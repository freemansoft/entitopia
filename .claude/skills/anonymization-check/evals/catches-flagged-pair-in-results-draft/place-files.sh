#!/usr/bin/env bash
# Builds draft.md from the live cluster rather than copying a fixture: the
# draft has to name a real flagged carrier, and that must never be committed.
set -eu
repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
"$repo/.venv/bin/python" "$(dirname "$0")/make_draft.py"
