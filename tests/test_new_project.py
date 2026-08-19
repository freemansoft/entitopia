"""The scaffold generator, and the forcing function it exists for.

The central pair of tests is that a freshly generated project FAILS validation,
and that resolving every marker is the only thing needed to make it pass. Both
halves matter and they fail differently: a scaffold that validates clean would
look finished while carrying placeholders into a sweep, and one that still
fails after every marker is resolved would send an operator hunting for a
defect in generated output instead of writing their config.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from utils import config_schema

_SCRIPT = Path(__file__).parent.parent / "scripts" / "new_project.py"


def _load():
    spec = importlib.util.spec_from_file_location("new_project", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


new_project = _load()

CSV = """Facility ID,Facility Name,City/Town,State,ZIP Code,Certification Date
010001,EXAMPLE REGIONAL MEDICAL CENTER,SPRINGFIELD,AL,36301,2021-01-04
010005,EXAMPLE VALLEY HOSPITAL,SHELBYVILLE,AL,00602,2019-06-30
010006,EXAMPLE MEMORIAL HOSPITAL,OGDENVILLE,AL,35630,2020-12-31
010007,EXAMPLE COMMUNITY HOSPITAL,NORTH HAVERBROOK,AL,36467,2018-03-15
"""


@pytest.fixture
def generated(tmp_path):
    csv_path = tmp_path / "hospitals.csv"
    csv_path.write_text(CSV)
    project = tmp_path / "Example-Project"
    written, markers = new_project.generate(
        project, {"hospitals": str(csv_path)}, rows_cap=None
    )
    return project, written, markers


def test_it_writes_the_expected_tree(generated):
    project, _, _ = generated
    assert (project / "configuration.json").exists()
    assert (project / "configuration" / "hospitals" / "index-config.json").exists()
    assert (project / "configuration" / "hospitals" / "index-mappings.json").exists()
    assert (project / "README.md").exists()
    # The data directory is created empty so it is obvious where the CSV goes.
    assert (project / "data" / "hospitals").is_dir()


def test_no_index_settings_is_generated(generated):
    # Deliberate: settings exist to declare analyzers, and analyzers are worth
    # configuring only if the dataset carries identity fields to match on --
    # a judgement the generator refuses. An empty file would look like a
    # decision that was made.
    project, _, _ = generated
    assert not (project / "configuration" / "hospitals" / "index-settings.json").exists()


def test_validate_runs_before_anything_that_creates_or_loads(generated):
    project, _, _ = generated
    config = json.loads((project / "configuration.json").read_text())
    for step in config["steps"]:
        assert step["phases"][0] == "validate"


def test_the_index_name_carries_a_now_stamp(generated):
    # Without it a reload lands in the same index as the same day's earlier
    # run rather than a fresh one.
    project, _, _ = generated
    config = json.loads(
        (project / "configuration" / "hospitals" / "index-config.json").read_text()
    )
    assert "{now/d}" in config["index"]


def test_a_leading_zero_column_is_not_mapped_numeric(generated):
    # ZIP Code carries 00602 in the fixture. A numeric mapping destroys the
    # padding, which is a measured incident in this repo.
    project, _, _ = generated
    mappings = json.loads(
        (project / "configuration" / "hospitals" / "index-mappings.json").read_text()
    )
    assert mappings["mappings"]["properties"]["ZIP Code"]["type"] == "keyword"


def test_a_date_column_is_not_mapped_date(generated):
    project, _, _ = generated
    mappings = json.loads(
        (project / "configuration" / "hospitals" / "index-mappings.json").read_text()
    )
    assert mappings["mappings"]["properties"]["Certification Date"]["type"] == "keyword"


def test_column_names_with_punctuation_survive_verbatim(generated):
    project, _, _ = generated
    mappings = json.loads(
        (project / "configuration" / "hospitals" / "index-mappings.json").read_text()
    )
    assert "City/Town" in mappings["mappings"]["properties"]
    assert "ZIP Code" in mappings["mappings"]["properties"]


def test_markers_are_reported_so_they_can_be_worked_through(generated):
    _, _, markers = generated
    assert markers, "a scaffold with no markers has decided something it should not"
    assert any("choose_id_field" in key for _, key in markers)
    assert any("choose_signals" in key for _, key in markers)


# --- the forcing function ---------------------------------------------------


def test_a_freshly_scaffolded_index_config_fails_validation(generated):
    """A scaffold that validates clean would defeat the whole point.

    The markers are the operator's work queue. If validation passes with them
    in place, a project can be swept with a placeholder as its document key
    and nothing will have said so.
    """
    project, _, _ = generated
    path = project / "configuration" / "hospitals" / "index-config.json"
    errors = config_schema.validate_file("index-config", str(path))
    assert errors
    assert any("__TODO_" in e for e in errors)


def test_a_freshly_scaffolded_entity_match_fails_validation(generated):
    project, _, _ = generated
    path = project / "configuration" / "entity-match" / "entity-match.json"
    errors = config_schema.validate_file("entity-match", str(path))
    assert errors
    assert any("__TODO_" in e for e in errors)


def test_resolving_every_marker_is_all_that_blocks_index_config(generated):
    """The other half: markers must be the ONLY thing blocking validation.

    A scaffold that still fails once every marker is resolved would send an
    operator hunting for a defect in generated output rather than writing
    their own config.
    """
    project, _, _ = generated
    path = project / "configuration" / "hospitals" / "index-config.json"
    config = json.loads(path.read_text())
    resolved = {k: v for k, v in config.items() if not k.startswith("__TODO_")}
    resolved["id_field"] = "Facility ID"
    assert config_schema.validate_mapping("index-config", resolved, str(path)) == []


def test_resolving_every_marker_is_all_that_blocks_entity_match(generated):
    project, _, _ = generated
    path = project / "configuration" / "entity-match" / "entity-match.json"
    config = json.loads(path.read_text())
    resolved = {k: v for k, v in config.items() if not k.startswith("__TODO_")}
    resolved["entity"] = {"key": "Facility ID"}
    resolved["population"]["sort_field"] = "Facility ID"
    resolved["signals"] = [
        {
            "type": "name-phonetic",
            "weight": 0.5,
            "fields": ["Facility Name"],
            "subfield": "phonetic",
        }
    ]
    resolved["candidates"]["seed_signals"] = ["name-phonetic"]
    assert config_schema.validate_mapping("entity-match", resolved, str(path)) == []


def test_the_generated_project_configuration_already_validates(generated):
    # configuration.json is entirely mechanical -- steps, phases, directories --
    # so there is nothing here for a human to decide and it must validate as
    # generated. A marker here would be the generator refusing a decision it is
    # perfectly able to make.
    project, _, _ = generated
    assert config_schema.validate_file(
        "configuration", str(project / "configuration.json")
    ) == []


def test_the_generated_mappings_already_validate(generated):
    project, _, _ = generated
    path = project / "configuration" / "hospitals" / "index-mappings.json"
    assert config_schema.validate_file("index-mappings", str(path)) == []
