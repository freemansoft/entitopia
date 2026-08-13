import json
import logging

from elasticsearch import BadRequestError, client

from utils import elasticsearch_utils, file_utils


class PhaseIndexMappings:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        index_mapping_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-mappings.json",
        )
        self.logger.debug("loaded config {}".format(index_mapping_config))

        if index_mapping_config:
            elasticsearch_utils.replace_index_with_now_version(index_mapping_config)
            indiciesClient = client.IndicesClient(self.es)
            self.logger.debug("Processing {}".format(index_mapping_config))
            properties_json = json.dumps(
                index_mapping_config.mappings.properties, default=vars
            )
            properties_dicts = json.loads(properties_json)
            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_mapping(
                    index=index_mapping_config.index,
                    properties=properties_dicts,
                    # ignore=400,
                )
                self.logger.info(
                    "Updating mappings on index {} returned {}".format(
                        index_mapping_config.index, r
                    )
                )

            except BadRequestError as e:
                # Raised, not logged. This was an INFO line -- below the level
                # anyone scans a long run for -- and the load then proceeded
                # into whatever mapping the index already had, exiting 0.
                #
                # The case that forced this: `carriers` already existed with
                # out_of_service_orders as an object, and Elasticsearch cannot
                # convert an existing object field to `nested`. Rerunning the
                # step refused the mapping, indexed 2,085,534 documents anyway,
                # and reported success, while the sweep's nested selector kept
                # failing with "failed to find nested object under path". A
                # mapping that did not apply is not a warning about the future,
                # it is a wrong index now, and populating it costs more than
                # stopping does.
                raise RuntimeError(
                    "Elasticsearch refused the mapping for index {}: {}. Nothing has "
                    "been loaded. An existing index cannot have a field's type "
                    "changed -- notably object to nested -- so delete that index and "
                    "rerun the step rather than populating it under the mapping it "
                    "already has.".format(index_mapping_config.index, e)
                ) from e
