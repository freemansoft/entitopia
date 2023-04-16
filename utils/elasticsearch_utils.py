from elasticsearch import Elasticsearch
import logging
import requests
from datetime import datetime
from utils.custom_logging_formatter import CustomFormatter


import utils.file_utils as file_utils
import utils.elasticsearch_utils as elasticsearch_utils


def connect_to_es(es_connection_info):
    logger = logging.getLogger(__name__)
    # Version 8.x is really chatty
    logging.getLogger("elastic_transport.transport").setLevel(logging.WARN)
    # For locally generated certs
    requests.packages.urllib3.disable_warnings()

    es_client = Elasticsearch(
        hosts=[
            {
                "host": es_connection_info.host,
                "port": es_connection_info.port,
                "scheme": es_connection_info.scheme,
            }
        ],
        basic_auth=[es_connection_info.username, es_connection_info.password],
        verify_certs=False,
        timeout=es_connection_info.timeout,
    )

    # test the connection
    if es_client.ping():
        logger.info("Elasticsearch connection succeed: {}".format(es_client.info()))
    else:
        logger.info("Elasticsearch connection failed")

    return es_client


def replace_index_with_now_version(config):
    # datetime.now().strftime('%Y.%m.%d-%H%m%S')
    # datetime.now().strftime('%Y.%m.%d')
    if "{now/d}" in config.index:
        config.index = config.index.replace(
            "{now/d}", datetime.now().strftime("%Y.%m.%d")
        )
    elif "{now/s}" in config.index:
        config.index = config.index.replace(
            "{now/s}", datetime.now().strftime("%Y.%m.%d")
        )


def replace_match_indicies_with_now_version(config):
    logger = logging.getLogger(__name__)

    # datetime.now().strftime('%Y.%m.%d-%H%m%S')
    # datetime.now().strftime('%Y.%m.%d')
    logger.debug("looking for now/d instances in {}".format(config.match.indices))
    if "{now/d}" in config.match.indices:
        config.match.indices = config.match.indices.replace(
            "{now/d}", datetime.now().strftime("%Y.%m.%d")
        )
    elif "{now/s}" in config.match.indices:
        config.match.indices = config.match.indices.replace(
            "{now/s}", datetime.now().strftime("%Y.%m.%d")
        )


def main():
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    CustomFormatter().replace_formatter(root_logger)

    logger = logging.getLogger(__name__)

    es_config = file_utils.load_from_file("es_config.json")
    es = elasticsearch_utils.connect_to_es(es_config)
    if not es:
        logger.error("No es connection!")


if __name__ == "__main__":
    main()
