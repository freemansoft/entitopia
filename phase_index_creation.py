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
        self.logger.debug("loaded config {}".format(index_setting_config))

        if index_setting_config:

            self.logger.debug("Processing {}".format(index_setting_config))
            settings_json = json.dumps(
                index_setting_config.settings, default=lambda s: vars(s)
            )
            settings_dicts = json.loads(settings_json)
            return settings_dicts
        else:
            return None

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
            "index-config.json",
        )
        self.logger.debug("loaded config {}".format(phase_config))

        if phase_config:
            elasticsearch_utils.replace_index_with_now_version(phase_config)
            indiciesClient = client.IndicesClient(self.es)

            self.logger.info("Creating index {} ".format(phase_config.index))
            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.create(
                    index=phase_config.index,
                    settings=self.get_index_settings(),
                    # ignore=400,
                )
                self.logger.info(
                    "Created index {} returned {}".format(phase_config.index, r)
                )
            except (BadRequestError) as e:
                self.logger.warning("Failed to create or update index: {}".format(e))

            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_alias(
                    index=phase_config.index,
                    name=phase_config.alias,
                )
                self.logger.info(
                    "Created alias {} on index {} returned {}".format(
                        phase_config.alias, phase_config.index, r
                    )
                )
            except (BadRequestError) as e:
                self.logger.warning("Failed to create or update alias: {}".format(e))
