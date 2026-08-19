"""Structural validation of the config files entitopia itself defines.

Exists because this repository's recurring failure is configuration that parses
and is inert: a renamed key that silently falls back to a default, an analyzer
naming a column that no longer exists, a validation row-cap left switched on in
production. None of those raise, and none of them show up as anything but a
quietly wrong result hours later. A schema turns the first class of them into an
error at startup.

Deliberately does NOT cover the interior of index-mappings.json or
index-settings.json. Those are Elasticsearch's own DSL — owned elsewhere, moving
independently of this project, and already rejected loudly by the cluster, which
this repo made fatal. A schema over them would go stale, start rejecting valid
config, and teach operators that a validation failure is something to work
around. The rule is: schematize what entitopia invented, and let Elasticsearch
reject what Elasticsearch invented.

Returns messages rather than raising per problem. Fixing configuration is
iterative, and a validator that stops at the first error turns a five-mistake
config into five runs.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

SCHEMA_SUFFIX = ".schema.json"


def known_kinds() -> list[str]:
    """Every schema kind on disk, for error messages and for callers iterating."""
    return sorted(p.name[: -len(SCHEMA_SUFFIX)] for p in SCHEMA_DIR.glob("*" + SCHEMA_SUFFIX))


def _load_schema(kind: str) -> dict:
    """Read one schema by kind, raising when the kind is unknown.

    Raises rather than falling back to an empty schema, because an empty schema
    validates everything: a caller with a typo in its kind string would report a
    clean config forever, which is worse than no validation at all — it is
    validation that lies.
    """
    path = SCHEMA_DIR / "{}{}".format(kind, SCHEMA_SUFFIX)
    if not path.exists():
        raise ValueError(
            "no schema for config kind {!r}; known kinds are {}".format(
                kind, ", ".join(known_kinds()) or "(none)"
            )
        )
    with open(path) as handle:
        return json.load(handle)


def _describe(error, source: str) -> str:
    """One jsonschema error as a line naming the file and the dotted key path.

    jsonschema's own str() omits the file entirely and renders the path as a
    deque repr, so an operator reading a failure could not tell which of twelve
    index-configs produced it. The path is joined with dots to match how these
    keys are written in the files and talked about in the docs.
    """
    location = ".".join(str(part) for part in error.absolute_path) or "(root)"
    return "{}: {}: {}".format(source, location, error.message)


def validate_mapping(kind: str, raw: dict, source: str) -> list[str]:
    """Validate an already-parsed config dict. Returns messages, empty if valid.

    Takes a plain dict rather than the SimpleNamespace the rest of the codebase
    uses, because that is what jsonschema validates — and because converting a
    namespace back into a dict would lose nothing except the attribute access,
    while reading config through `file_utils` first would already have discarded
    the "this key is not one we recognize" information this exists to catch.

    Errors are sorted by key path so two runs over the same broken file produce
    the same report in the same order, which is what makes a fix diffable.
    """
    validator = jsonschema.Draft202012Validator(_load_schema(kind))
    return [
        _describe(error, source)
        for error in sorted(
            validator.iter_errors(raw), key=lambda e: list(map(str, e.absolute_path))
        )
    ]


def validate_file(kind: str, path: str) -> list[str]:
    """Validate one config file on disk, reporting unreadable JSON as a finding.

    A missing or malformed file is a validation failure like any other, not an
    exception for the caller to handle separately: reporting it in the same list
    keeps the phase's output one flat list of things to go and fix.
    """
    try:
        with open(path) as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return ["{}: file not found".format(path)]
    except json.JSONDecodeError as e:
        return ["{}: invalid JSON: {}".format(path, e)]
    return validate_mapping(kind, raw, path)
