import argparse
import logging as logging
from utils.custom_logging_formatter import CustomFormatter
import copy

from phase_providers.phase_dispatcher import PhaseDispatcher

import utils.file_utils as file_utils
import utils.elasticsearch_utils as elasticsearch_utils

PROJECT_CONFIGURATION_FILE_NAME = "configuration.json"


def process_phase_steps(
    dispatcher,
    es,
    project,
    phase_steps,
    project_config,
):
    for one_step in phase_steps:
        for one_phase in one_step.phases:
            dispatcher.process_phase_step(
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
        help="The root configuration directory Ex: --project=DOT-Commercial",
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
    try:
        logger.setLevel(project_config.logLevel)
    except AttributeError as e:
        logger.warning("No default logging level in config {}".format(project_config))


def main():
    """
    Example Usage:
    `python3 execute_template.py --project=DOT-Commercial`
    """

    # set the default to be overriden by command line
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    CustomFormatter().replace_formatter(root_logger)

    args = parse_args()
    project_config = load_project_config(args.project)
    if project_config:
        update_logger_based_from_config(project_config, root_logger)
        project_config = apply_args_to_config(project_config, args)

        # connect to cluster
        es_config = file_utils.load_from_file("es_config.json")
        es = elasticsearch_utils.connect_to_es(es_config)

        # run it!
        dispatcher = PhaseDispatcher()
        process_phase_steps(
            dispatcher, es, args.project, project_config.steps, project_config
        )
    else:
        root_logger.critical("Could not load configuration for ".format(args.project))


if __name__ == "__main__":
    main()
