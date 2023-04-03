import file_utils
import logging as logging
from csv_load_utils import CsvLoadUtils


class PhasePipelines:
    def __init__(self, es, project, one_step, project_config):
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.pipelines_configs = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        pipelines_configs = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "pipelines.json",
        )
        self.pipelines_configs = pipelines_configs
        self.logger.debug("loaded config %s" % str(pipelines_configs))
        for pipeline_config in pipelines_configs:
            self.logger.debug("Processing %s" % pipeline_config)
