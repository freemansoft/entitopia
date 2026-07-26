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
            logger.warning("Failed loading:{} error:{}".format(file_name, e))
        finally:
            config_file.close()
    except Exception as e:
        logger.warning("Failed opening:{} error:{}".format(file_name, e))
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
        logger.warning("Returing: None {}".format(e))
        return None


def load_key_value_file(file_name):
    """
    Parses simple KEY='VALUE' or KEY=VALUE lines (bash-sourceable) into a dict.
    Returns None if the file cannot be opened.
    """
    logger = logging.getLogger(__name__)
    result = {}
    try:
        with open(file_name) as key_value_file:
            for line in key_value_file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip("'\"")
        logger.debug("Loaded {} info : {}".format(file_name, result))
        return result
    except Exception as e:
        logger.warning("Failed opening:{} error:{}".format(file_name, e))
        return None
