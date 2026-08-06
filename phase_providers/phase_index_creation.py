import json
import logging

from elasticsearch import BadRequestError, client

from utils import analysis_fingerprint, elasticsearch_utils, file_utils


class PhaseindexCreate:
    def __init__(self, es, project, one_step, project_configs):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_configs
        self.logger = logging.getLogger(__name__)

    def get_index_settings(self):
        index_setting_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-settings.json",
        )
        self.logger.debug("loaded config {}".format(index_setting_config))

        if index_setting_config:
            self.logger.debug("Processing {}".format(index_setting_config))
            settings_json = json.dumps(index_setting_config.settings, default=vars)
            settings_dicts = json.loads(settings_json)
            return settings_dicts
        else:
            return None

    def get_index_mapping_properties(self):
        # Loaded here too (PhaseIndexMappings applies the mapping itself, in its
        # own later step) because the fingerprint stamped at creation time must
        # cover which analyzer each subfield binds to, not just which analyzers
        # index-settings.json declares — that binding lives in index-mappings.json
        # and a change to it is otherwise invisible to the staleness check.
        index_mapping_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-mappings.json",
        )
        if not index_mapping_config:
            return None
        properties = getattr(index_mapping_config.mappings, "properties", None)
        if properties is None:
            return None
        properties_json = json.dumps(properties, default=vars)
        return json.loads(properties_json)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        phase_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.logger.debug("loaded config {}".format(phase_config))

        if phase_config:
            elasticsearch_utils.replace_index_with_now_version(phase_config)
            indiciesClient = client.IndicesClient(self.es)

            self.logger.info("Creating index {} ".format(phase_config.index))
            settings = self.get_index_settings()
            mapping_properties = self.get_index_mapping_properties()
            # Recorded at creation because this is the only moment the settings
            # that built the index and the index itself are both in hand. A
            # later reader can compare it against the config on disk and find
            # out whether the tokens it is about to score are current.
            fingerprint = analysis_fingerprint.fingerprint_analysis(
                settings, mapping_properties
            )
            create_args = {"index": phase_config.index, "settings": settings}
            if fingerprint:
                create_args["mappings"] = {
                    "_meta": {"analysis_fingerprint": fingerprint}
                }

            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.create(**create_args)
                self.logger.info(
                    "Created index {} with analysis fingerprint {} returned {}".format(
                        phase_config.index, fingerprint, r
                    )
                )
            except BadRequestError as e:
                self.logger.warning("Failed to create or update index: {}".format(e))

            try:
                # https://elasticsearch-py.readthedocs.io/en/latest/api.html#indices
                r = indiciesClient.put_alias(
                    index=phase_config.index,
                    name=phase_config.alias,
                )
                self.logger.info(
                    "Created alias {} on index {} returned {}".format(
                        phase_config.alias, phase_config.index, r
                    )
                )
            except BadRequestError as e:
                self.logger.warning("Failed to create or update alias: {}".format(e))
