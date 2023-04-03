import file_utils
import logging as logging
from csv_load_utils import CsvLoadUtils


class PhaseIndexMappings:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.index_mappings_configs = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        index_mappings_configs = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-mappings.json",
        )
        self.index_mappings_configs = index_mappings_configs
        self.logger.info("loaded config %s" % str(index_mappings_configs))

        if self.index_mappings_configs:
            for index_mapping_config in self.index_mappings_configs:
                self.logger.debug("Processing %s" % index_mapping_config)
