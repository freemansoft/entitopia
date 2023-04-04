import file_utils
import logging as logging
import json
from csv_load_utils import CsvLoadUtils
from elasticsearch import NotFoundError, ConflictError, BadRequestError
from elasticsearch import client


class PhaseIndexMappings:
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
        index_mapping_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-mappings.json",
        )
        self.logger.info("loaded config %s" % str(index_mapping_config))

        if index_mapping_config:
            indiciesClient = client.IndicesClient(self.es)
            self.logger.debug("Processing %s" % index_mapping_config)
            properties_json = json.dumps(
                index_mapping_config.mappings.properties, default=lambda s: vars(s)
            )
            properties_dicts = json.loads(properties_json)
            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_mapping(
                    index=index_mapping_config.index,
                    properties=properties_dicts,
                    ignore=400,
                )
                self.logger.info(
                    "Updating mappings on index %s returned %s"
                    % (index_mapping_config.index, r)
                )

            except (BadRequestError) as e:
                self.logger.info("Failed to create or update index: " + str(e))
