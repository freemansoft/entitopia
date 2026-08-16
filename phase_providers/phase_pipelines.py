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
                # Raise rather than log: a refused pipeline does not stop the
                # load that follows it, and that load succeeds and looks right.
                # A dot_number pipeline only normalizes a join key, so losing it
                # costs no documents and no errors -- just an enrichment that
                # matches nothing, the failure that twice emptied carriers'
                # auth_history and boc3_agents without a single ERROR line.
                # Same call the mapping and policy phases now make, for the same
                # reason: the phase cannot fix this, so it must not hide it.
                raise RuntimeError(
                    "Elasticsearch refused pipeline {}: {}".format(
                        pipeline_config.name, e
                    )
                ) from e
