"""Every config file both shipped projects run on must validate.

These files have been driving real loads — 5.6M rows in one project, and in the
other the sweep whose output the compatibility gate certified. So a failure here
means the schema is wrong, not the config. That direction matters: a schema
written from the documentation rather than from the data will quietly reject
working configuration, and the first instinct on seeing it fail would be to
change the config.
"""

from pathlib import Path

import pytest

from utils import config_schema

_ROOT = Path(__file__).parent.parent

_KIND_BY_FILENAME = {
    "configuration.json": "configuration",
    "index-config.json": "index-config",
    "index-mappings.json": "index-mappings",
    "index-settings.json": "index-settings",
    "pipelines.json": "pipelines",
    "enrichment-policies.json": "enrichment-policies",
    "entity-match.json": "entity-match",
}

_PROJECTS = ("DOT-Commercial", "CMS-Providers")


def _shipped_config_files():
    for project in _PROJECTS:
        yield _ROOT / project / "configuration.json"
        for path in sorted((_ROOT / project / "configuration").rglob("*.json")):
            if path.name in _KIND_BY_FILENAME:
                yield path


_SHIPPED = list(_shipped_config_files())


@pytest.mark.parametrize("path", _SHIPPED, ids=lambda p: str(p.relative_to(_ROOT)))
def test_shipped_config_validates(path):
    kind = _KIND_BY_FILENAME[path.name]
    if kind == "entity-match":
        pytest.xfail("entity-match schema arrives in Task 3 of this plan")
    assert config_schema.validate_file(kind, str(path)) == []


def test_the_sweep_reaches_every_shipped_file():
    # A glob that silently matched nothing would make this whole file vacuous —
    # it would report a row of passes having checked zero bytes.
    assert len(_SHIPPED) >= 20
    assert any(p.name == "entity-match.json" for p in _SHIPPED)
    assert any(p.name == "enrichment-policies.json" for p in _SHIPPED)
    for project in _PROJECTS:
        assert any(project in str(p) for p in _SHIPPED)


def test_every_schema_on_disk_is_reachable_by_some_filename():
    # A schema nothing maps to is a schema nothing validates against, which
    # reads as coverage while providing none.
    mapped = set(_KIND_BY_FILENAME.values())
    for kind in config_schema.known_kinds():
        assert kind in mapped, "schema/{}.schema.json is never used".format(kind)
