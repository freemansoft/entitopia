from elasticsearch import Elasticsearch
import logging
import requests

import file_utils
import elasticsearch_utils


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
        logger.info("Elasticsearch connection succeed:" + str(es_client.info()))
    else:
        logger.info("Elasticsearch connection failed")

    return es_client


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    es_config = file_utils.load_from_file("es_config.json")
    es = elasticsearch_utils.connect_to_es(es_config)
    if not es:
        logger.error("No es connection!")


if __name__ == "__main__":
    main()
