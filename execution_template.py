import argparse
from types import SimpleNamespace
import logging as logging
import itertools

from phase_enrichment_policies import PhaseEnrichmentPolicies
from phase_index_creation import PhaseindexCreate
from phase_indexing import PhaseIndexing
from phase_index_mappings import PhaseIndexMappings
from phase_pipelines import PhasePipelines

import file_utils
import elasticsearch_utils

PROJECT_CONFIGURATION_FILE_NAME = "configuration.json"


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
    args = parser.parse_args()
    logger.info("Args: {} ".format(args))
    return args


def permutations(outer_key, outer, inner_key, inner):
    """
    return a list of SimpleNameSapce(step:, phase:) objects
    create product() of tuples and convert product() to list of dictionary{step:, phase:}
    outer is a tupple of steps
    inner is a tuple of phases
    outer_key and inner_key are the labels to be applied to the values in the tuples
    """
    logger = logging.getLogger(__name__)
    # TODO simplify this with a nested set of for loops
    # TODO tthis approach is techinically interesting and a great ref for future work
    # create a list of 4-tuples (label:value, label2:value2)
    all_phases = [
        list(item) for item in itertools.product(outer_key, outer, inner_key, inner)
    ]
    # convert [4-tubles] to [2-key dictionaries]
    logger.debug([type(item) for item in all_phases])
    all_phases_dict = [
        dict([(item[0], item[1]), (item[2], item[3])]) for item in all_phases
    ]
    # convert the [{step: phase:}] into [SimpleNamespace] objects
    all_phases = [SimpleNamespace(**one_step) for one_step in all_phases_dict]
    return all_phases


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
        handler = PhaseIndexMappings(es, project, step_name, project_config)
        handler.handle()
    elif one_phase == "index":
        handler = PhaseIndexing(es, project, step_name, project_config)
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
                one_step.step,
                one_phase,
                project_config,
            )


from custom_logging_formatter import CustomFormatter


def main():
    """
    Example Usage:
    `python3 execution_template.py --project=DOT-Commercial`
    """
    # set the default to be overriden by command line
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    # create console handler with a higher log level
    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter())
    root_logger.handlers = []
    root_logger.addHandler(handler)

    args = parse_args()
    project_config = load_project_config(args.project)
    if project_config.logLevel:
        root_logger.setLevel(project_config.logLevel)

    # connect to cluster
    es_config = file_utils.load_from_file("es_config.json")
    es = elasticsearch_utils.connect_to_es(es_config)

    # run it!
    process_phase_steps(es, args.project, project_config.steps, project_config)


if __name__ == "__main__":
    main()
