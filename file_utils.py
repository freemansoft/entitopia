from types import SimpleNamespace
import json
import logging


def load_from_file(file_name):
    # TODO add is_required flag to change warnign behavior
    logger = logging.getLogger(__name__)
    try:
        logger.debug("Loading from file {}".format(file_name))
        config_file = open(file_name)
        try:
            loaded_config = json.loads(
                config_file.read(), object_hook=lambda d: SimpleNamespace(**d)
            )
            logger.debug("Loaded {} info : {}".format(file_name, loaded_config))
            return loaded_config
        except Exception as e:
            logger.warn("Failed loading:{} error:{}".format(file_name, e))
        finally:
            config_file.close()
    except Exception as e:
        logger.warn("Failed opening:{} error:{}".format(file_name, e))
        return None


def load_from_project_file(target_project, config_or_data, target_step, file):
    """
    Returns SimpleNamespace of loaded expected json file
    ignores target_step if None
    """
    logger = logging.getLogger(__name__)
    try:
        configuration_file_name = (
            "{}/{}/{}/{}".format(
                target_project,
                config_or_data,
                target_step,
                file,
            )
            if target_step
            else "{}/{}".format(
                target_project,
                file,
            )
        )
        return load_from_file(configuration_file_name)
    except Exception as e:
        logger.warn("Returing: None {}".format(e))
        return None
