import file_utils
import logging as logging


class PhasePipelines:
    def __init__(self, es, project, one_step, project_config):
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.pipeline_config = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        pipeline_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "pipelines.json",
        )
        self.logger.debug("loaded config {}".format(pipeline_config))
        if pipeline_config:
            self.logger.debug("Processing {} NOT IMPLEMENTED".format(pipeline_config))
