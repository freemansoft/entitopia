"""validate phase: refuse to start a run whose configuration is already wrong.

This repository's recurring failure is configuration that parses and is inert.
Every hazard in the top-level README is that shape — an analyzer naming a column
the source renamed, a validation row-cap left switched on in production, an
enrichment policy pointing at an index days superseded, a term query against an
analyzed field. None of them raise. Each produces a run that logs success and
output that is quietly wrong, discovered hours later or not at all.

The three tiers are ordered by cost and run cheapest first:

    1. schema      — structure, and unknown keys, which no other tier can see
    2. coherence   — facts that live in two places at once
    3. liveness    — what the config claims about the index, asked of the index

A later tier does not run when an earlier one failed. Tier 3 asks the cluster
about field paths a tier-1 failure may mean are garbage, so running it anyway
would bury the real problem under a page of consequences of it.

The phase raises. The repo's rule is that a phase raises when it cannot fix a
problem, and this one never can — every finding here needs a human to edit a
file.
"""

import json
import logging

# Config is read with json.load rather than through file_utils, which parses
# into SimpleNamespace. Schema validation needs plain dicts: a namespace has
# already discarded the "this key is not one we recognize" information that
# additionalProperties exists to catch.
from utils import config_coherence, config_liveness, config_schema

# Config kinds this phase validates for a step, and the filename each lives in.
# index-mappings and index-settings are envelope-checked only; what is inside
# them is Elasticsearch's own DSL, which the cluster rejects loudly on its own.
STEP_CONFIG_FILES = (
    ("index-config", "index-config.json"),
    ("index-mappings", "index-mappings.json"),
    ("index-settings", "index-settings.json"),
    ("pipelines", "pipelines.json"),
    ("enrichment-policies", "enrichment-policies.json"),
    ("entity-match", "entity-match.json"),
)

ENTITY_MATCH_FILE = "entity-match.json"


class ConfigurationInvalid(Exception):
    """Raised when validation found anything. Carries every finding, not the first.

    A distinct exception type rather than ValueError so a caller can tell a
    configuration problem from a bug in the validator itself.
    """


class PhaseValidate:
    """Runs the three validation tiers over one step's configuration."""

    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def _step_path(self, filename):
        return "{}/{}/{}/{}".format(
            self.project, self.project_config.configurationDir, self.one_step, filename
        )

    def _present_step_files(self):
        """(kind, path, parsed) for each config file this step actually ships.

        A step declares its shape by which files it carries — an ingestion-setup
        step has pipelines and no index-config — so an absent file is normal and
        not a finding. A file that exists and will not parse IS a finding, and
        is returned with `None` for the caller to report.
        """
        for kind, filename in STEP_CONFIG_FILES:
            path = self._step_path(filename)
            try:
                with open(path) as handle:
                    yield kind, path, json.load(handle)
            except FileNotFoundError:
                continue
            except json.JSONDecodeError:
                yield kind, path, None

    def _tier_schema(self, present):
        findings = []
        for kind, path, raw in present:
            if raw is None:
                findings.extend(config_schema.validate_file(kind, path))
                continue
            findings.extend(config_schema.validate_mapping(kind, raw, path))
        return findings

    def _tier_coherence(self, present):
        return [
            message
            for kind, path, raw in present
            if kind == "entity-match" and raw is not None
            for message in config_coherence.check(raw, path)
        ]

    def _tier_liveness(self, present):
        return [
            message
            for kind, path, raw in present
            if kind == "entity-match" and raw is not None
            for message in config_liveness.check(self.es, raw, path)
        ]

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        present = list(self._present_step_files())
        if not present:
            self.logger.warning(
                "No configuration files found for step {}; nothing to validate".format(
                    self.one_step
                )
            )
            return

        for tier_name, tier in (
            ("schema", self._tier_schema),
            ("coherence", self._tier_coherence),
            ("liveness", self._tier_liveness),
        ):
            findings = tier(present)
            if findings:
                for message in findings:
                    self.logger.error("%s", message)
                raise ConfigurationInvalid(
                    "{} configuration problem(s) in step {} at the {} tier; "
                    "later tiers were not run because their findings would be "
                    "consequences of these".format(
                        len(findings), self.one_step, tier_name
                    )
                )

        self.logger.info(
            "Validated {} configuration file(s) for step {}: schema, coherence "
            "and live-index checks all clean".format(len(present), self.one_step)
        )
