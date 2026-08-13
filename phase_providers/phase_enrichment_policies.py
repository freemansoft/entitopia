import json
import logging

from elasticsearch import BadRequestError, ConflictError, NotFoundError, client

from utils import elasticsearch_utils, file_utils

# Fields of a policy's `match` block that decide what the enrich index contains.
# Compared when a policy could not be deleted, to tell "the existing policy is
# the one config asks for" apart from "a stale policy is squatting on the name".
POLICY_IDENTITY_KEYS = ("indices", "match_field", "enrich_fields")

# Executing a policy reindexes the whole source into a hidden enrich index, so
# it scales with the source, not with the request. elasticsearch-py's default
# request timeout expires long before a large one finishes: the 9.6M-document
# inspections-per-unit policy raised "Connection timed out" while Elasticsearch
# went on to build all 9,632,353 documents successfully. The client gave up, not
# the server, so the run reported a failure that had not happened.
#
# An hour is chosen to be longer than any source this project loads rather than
# tuned to one of them. Raising it costs nothing when policies are small, and a
# policy that genuinely hangs still surfaces -- an hour late, but as a real
# failure rather than as a routine timeout nobody can distinguish from one.
EXECUTE_TIMEOUT_SECONDS = 3600


class PhaseEnrichmentPolicies:
    """Rebuilds each configured enrich policy's backing index from current source data.

    An enrich policy is a point-in-time snapshot: `execute_policy` copies the
    source index into a hidden `.enrich-*` index, and documents indexed later
    are invisible to enrichment until the policy is executed again. So this
    phase must execute every policy on every run, not merely ensure one exists
    — a policy left un-executed enriches against whatever the source held the
    last time someone ran it, which for a reused cluster is routinely nothing.

    That distinction was previously lost: delete/put/execute shared one `try`,
    so a policy that already existed (its `put` failing with
    `resource_already_exists_exception`) skipped `execute` entirely and left
    the previous run's empty `.enrich-*` index in place. Every phase logged
    success, carriers came out with no `out_of_service_orders`, and the
    chameleon sweep reported zero predecessors — a clean-looking run with a
    fabricated negative result. Execution is now unconditional and verified.
    """

    def __init__(self, es, project, one_step, project_configs):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_configs
        self.logger = logging.getLogger(__name__)

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        all_phase_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "enrichment-policies.json",
        )
        self.logger.debug("loaded config {}".format(all_phase_config))

        if not all_phase_config:
            return

        # Collected rather than raised on the spot so one broken policy cannot
        # hide the state of the rest: the real incident had six drifted
        # policies, and stopping at the first would have reported one.
        failed = []

        for phase_config in all_phase_config:
            elasticsearch_utils.replace_match_indicies_with_now_version(phase_config)
            enrich_client = client.EnrichClient(self.es)

            match_json = json.dumps(phase_config.match, default=vars)
            match_dicts = json.loads(match_json)

            self.logger.info(
                "Processing policy name {} match {}".format(
                    phase_config.name, phase_config.match
                )
            )

            if not self._ensure_policy(enrich_client, phase_config.name, match_dicts):
                failed.append(phase_config.name)
                continue
            # The timeout has to be set on the Elasticsearch object and a fresh
            # namespaced client built from it: EnrichClient has no `options` of
            # its own, and calling one on it fails only at runtime, on the long
            # policy this exists to protect.
            executing_client = client.EnrichClient(
                self.es.options(request_timeout=EXECUTE_TIMEOUT_SECONDS)
            )
            if not self._execute_policy(executing_client, phase_config.name, match_dicts):
                failed.append(phase_config.name)

        if failed:
            # Raised, not logged. Every one of these paths already logged an
            # ERROR explaining itself and the run still exited 0, so a reload
            # finished with carriers enriched from a superseded index while
            # every phase reported success -- the silent-wrong-output failure
            # this codebase keeps hitting. The exit code is the only part of a
            # long unattended run anyone actually checks.
            raise RuntimeError(
                "Enrichment policies could not be rebuilt: {}. Anything enriched from "
                "them would read the wrong source, so the run is stopped rather than "
                "left to produce a healthy-looking result. See the errors above; a "
                "policy bound to a pipeline needs that pipeline deleted and the step "
                "rerun.".format(", ".join(failed))
            )

    def _ensure_policy(self, enrich_client, name, match_dicts) -> bool:
        """Make the named policy match config, returning whether it is safe to execute.

        Enrich policies are immutable, so changing one means deleting and
        recreating it — but a policy cannot be deleted while an ingest pipeline
        references it, which is the normal state on any cluster that has been
        loaded before. When deletion is blocked, the existing policy is only
        acceptable if it already targets what config asks for; executing a
        policy whose definition has drifted (typically pointing at an earlier
        day's index) would enrich from the wrong source while looking healthy.
        """
        try:
            enrich_client.delete_policy(name=name)
        except ConflictError as e:
            self.logger.warning(
                "Could not delete enrichment policy {} ({}); an ingest pipeline is "
                "still bound to it. Falling back to verifying the existing "
                "definition.".format(name, e)
            )
        except NotFoundError:
            pass

        try:
            r = enrich_client.put_policy(name=name, match=match_dicts)
            self.logger.info("Updated policy {} returned {}".format(name, r))
            return True
        except BadRequestError as e:
            if getattr(e, "error", "") != "resource_already_exists_exception":
                self.logger.error("Failed to update policy {}: {}".format(name, e))
                return False

        return self._existing_policy_matches(enrich_client, name, match_dicts)

    def _existing_policy_matches(self, enrich_client, name, match_dicts) -> bool:
        """Whether the undeletable policy already targets what config asks for.

        Only the fields that determine the enrich index's contents are
        compared; Elasticsearch echoes back a normalized form of the policy, so
        comparing whole documents would report differences that do not matter.
        """
        try:
            response = enrich_client.get_policy(name=name)
        except NotFoundError:
            self.logger.error(
                "Policy {} could neither be created nor read back".format(name)
            )
            return False

        policies = response.get("policies") or []
        if not policies:
            self.logger.error("Policy {} read back empty".format(name))
            return False
        existing = policies[0].get("config", {}).get("match", {})

        for key in POLICY_IDENTITY_KEYS:
            wanted = match_dicts.get(key)
            found = existing.get(key)
            # `indices` accepts a bare string in config but always reads back as
            # a list, so both sides are normalized before comparing.
            if isinstance(wanted, str):
                wanted = [wanted]
            if isinstance(found, str):
                found = [found]
            if wanted != found:
                self.logger.error(
                    "Existing policy {} disagrees with config on {}: has {!r}, config "
                    "wants {!r}. It is bound to a pipeline and cannot be replaced. "
                    "Delete the bound pipeline (or the index it writes to) and rerun; "
                    "executing it as-is would enrich from the wrong source.".format(
                        name, key, found, wanted
                    )
                )
                return False

        self.logger.info(
            "Reusing existing policy {} — its definition already matches config".format(name)
        )
        return True

    def _execute_policy(self, enrich_client, name, match_dicts) -> bool:
        """Rebuild the policy's enrich index and confirm it actually received data.

        The verification is the point: an enrich policy executed against a
        source index whose documents are not yet searchable succeeds, reports
        `acknowledged`, and produces an empty enrich index. Nothing downstream
        errors — the enriched documents simply come out missing the fields, and
        a sweep over them returns a plausible-looking zero. Comparing the
        resulting count against the source's turns that into a loud failure.
        """
        try:
            enrich_client.execute_policy(name=name, wait_for_completion=True)
        except Exception as e:
            self.logger.error("Failed to execute policy {}: {}".format(name, e))
            return False

        source_count = self._count(match_dicts.get("indices"))
        enrich_count = self._count(".enrich-{}".format(name))

        if source_count > 0 and enrich_count == 0:
            self.logger.error(
                "Policy {} executed but its enrich index is EMPTY while source {} holds "
                "{} documents. Enrichment reads only searchable documents, so the source "
                "was almost certainly not refreshed before this ran. Anything enriched "
                "from this policy will silently lack its fields.".format(
                    name, match_dicts.get("indices"), source_count
                )
            )
            return False

        self.logger.info(
            "Policy {} executed: {} documents in enrich index (source {})".format(
                name, enrich_count, source_count
            )
        )
        return True

    def _count(self, index) -> int:
        """Document count for an index, or 0 when it cannot be counted.

        Used only to compare source against enrich output, so an unreadable
        index is reported as 0 rather than raising — the caller's job is to
        flag a suspicious result, not to fail the run on a count call.
        """
        if not index:
            return 0
        try:
            return self.es.count(index=index)["count"]
        except Exception as e:
            self.logger.debug("Could not count {}: {}".format(index, e))
            return 0
