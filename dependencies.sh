#!/usr/bin/env bash
# Create the project virtualenv (.venv) if missing and install requirements
# into it. Everything in this project runs from .venv so the host machine's
# Python installation cannot affect it.
#
# Usage:
#     bash dependencies.sh
#
# Then run project commands through the venv interpreter, e.g.
#     .venv/bin/python execute_project.py --project=DOT-Commercial
#     .venv/bin/python -m pytest
#
# Or activate it for the shell session:
#     source .venv/bin/activate
#
# requirements.txt pins every version, direct and transitive. To regenerate it
# after changing a dependency:
#     .venv/bin/python -m pip list --format=freeze \
#       | grep -viE '^(pip|setuptools|wheel)=' > requirements.txt
# then restore the header comments and the direct/transitive split by hand.

set -euo pipefail

VENV_DIR=".venv"
MINIMUM="3.11"

# Interpreter used only to CREATE the venv. Once .venv exists we use its own
# interpreter and never consult the system Python again.
BOOTSTRAP_PYTHON="${PYTHON:-python3}"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    if ! command -v "${BOOTSTRAP_PYTHON}" >/dev/null 2>&1; then
        echo "Cannot find ${BOOTSTRAP_PYTHON} to create ${VENV_DIR}." >&2
        echo "Set PYTHON=/path/to/python3.11 and re-run." >&2
        exit 1
    fi

    "${BOOTSTRAP_PYTHON}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
        echo "entitopia requires Python ${MINIMUM} or higher (found $("${BOOTSTRAP_PYTHON}" --version))" >&2
        echo "Set PYTHON=/path/to/python3.11 and re-run." >&2
        exit 1
    }

    echo "Creating ${VENV_DIR} with $("${BOOTSTRAP_PYTHON}" --version)"
    "${BOOTSTRAP_PYTHON}" -m venv "${VENV_DIR}"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"

# Re-check against the venv's own interpreter. A .venv built long ago by a
# since-upgraded Python would otherwise slip past the bootstrap check above.
"${VENV_PYTHON}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "${VENV_DIR} runs $("${VENV_PYTHON}" --version), but entitopia requires ${MINIMUM}+." >&2
    echo "Delete ${VENV_DIR} and re-run this script to rebuild it." >&2
    exit 1
}

"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -r requirements.txt

echo
echo "Done. ${VENV_DIR} runs $("${VENV_PYTHON}" --version)"
echo "Run project commands with ${VENV_PYTHON}, e.g."
echo "    ${VENV_PYTHON} execute_project.py --project=DOT-Commercial"
echo "    ${VENV_PYTHON} -m pytest"
