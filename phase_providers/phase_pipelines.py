import contextlib
import json
import logging

from elasticsearch import BadRequestError, NotFoundError, client

from utils import file_utils


class PhasePipelines:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.pipeline_config = []
        self.logger = logging.getLogger(__name__)

    def handle(self):
        # assumes that enrichment policies were already applied when needed
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
            ingestClient = client.IngestClient(self.es)

            with contextlib.suppress(NotFoundError):
                ingestClient.delete_pipeline(id=pipeline_config.name)

            self.logger.info("Processing policy name {} ".format(pipeline_config.name))

            try:
                processors_json = json.dumps(pipeline_config.processors, default=vars)
                processors_dict = json.loads(processors_json)
                r = ingestClient.put_pipeline(
                    id=pipeline_config.name, processors=processors_dict
                )
                self.logger.info(
                    "Updated pipeline {} returned {}".format(pipeline_config.name, r)
                )

            except BadRequestError as e:
                self.logger.info("Failed to update pipeline: {} ".format(e))
