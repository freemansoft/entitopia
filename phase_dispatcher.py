import logging

from phase_enrichment_policies import PhaseEnrichmentPolicies
from phase_index_creation import PhaseindexCreate
from phase_index_populate import PhaseIndexingPopulate
from phase_index_mappings import PhaseIndexMappings
from phase_pipelines import PhasePipelines


class PhaseDispatcher:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def process_phase_step(self, es, project, step_name, one_phase, project_config):
        self.logger.info(
            " ============> Starting step:{} phase:{}".format(step_name, one_phase)
        )
        # This is an unfortunate set of string matching
        if one_phase == "enrichment-policies":
            handler = PhaseEnrichmentPolicies(es, project, step_name, project_config)
            handler.handle()
        elif one_phase == "pipelines":
            handler = PhasePipelines(es, project, step_name, project_config)
            handler.handle()
        elif one_phase == "index-create":
            handler = PhaseindexCreate(es, project, step_name, project_config)
            handler.handle()
        elif one_phase == "index-map":
            handler = PhaseIndexMappings(es, project, step_name, project_config)
            handler.handle()
        elif one_phase == "index-populate":
            handler = PhaseIndexingPopulate(es, project, step_name, project_config)
            handler.handle()
        else:
            logger.error("Unrecognized phase: {}".format(step_name.phase))
