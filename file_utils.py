from types import SimpleNamespace
import json
import logging


def load_from_file(file_name):
    # TODO add is_required flag to change warnign behavior
    logger = logging.getLogger(__name__)
    try:
        logger.debug("Loading from file %s" % (file_name))
        config_file = open(file_name)
        try:
            loaded_config = json.loads(
                config_file.read(), object_hook=lambda d: SimpleNamespace(**d)
            )
            logger.debug("Loaded %s info : %s" % (file_name, loaded_config))
            return loaded_config
        except Exception as e:
            logger.warn(e)
        finally:
            config_file.close()
    except Exception as e:
        logger.warn(e)
        return None


def load_from_project_file(target_project, config_or_data, target_step, file):
    """
    Returns SimpleNamespace of loaded expected json file
    ignores target_step if None
    """
    logger = logging.getLogger(__name__)
    try:
        configuration_file_name = (
            "%s/%s/%s/%s"
            % (
                target_project,
                config_or_data,
                target_step,
                file,
            )
            if target_step
            else "%s/%s"
            % (
                target_project,
                file,
            )
        )
        return load_from_file(configuration_file_name)
    except Exception as e:
        logger.warn("Returing: None %e" % e)
        return None
