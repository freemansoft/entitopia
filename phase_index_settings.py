import file_utils
import logging as logging
from csv_load_utils import CsvLoadUtils


class PhaseIndexSettings:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.index_settings_configs = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        index_settings_configs = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.index_settings_configs = index_settings_configs
        self.logger.info("loaded config %s" % str(index_settings_configs))
        if self.index_settings_configs:
            for index_settings_config in index_settings_configs:
                self.logger.debug("Processing %s" % index_settings_config)
