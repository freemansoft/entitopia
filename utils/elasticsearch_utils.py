import logging
import re
from datetime import datetime

import requests
from elasticsearch import Elasticsearch, NotFoundError

from utils import elasticsearch_utils, file_utils
from utils.custom_logging_formatter import CustomFormatter

# The trailing number on a dated index name, e.g. the 000001 in
# carriers-2026.11.08-000001. See resolve_new_index.
_GENERATION = re.compile(r"^(?P<stem>.*)-(?P<generation>\d+)$")


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


def _substitute_date(template):
    """Replace the date placeholder with today's date.

    `{now/s}` is still accepted, and still resolves to the day stamp, because
    the code this replaced handled it that way and a config may rely on it.
    """
    today = datetime.now().strftime("%Y.%m.%d")
    return template.replace("{now/d}", today).replace("{now/s}", today)


def _generations(indices_client, name):
    """Existing indexes that differ from `name` only in the trailing number.

    Returns (stem, width, {generation: index}), or (None, 0, {}) for a name with
    no trailing number. Filtered with an exact pattern after the wildcard
    lookup because the wildcard alone also matches hand-made copies such as
    `chameleon-candidates-2026.11.06-pretiebreak-000001`, a clone kept aside
    for comparison, which is not a generation and must not be counted as one.
    """
    match = _GENERATION.match(name)
    if not match:
        return None, 0, {}
    stem, digits = match.group("stem"), match.group("generation")
    existing = indices_client.get(
        index=stem + "-*", allow_no_indices=True, ignore_unavailable=True, expand_wildcards="all"
    )
    pattern = re.compile(re.escape(stem) + r"-(\d+)$")
    found = {}
    for index in existing:
        hit = pattern.match(index)
        if hit:
            found[int(hit.group(1))] = index
    return stem, len(digits), found


def resolve_new_index(indices_client, template):
    """The name index-create should create: today's, at the next free generation.

    Exists because every index-config.json names its index
    `<step>-{now/d}-000001`, so two runs on one day used to resolve to one
    name. The second create was refused with resource_already_exists_exception,
    which was logged as a warning, and every later phase wrote into the first
    run's index. For a sweep that left a union of both runs under one name: new
    pairs overwrote matching _ids and pairs the new run dropped stayed behind.

    Taking the next generation instead gives the rerun a fresh index with the
    current settings and leaves the earlier one intact to compare against. A
    name with no trailing number has no next generation, so an existing one
    refuses rather than falling back to the reuse this exists to prevent.
    """
    name = _substitute_date(template)
    stem, width, found = _generations(indices_client, name)
    if stem is None:
        if indices_client.get(index=name, allow_no_indices=True, ignore_unavailable=True):
            raise RuntimeError(
                "Index {} already exists and its name has no trailing generation "
                "number to advance. Delete it, or end the configured name in "
                "-000001.".format(name)
            )
        return name
    if not found:
        return name
    return "{}-{:0{}d}".format(stem, max(found) + 1, width)


def resolve_current_index(indices_client, template):
    """The index a phase after index-create should read or write: today's newest.

    The counterpart to resolve_new_index, for index-map, index-populate,
    entity-match and the enrich policies. Each phase resolves the name on its
    own from the same template, so they agree with create only if they look up
    what create made rather than re-deriving the configured name. Falls back to
    the configured name when nothing exists yet, which is what these phases
    targeted before generations existed.
    """
    name = _substitute_date(template)
    _, _, found = _generations(indices_client, name)
    return found[max(found)] if found else name


def resolve_existing_index(indices_client, template):
    """resolve_current_index for a phase that writes, refusing when there is nothing to write into.

    A phase run on its own with --phase, on a later day than its step's
    index-create, resolves to today's name, which no index carries yet.
    Elasticsearch auto-creates an index on first write, so the load used to
    land in an index with dynamic mappings and none of the configured
    analyzers, and the run reported success. Refusing names the fix instead.
    """
    name = resolve_current_index(indices_client, template)
    if indices_client.get(index=name, allow_no_indices=True, ignore_unavailable=True):
        return name
    raise RuntimeError(
        "No index {} exists: this step's index-create has not run today. A phase "
        "run on its own writes into today's index, so run the whole step "
        "(--step=<step>) to create it first. Writing anyway would let "
        "Elasticsearch auto-create it with dynamic mappings and none of the "
        "configured analyzers.".format(name)
    )


def alias_targets(indices_client, alias):
    """The indexes `alias` names, sorted; empty when the alias does not exist."""
    try:
        return sorted(indices_client.get_alias(name=alias))
    except NotFoundError:
        return []


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
