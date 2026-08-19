"""Scaffold a new project directory from its CSVs.

Removes the typing, not the judgement. Directory layout, per-column mappings,
aliases, index-name stamps and phase lists are mechanical, and getting them
subtly wrong is a slow way to learn what this framework expects. Which column
is the document key, whether a field is worth matching on, what a signal is
worth — none of that is mechanical, and this refuses to guess at any of it.

    .venv/bin/python scripts/new_project.py --project Example-Project \\
        --csv hospitals=path/to/Hospital_General_Information.csv

**What it generates does not run.** Every decision it refuses is written as a
`__TODO_*__` key, which the `additionalProperties: false` in every schema
rejects, so `validate` fails until a human has resolved each one. That is
deliberate: a generated project that validated clean would look finished while
carrying placeholders into a sweep.

A marker is a KEY rather than a value for the same reason. `"id_field": "TODO"`
validates cleanly — `id_field` is typed `string` — so a scaffolded project
would happily key every document on the literal string TODO.

An `entity-match.json` stub is emitted even though that file is almost entirely
judgement and the schema now documents its shape. Its value here is not the
shape but the forcing function: a project carrying no entity-match validates
clean and looks done, while one carrying a marker-filled stub cannot pass
validation until somebody has made the decisions.

No `index-settings.json` is generated. Settings exist almost entirely to
declare analyzers, and analyzers are worth configuring only if the dataset
carries identity fields to match on — which is the judgement this refuses. An
empty settings file would be scaffolding that looks like a decision.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import scaffold

_PROFILER = Path(__file__).resolve().parent / "profile_dataset.py"

# Rows to profile per CSV by default. Enough for type inference to be stable
# without reading several million rows to decide a mapping the operator will
# review anyway; --rows raises it when a column's shape is in doubt.
DEFAULT_PROFILE_ROWS = 50_000


def _load_profiler():
    """Load the profiler by path — scripts/ is tools, not an importable package."""
    spec = importlib.util.spec_from_file_location("profile_dataset", _PROFILER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiler = _load_profiler()


def _unique_single_columns(fieldnames, columns, rows):
    """Columns whose values were distinct on every profiled row.

    Offered as *candidates* in the marker message, never chosen. Uniqueness
    over a sample is not uniqueness over the file, and the profiler has a
    --key mode that tests a candidate properly. Suggesting without deciding is
    the line this whole script sits on.
    """
    found = []
    for name in fieldnames:
        column = columns[name]
        if column.distinct_capped or not column.populated:
            continue
        if column.blank == 0 and len(column.values) == rows:
            found.append(name)
    return found


def index_config(step, csv_name, fieldnames, columns, rows):
    """Loader settings for one dataset, with the key left unresolved.

    num_rows is null rather than a sample size: a committed non-null value is
    the validation-sample-left-on-in-production hazard, where a "full" load
    silently truncates.
    """
    config = {
        "alias": "{}-000001".format(step),
        # The {now/d} stamp means a reload lands in a fresh index rather than
        # overwriting the same day's earlier run.
        "index": "{}-{{now/d}}-000001".format(step),
        "source": csv_name,
        "num_rows": None,
        "skip_rows": 0,
    }
    candidates = _unique_single_columns(fieldnames, columns, rows)
    suggestion = (
        "Candidates unique across {:,} profiled rows: {}. ".format(
            rows, ", ".join(candidates)
        )
        if candidates
        else "No single column was unique across {:,} profiled rows, so this "
        "likely needs a composite key. ".format(rows)
    )
    key, message = scaffold.marker(
        "choose_id_field",
        suggestion
        + "Verify before committing to one: .venv/bin/python "
        "scripts/profile_dataset.py <csv> --key col_a --key col_b. Without an "
        "id_field, every rerun duplicates every row.",
    )
    config[key] = message
    return config


def index_mappings(step, fieldnames, columns):
    return {
        "index": "{}-{{now/d}}-000001".format(step),
        "mappings": {"properties": scaffold.mapping_properties(fieldnames, columns)},
    }


def entity_match_stub(steps):
    """A stub that cannot run, for a project that may not want matching at all.

    Deliberately minimal. The schema documents the full shape and an editor can
    complete against it, so repeating every optional block here would be noise;
    what this contributes is that the project fails validation until somebody
    decides whether it is matching anything.
    """
    entity_key, entity_message = scaffold.marker(
        "choose_entity_key",
        "The column this project calls an entity's identity. Usually the same "
        "column as the source index's id_field.",
    )
    signals_key, signals_message = scaffold.marker(
        "choose_signals",
        "Which fields become scored evidence, at what weight, and which may "
        "seed retrieval. Profile first: a field that looks like a strong "
        "fingerprint can be worthless. See docs/adding-a-dataset.md, and "
        "schema/entity-match.schema.json for the shape.",
    )
    return {
        "source_index": "{}-000001".format(steps[0]),
        "entity": {entity_key: entity_message},
        "population": {"mode": "all-entities", "sort_field": "CHANGE-ME"},
        "candidates": {"max_candidates": 100, "seed_signals": []},
        signals_key: signals_message,
        "scoring": {"min_total_score": 0.5, "min_signals": 1},
    }


def project_configuration(steps):
    return {
        "steps": [
            {
                "name": step,
                # validate runs first so a config mistake stops the run before
                # it creates indexes or loads several million rows.
                "phases": ["validate", "index-create", "index-map", "index-populate"],
            }
            for step in steps
        ],
        "all_phases": [
            "validate",
            "index-create",
            "index-map",
            "enrichment-policies",
            "pipelines",
            "index-populate",
            "entity-match",
        ],
        "configurationDir": "configuration",
        "dataDir": "data",
        "logLevel": "INFO",
    }


def readme(project, datasets):
    """A project README carrying what was measured, not what was assumed.

    Records the profiled counts and the columns that need a decision, because
    the expensive thing to rediscover later is not the layout but the
    measurement that justified a choice.
    """
    lines = [
        "# {}".format(project),
        "",
        "Scaffolded by `scripts/new_project.py`. **This project does not run yet.**",
        "",
        "Every decision the generator refused is a `__TODO_*__` key in the config,",
        "and every schema rejects unknown keys — so `validate` fails until each one",
        "is resolved:",
        "",
        "```bash",
        ".venv/bin/python execute_project.py --project={} --phase=validate".format(
            project
        ),
        "```",
        "",
        "## Datasets",
        "",
        "| Step | Source | Columns | Rows profiled |",
        "| ---- | ------ | ------- | ------------- |",
    ]
    for step, info in datasets.items():
        lines.append(
            "| `{}` | `{}` | {} | {:,} |".format(
                step, info["csv"], len(info["fieldnames"]), info["rows"]
            )
        )

    lines += [
        "",
        "## Decisions the generator refused",
        "",
        "- **Document key** — the profiler tests a candidate key; it cannot choose one.",
        "  A missing `id_field` duplicates every row on every rerun.",
        "- **Analyzers** — worth configuring only if this dataset carries identity",
        "  fields to match on. No `index-settings.json` was generated for that reason.",
        "- **Signals and weights** — measure before configuring. A field can look like",
        "  a strong fingerprint and be worthless; see `docs/adding-a-dataset.md`.",
        "",
    ]

    dated = {
        step: info["dated"] for step, info in datasets.items() if info["dated"]
    }
    if dated:
        lines += [
            "## Date-shaped columns, mapped `keyword`",
            "",
            "Mapped `keyword` rather than `date` on purpose: a single malformed value",
            "in a `date`-mapped field fails the **whole document**, not just the field.",
            "Decide per column whether to keep it `keyword` and parse client-side, or",
            "convert to ISO in an ingest pipeline and map it `date`.",
            "",
        ]
        for step, names in dated.items():
            lines.append("- `{}`: {}".format(step, ", ".join("`{}`".format(n) for n in names)))
        lines.append("")

    identity = {
        step: info["identity"] for step, info in datasets.items() if info["identity"]
    }
    if identity:
        lines += [
            "## Columns that may want analyzers",
            "",
            "Everything is mapped `keyword`, or `text` with a `keyword` subfield where",
            "the values are varied enough to need it. **Nothing is analyzed**, because",
            "analyzers are only worth configuring for fields you intend to match on,",
            "and that is a decision the generator refuses to make.",
            "",
            "Measured against a hand-written mapping for the same extract, this is where",
            "generated and hand-written output diverge most: a human writing config for",
            "matching maps these `text` and hangs `clean`, `phonetic` or `tokens`",
            "subfields off them. A `keyword` base can carry analyzed subfields too, so",
            "nothing here is wrong — but if you are matching on one of these, it",
            "probably wants analysis.",
            "",
        ]
        for step, names in identity.items():
            lines.append(
                "- `{}`: {}".format(step, ", ".join("`{}`".format(n) for n in names))
            )
        lines.append("")

    lines += [
        "## Next",
        "",
        "1. Put each source CSV in `data/<step>/`.",
        "1. Resolve every `__TODO_*__` marker.",
        "1. Run the validate phase until it is clean.",
        "1. Load, then verify against the cluster rather than trusting the load.",
        "",
    ]
    return "\n".join(lines)


def _write(path, content, written):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content)
    else:
        path.write_text(json.dumps(content, indent=2) + "\n")
    written.append(path)


def generate(project_dir: Path, csvs: dict, rows_cap: int) -> tuple[list, list]:
    """Write the project tree. Returns (files written, markers placed)."""
    written = []
    datasets = {}

    for step, csv_path in csvs.items():
        fieldnames, columns, rows = profiler.profile(str(csv_path), max_rows=rows_cap)
        datasets[step] = {
            "csv": Path(csv_path).name,
            "fieldnames": fieldnames,
            "rows": rows,
            "dated": scaffold.date_shaped_columns(fieldnames, columns),
            "identity": scaffold.candidate_identity_columns(fieldnames, columns),
        }
        step_dir = project_dir / "configuration" / step
        _write(
            step_dir / "index-config.json",
            index_config(step, Path(csv_path).name, fieldnames, columns, rows),
            written,
        )
        _write(
            step_dir / "index-mappings.json",
            index_mappings(step, fieldnames, columns),
            written,
        )
        (project_dir / "data" / step).mkdir(parents=True, exist_ok=True)

    steps = list(csvs)
    _write(project_dir / "configuration.json", project_configuration(steps), written)
    _write(
        project_dir / "configuration" / "entity-match" / "entity-match.json",
        entity_match_stub(steps),
        written,
    )
    _write(project_dir / "README.md", readme(project_dir.name, datasets), written)

    markers = []
    for path in written:
        if path.suffix != ".json":
            continue
        for key in _markers_in(json.loads(path.read_text())):
            markers.append((path, key))
    return written, markers


def _markers_in(value, path=""):
    """Every marker key anywhere in a config document, with its location."""
    if isinstance(value, dict):
        for key, child in value.items():
            here = "{}.{}".format(path, key) if path else key
            if scaffold.is_marker(key):
                yield here
            else:
                yield from _markers_in(child, here)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _markers_in(child, "{}[{}]".format(path, index))


def _parse_csv_argument(raw):
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            "expected step=path, got {!r} — the step name becomes the "
            "configuration subdirectory".format(raw)
        )
    step, path = raw.split("=", 1)
    if not Path(path).exists():
        raise argparse.ArgumentTypeError("no such file: {}".format(path))
    return step, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Project directory to create")
    parser.add_argument(
        "--csv",
        required=True,
        action="append",
        type=_parse_csv_argument,
        metavar="STEP=PATH",
        help="Repeatable. The step name becomes the configuration subdirectory.",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_PROFILE_ROWS)
    parser.add_argument(
        "--force", action="store_true", help="Write into an existing directory"
    )
    args = parser.parse_args()

    project_dir = Path(args.project)
    if project_dir.exists() and not args.force:
        sys.exit(
            "{} already exists; pass --force to write into it".format(project_dir)
        )

    written, markers = generate(project_dir, dict(args.csv), args.rows)

    print("Wrote {} file(s):".format(len(written)))
    for path in written:
        print("  {}".format(path))

    print("\n{} decision(s) left for you — validate will fail until each is "
          "resolved:".format(len(markers)))
    for path, key in markers:
        print("  {}: {}".format(path, key))

    print(
        "\nNext: .venv/bin/python execute_project.py --project={} "
        "--phase=validate".format(project_dir.name)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
