import logging
from datetime import datetime

import requests
from elasticsearch import Elasticsearch, NotFoundError

from utils import elasticsearch_utils, file_utils
from utils.custom_logging_formatter import CustomFormatter


def connect_to_es(es_connection_info):
    logger = logging.getLogger(__name__)
    # Elasticsearch's client transport logging is really chatty
    logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
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
        # `timeout` was removed in elasticsearch-py 9.0; `request_timeout` is the replacement
        request_timeout=es_connection_info.timeout,
    )

    # test the connection
    if es_client.ping():
        logger.info("Elasticsearch connection succeed: {}".format(es_client.info()))
    else:
        logger.info("Elasticsearch connection failed")

    return es_client


def attach_alias(indices_client, index, alias, retain_existing=False):
    """Point `alias` at `index` and, by default, at nothing else.

    Exists because `put_alias` is purely additive: Elasticsearch never detaches
    an alias from the index it previously named, so a project that reloads into
    a fresh date-stamped index left every earlier index attached. The alias then
    resolved to a union, and reads through it returned each document once per
    accumulated index — no error, just silently multiplied data on the one name
    every consumer uses. Downstream that produced sweeps scoring a mixture of
    differently-analyzed tokens, a preflight check inspecting whichever index
    the mapping response happened to list first, and a corpus in which every
    record was its own perfect-scoring duplicate.

    The removes and the add go in **one** `update_aliases` call because that
    call applies its whole action list atomically. Removing first and adding
    second would open a window where the alias resolves to nothing and a
    concurrent reader gets index-not-found rather than stale data, which would
    defeat the reason the alias exists.

    The remove list is built from live alias membership rather than from an
    assumption that exactly one index holds it, so a cluster that already
    accumulated several is repaired by its next run instead of needing a
    migration. Membership is read with an explicit NotFoundError catch rather
    than `ignore_status=404`: a missing alias answers with the error document
    `{"error": ..., "status": 404}`, and treating that body as alias membership
    would send `remove` actions for indexes named `error` and `status`, failing
    the very first-creation case this has to handle without a branch.

    `retain_existing` reinstates the additive behavior for an operator who
    deliberately wants the previous index to keep serving the alias — a
    read-side cutover held open across a reload, say. It defaults off because
    accumulation is indistinguishable from the bug, and it skips the membership
    lookup entirely so a retained add cannot fail on an alias read.
    """
    actions = []
    if not retain_existing:
        try:
            attached = indices_client.get_alias(name=alias)
        except NotFoundError:
            # First creation. The remove list stays empty and the call degrades
            # to a plain add, so no branch is needed here or at the call site.
            attached = {}
        actions = [
            {"remove": {"index": attached_index, "alias": alias}}
            for attached_index in attached
            # A same-day re-run resolves `{now/d}` to the index already holding
            # the alias. Elasticsearch would accept a remove/add pair on it, but
            # the transaction would read as though the alias had moved.
            if attached_index != index
        ]
    actions.append({"add": {"index": index, "alias": alias}})
    return indices_client.update_aliases(actions=actions)


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
