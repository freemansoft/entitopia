import argparse
import logging as logging
from custom_logging_formatter import CustomFormatter
import copy

from phase_enrichment_policies import PhaseEnrichmentPolicies
from phase_index_creation import PhaseindexCreate
from phase_index_populate import PhaseIndexingPopulate
from phase_index_mappings import PhaseIndexMappings
from phase_pipelines import PhasePipelines

import file_utils
import elasticsearch_utils

PROJECT_CONFIGURATION_FILE_NAME = "configuration.json"


def process_phase_step(es, project, step_name, one_phase, project_config):
    logger = logging.getLogger(__name__)
    logger.info(" ============> Starting step:{} phase:{}".format(step_name, one_phase))
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


def process_phase_steps(
    es,
    project,
    phase_steps,
    project_config,
):
    logger = logger = logging.getLogger(__name__)

    for one_step in phase_steps:
        for one_phase in one_step.phases:
            process_phase_step(
                es,
                project,
                one_step.name,
                one_phase,
                project_config,
            )


def load_project_config(project):
    config = file_utils.load_from_project_file(
        project, None, None, PROJECT_CONFIGURATION_FILE_NAME
    )
    return config


def parse_args():
    logger = logging.getLogger(__name__)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project",
        required=True,
        default=None,
        help="The root configuration directory Ex: --target=DOT-Commercial",
    )
    parser.add_argument(
        "--step", required=False, default=None, help="Run a single step"
    )
    parser.add_argument(
        "--phase", required=False, default=None, help="Run a single phase"
    )
    args = parser.parse_args()
    logger.info("Args: {} ".format(args))
    return args


def apply_args_to_config(config, args):
    """
    Change the configuration based on command line arguments
    Has hard coded kowledge of mapping
    """
    logger = logging.getLogger(__name__)
    new_config = copy.copy(config)
    if not args.step and not args.phase:
        logger.debug("No step or phase overrides applied")
    if args.step:
        logger.debug("Step override specified {}".format(args.step))
        filtered_steps = []
        for one_step in config.steps:
            if one_step.name == args.step:
                filtered_steps.append(one_step)
            else:
                logger.debug("Filtering out step {}".format(one_step))
        new_config.steps = filtered_steps
        logger.debug("Steps filtered to {}".format(new_config.steps))
    if args.phase:
        logger.debug("Phase override specified {}".format(args.phase))
        for one_step in new_config.steps:
            filtered_phases = []
            for one_phase in one_step.phases:
                if one_phase == args.phase:
                    filtered_phases.append(one_phase)
                else:
                    logger.debug(
                        "Filtering out phase {} in step {}".format(one_phase, one_step)
                    )
            one_step.phases = filtered_phases
            logger.debug(
                "Phases filtered to {} in step {} ".format(
                    one_step.phases, one_step.name
                )
            )
    logger.info("Filter resulted in these steps/phases {}".format(new_config.steps))
    return new_config


def update_logger_based_from_config(project_config, logger):
    if project_config.logLevel:
        logger.setLevel(project_config.logLevel)


def main():
    """
    Example Usage:
    `python3 execution_template.py --project=DOT-Commercial`
    """

    # set the default to be overriden by command line
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    CustomFormatter().replace_formatter(root_logger)

    args = parse_args()
    project_config = load_project_config(args.project)
    update_logger_based_from_config(project_config, root_logger)
    project_config = apply_args_to_config(project_config, args)

    # connect to cluster
    es_config = file_utils.load_from_file("es_config.json")
    es = elasticsearch_utils.connect_to_es(es_config)

    # run it!
    process_phase_steps(es, args.project, project_config.steps, project_config)


if __name__ == "__main__":
    main()
