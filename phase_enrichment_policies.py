import file_utils
import elasticsearch_utils
import logging as logging
import json
from csv_load_utils import CsvLoadUtils
from elasticsearch import NotFoundError, ConflictError, BadRequestError
from elasticsearch import client


class PhaseEnrichmentPolicies:
    def __init__(self, es, project, one_step, project_configs):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_configs
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        phase_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "enrichment-policies.json",
        )
        self.logger.debug("loaded config {}".format(phase_config))

        enrichClient = client.EnrichClient(self.es)

        if phase_config:
            try:
                enrichClient.delete_policy(name=phase_config.name)
            except NotFoundError:
                pass

            elasticsearch_utils.replace_match_indicies_with_now_version(phase_config)
            self.logger.info(
                "Processing policy name {} match {}".format(
                    phase_config.name, phase_config.match
                )
            )
            try:
                match_json = json.dumps(phase_config.match, default=lambda s: vars(s))
                match_dicts = json.loads(match_json)
                r = enrichClient.put_policy(name=phase_config.name, match=match_dicts)
                self.logger.info(
                    "Updated policy {} returned {}".format(phase_config.name, r)
                )
                r = enrichClient.execute_policy(
                    name=phase_config.name, wait_for_completion=True
                )

            except (BadRequestError) as e:
                self.logger.info("Failed to update policy: " + str(e))
