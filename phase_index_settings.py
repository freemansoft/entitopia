import file_utils
import logging as logging
import json
from csv_load_utils import CsvLoadUtils
from elasticsearch import NotFoundError, ConflictError, BadRequestError
from elasticsearch import client


class PhaseIndexSettings:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        index_setting_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-settings.json",
        )
        self.logger.info("loaded config %s" % str(index_setting_config))

        if index_setting_config:
            indiciesClient = client.IndicesClient(self.es)
            self.logger.debug("Processing %s" % index_setting_config)
            settings_json = json.dumps(
                index_setting_config.settings, default=lambda s: vars(s)
            )
            settings_dicts = json.loads(settings_json)
            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_settings(
                    index=index_setting_config.index,
                    settings=settings_dicts,
                    ignore=400,
                )
                self.logger.info(
                    "Updated Index settings index %s returned %s"
                    % (index_setting_config.index, r)
                )

            except (BadRequestError) as e:
                self.logger.info("Failed to create or update index settings: " + str(e))
