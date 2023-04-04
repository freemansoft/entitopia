import file_utils
import elasticsearch_utils
import logging as logging
import json
from csv_load_utils import CsvLoadUtils
from elasticsearch import NotFoundError, ConflictError, BadRequestError
from elasticsearch import client


class PhaseindexCreate:
    def __init__(self, es, project, one_step, project_configs):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_configs
        self.logger = logging.getLogger(__name__)

    def get_index_settings(self):
        index_setting_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-settings.json",
        )
        self.logger.info("loaded config %s" % str(index_setting_config))

        if index_setting_config:

            self.logger.debug("Processing %s" % index_setting_config)
            settings_json = json.dumps(
                index_setting_config.settings, default=lambda s: vars(s)
            )
            settings_dicts = json.loads(settings_json)
            return settings_dicts
        else:
            return None

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        phase_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.logger.info("loaded config %s" % str(phase_config))

        if phase_config:
            elasticsearch_utils.replace_index_with_now_version(phase_config)
            indiciesClient = client.IndicesClient(self.es)

            self.logger.info("Creating index %s " % (phase_config.index))
            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.create(
                    index=phase_config.index,
                    settings=self.get_index_settings(),
                    ignore=400,
                )
                self.logger.info(
                    "Created index %s returned %s" % (phase_config.index, r)
                )
            except (BadRequestError) as e:
                self.logger.warn("Failed to create or update index: " + str(e))

            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_alias(
                    index=phase_config.index,
                    name=phase_config.alias,
                )
                self.logger.info(
                    "Created alias %s on index % returned %s"
                    % (phase_config.alias, phase_config.index, r)
                )
            except (BadRequestError) as e:
                self.logger.warn("Failed to create or update alias: " + str(e))
