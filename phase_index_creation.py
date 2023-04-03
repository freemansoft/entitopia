import file_utils
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
        self.phase_configs = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        phase_configs = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.phase_configs = phase_configs
        self.logger.info("loaded config %s" % str(phase_configs))

        if self.phase_configs:
            indiciesClient = client.IndicesClient(self.es)
            for phase_config in phase_configs:

                self.logger.info("Creating index %s " % (phase_config.index))
                try:
                    r = indiciesClient.create(index=phase_config.index)
                    self.logger.info("Creating index %s runed %s" %
                                     (phase_config.index, r))

                except (BadRequestError) as e:
                    self.logger.info(
                        "Failed to create or update index: " + str(e))
