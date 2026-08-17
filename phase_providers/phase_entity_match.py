"""entity-match phase: sweep shut-down carriers for likely successors.

The theme of the error handling here is converting silent wrong output into
loud failure. Every bug documented in this repo's README is of that shape: a
phase logs acknowledged/True, nothing errors, and the output is quietly wrong.
"""

import dataclasses
import datetime
import json
import logging
import uuid
from types import SimpleNamespace

from elasticsearch.helpers import parallel_bulk

from matching.candidates import CandidateFinder
from matching.documents import ScoringContext
from matching.population import PopulationSelector
from matching.scorer import PairScorer
from matching.signals import build_signal, parse_flexible_date
from utils import analysis_fingerprint, elasticsearch_utils, file_utils, id_utils

AGENT_TERMS_SIZE = 500
# Fallback for how many records may share a value before it stops identifying
# anything, when neither config nor the signal says. Deliberately small: this
# only applies to fields a signal scores as exact identity, where sharing is
# the exception.
DEFAULT_SHARED_LIMIT = 5
# Buckets to pull per field when scanning for non-identifying values. Sized
# against the measurement rather than guessed: on the July 2026 extract exactly
# 200 email addresses sit on more than 20 carriers, so the previous value of 200
# was precisely at its own ceiling and one more shared address would have been
# missed silently. Contact fields have a much longer tail than VIN placeholders
# because every filing service and corporate parent contributes one.
SUPPRESSION_TERMS_SIZE = 2000
BULK_THREAD_COUNT = 2


@dataclasses.dataclass(frozen=True)
class RunProvenance:
    """Where a stored pair came from: which sweep, when, and over which tokens.

    Provenance in the archival sense — the record of origin. A pair carries it
    so that a reader months later can still say what its score means without
    the run that produced it being reconstructable. On its own a total_score of
    0.83 is unfalsifiable: nothing on the document says which sweep emitted it,
    whether the index behind it still exists, or whether today's analyzers
    would even produce those tokens. Each field answers one of the questions a
    stored pair actually gets asked:

    - run_id — which sweep emitted it, so its siblings can be found and two
      runs compared. One value per sweep, not per pair.
    - generated_at — when that sweep ran, since the corpus moves underneath.
    - source_index — which index it was swept from, resolved to the concrete
      name. See for_sweep below: the configured name is usually an alias, and
      an alias does not pin anything.
    - source_alias — the name configuration asked for, when that differs.
    - analysis_fingerprint — which analyzers tokenized that index, since a name
      or address score means nothing apart from the tokens behind it.

    Grouped into one object because these values are decided once per run and
    travel together onto every document; passing them individually pushed
    _generate_actions past the argument limit and, more to the point, invited a
    future field to be added to the document but not to the generator that
    fills it.

    analysis_fingerprint is the stamp read off the *source* index, not one
    computed from config, because the question a stored pair has to answer is
    which analyzers actually produced its tokens. None means the source index
    predates the stamp — unknown, and left off the document rather than
    guessed.
    """

    run_id: str
    generated_at: str
    source_index: str
    source_alias: str | None = None
    analysis_fingerprint: str | None = None

    @classmethod
    def for_sweep(cls, configured_index, resolved_index, analysis_fingerprint):
        """Identity for one sweep, keyed to the index actually read.

        entity-match.json names `carriers-000001`, which is an **alias**, and
        every rebuild repoints it at a new dated index. Stamping that name
        would answer "which index produced this pair?" with something that
        means a different index next month — the exact ambiguity the stamp
        exists to remove. The concrete name comes free from the mapping read in
        _preflight, so it is what lands on the document, with the configured
        name kept alongside as source_alias.

        source_alias is omitted when the two agree, rather than duplicated: a
        project may legitimately configure a concrete index, and a field that
        merely repeats its neighbour trains readers to skip both.

        Falls back to the configured name when resolution produced nothing —
        an unresolvable source index is a wrong answer, but a blank one is
        worse, since it leaves a pair unable to say even which name was asked
        for.
        """
        resolved = resolved_index or configured_index
        return cls(
            run_id=uuid.uuid4().hex,
            generated_at=datetime.datetime.now(datetime.UTC).isoformat(),
            source_index=resolved,
            source_alias=configured_index if resolved != configured_index else None,
            analysis_fingerprint=analysis_fingerprint,
        )


class PhaseEntityMatch:
    """Sweeps shut-down carriers for likely successors and writes ranked pairs.

    This is the phase Tasks 1-12 were built for: PopulationSelector picks the
    starting population, CandidateFinder retrieves and tokenizes candidate
    successors, PairScorer turns each pair into a scored, explainable verdict,
    and this class is the only piece that ties them to a live index and
    writes the result. Everything else in matching/ is a pure library with no
    Elasticsearch side effects of its own.
    """

    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)
        # Replaced by handle() from the loaded entity-match.json. Defaulted
        # here so _to_action stays callable on a bare instance rather than
        # raising AttributeError depending on which method ran first -- a
        # pair document built from an empty block carries only entity_key,
        # which is a degraded document rather than a crash.
        self.entity_config = SimpleNamespace()

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )

        config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "entity-match.json",
        )
        if not config:
            self.logger.error("No entity-match.json for step {}".format(self.one_step))
            return

        index_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        if not index_config:
            self.logger.error("No index-config.json for step {}".format(self.one_step))
            return
        elasticsearch_utils.replace_index_with_now_version(index_config)

        source_index = config.source_index
        # Overrides the __init__ default. Held on the instance because
        # _to_action needs it several call frames below, and threading it
        # through the generator would put a config object in the signature of
        # every intermediate method.
        self.entity_config = getattr(config, "entity", SimpleNamespace())
        scorer = PairScorer(config.signals, config.scoring)
        finder = CandidateFinder(
            self.es,
            source_index,
            config.candidates,
            config.signals,
            entity_config=self.entity_config,
        )
        selector = PopulationSelector(self.es, source_index, config.population)

        ok, resolved_index, source_fingerprint = self._preflight(
            source_index, finder.scored_subfields(), self._expected_analysis_fingerprint(config)
        )
        if not ok:
            return

        ctx = self._build_context(source_index, config.signals, config)
        max_pairs = int(getattr(config.scoring, "max_pairs_per_predecessor", 10))
        provenance = RunProvenance.for_sweep(
            source_index, resolved_index, source_fingerprint
        )
        self._stamp_provenance(index_config.index, provenance)

        stats = {
            "predecessors": 0,
            "candidates": 0,
            "pairs": 0,
            "truncated": 0,
            "errors": 0,
        }

        actions = self._generate_actions(
            selector, finder, scorer, ctx, index_config.index, max_pairs,
            provenance, stats,
        )

        indexed = 0
        for success, response in parallel_bulk(
            client=self.es,
            thread_count=BULK_THREAD_COUNT,
            actions=actions,
            raise_on_error=False,
            raise_on_exception=False,
        ):
            if success:
                indexed += 1
            else:
                stats["errors"] += 1
                self.logger.error("Failed to index pair: {}".format(response))

        self.logger.info(
            "entity-match complete: {} predecessors, {} candidates examined, "
            "{} pairs emitted, {} indexed, {} truncated candidate sets, {} errors".format(
                stats["predecessors"], stats["candidates"], stats["pairs"],
                indexed, stats["truncated"], stats["errors"],
            )
        )
        if indexed == 0:
            self.logger.warning(
                "entity-match produced NO pairs. Check that {} is populated and "
                "that min_total_score ({}) is not set too high.".format(
                    source_index, scorer.min_total_score
                )
            )
        if stats["truncated"]:
            self.logger.warning(
                "{} predecessors hit the max_candidates ceiling; real matches "
                "may have been cut off".format(stats["truncated"])
            )

    def _preflight(self, source_index, required_subfields, expected_fingerprint):
        """Fail loudly before sweeping rather than emitting a silently empty result.

        Running against an older carriers index that lacks .phonetic_bm would
        make _mtermvectors return nothing for that field, turn every phonetic
        score into None, and produce an empty result set with no error anywhere.

        Returns (ok, resolved_index, source_fingerprint). Both are returned
        rather than only logged because they are what the emitted pairs are
        stamped with, and this is the only place the source index's mapping is
        read — reading it twice would let the values checked and the values
        recorded drift apart on a long sweep.
        """
        try:
            self.es.indices.refresh(index=source_index)
        except Exception as e:
            self.logger.error("Cannot refresh source index {}: {}".format(source_index, e))
            return False, None, None

        count = self.es.count(index=source_index)["count"]
        if count == 0:
            self.logger.error("Source index {} is empty; nothing to sweep".format(source_index))
            return False, None, None
        self.logger.info("Sweeping against {} ({} documents)".format(source_index, count))

        mapping = self.es.indices.get_mapping(index=source_index)
        properties = {}
        stored_fingerprint = None
        resolved_index = None
        # The response is keyed by *concrete* index name even when queried
        # through an alias, which is the only place that name is available
        # without a second round trip — and the configured name is normally an
        # alias, so it is the concrete one the pairs need.
        if len(mapping.body) > 1:
            # A source alias fanning out over several indexes is the accumulated
            # -alias bug the creation phase's retain_aliases flag exists to
            # avoid, not a supported configuration: the sweep reads all of them
            # but can only attribute its pairs to one.
            self.logger.warning(
                "Source {} resolves to {} indexes ({}); pairs will be attributed to "
                "the first alone. An alias spanning several indexes is normally "
                "left over from a previous load.".format(
                    source_index, len(mapping.body), ", ".join(sorted(mapping.body))
                )
            )
        for concrete_index, index_mapping in mapping.body.items():
            resolved_index = concrete_index
            properties = index_mapping.get("mappings", {}).get("properties", {})
            stored_fingerprint = (
                index_mapping.get("mappings", {}).get("_meta", {}).get("analysis_fingerprint")
            )
            break

        self._check_analysis_fingerprint(source_index, stored_fingerprint, expected_fingerprint)

        missing = []
        for subfield_path in sorted(required_subfields):
            field_name, _, subfield = subfield_path.partition(".")
            field_def = properties.get(field_name, {})
            if subfield not in (field_def.get("fields") or {}):
                missing.append(subfield_path)

        if missing:
            self.logger.error(
                "Source index {} is missing scored subfields: {}. Recreate and "
                "reload the carriers index with the updated index-settings.json "
                "and index-mappings.json.".format(source_index, ", ".join(missing))
            )
            return False, resolved_index, stored_fingerprint
        return True, resolved_index, stored_fingerprint

    def _stamp_provenance(self, target_index, provenance):
        """Record on the candidates index which index's tokens its pairs were scored from.

        The pairs are the durable artifact and the fingerprint check above is
        only a log line, so without this a pair on disk cannot answer "which
        analyzers produced these scores?" once the run's log is gone — and the
        published figures that cite a fingerprint stop being verifiable the
        moment the source index is pruned, silently, because the scores still
        look fine.

        Stamped here rather than by phase_index_creation because the candidates
        index has no analyzers of its own to hash; the value is borrowed from
        the source index, which is why it is stored under source_* keys. A
        reader that took it for this index's own analysis fingerprint would
        compare it against the wrong config and report a mismatch on every run.

        Deliberately not fatal: an unstamped index is worse than a stamped one
        but far better than discarding a sweep that takes hours, and every pair
        carries the same values in its own document anyway.
        """
        meta = {"source_index": provenance.source_index}
        if provenance.source_alias:
            meta["source_alias"] = provenance.source_alias
        if provenance.analysis_fingerprint:
            meta["source_analysis_fingerprint"] = provenance.analysis_fingerprint
        try:
            self.es.indices.put_mapping(index=target_index, meta=meta)
            self.logger.info(
                "Stamped {} with source index {} (analysis fingerprint {})".format(
                    target_index,
                    provenance.source_index,
                    provenance.analysis_fingerprint or "unknown",
                )
            )
        except Exception as e:
            self.logger.error(
                "Could not stamp source provenance onto {} ({}); the index will not "
                "record which analyzers scored its pairs, though the pair "
                "documents still will".format(target_index, e)
            )

    def _check_analysis_fingerprint(self, source_index, stored, expected):
        """Report, without blocking, that the index's tokens predate the analyzers.

        Deliberately advisory and always returns True. Sweeping an older index on
        purpose is legitimate — comparing two runs, reproducing a past result —
        so refusing would block real work. What is unacceptable is doing it
        unknowingly: every name and address score would be derived from tokens
        the current configuration would no longer produce, and no other check in
        _preflight can see that, because the subfields still exist and still
        hold data.

        A missing stored value means the index predates the stamp, which is
        unknown rather than wrong. Reporting that as a mismatch would train the
        operator to ignore the message that matters.
        """
        if expected is None:
            return True
        if stored is None:
            self.logger.warning(
                "Source index {} carries no analysis fingerprint, so it cannot be "
                "checked against the analyzers on disk (expected {}). It predates "
                "this check; recreate and reload it to enable the "
                "comparison.".format(source_index, expected)
            )
            return True
        if stored != expected:
            self.logger.error(
                "Source index {} was built with analysis fingerprint {} but the "
                "configuration on disk is {}. Every token-based score in this "
                "sweep comes from the OLDER analyzers. Recreate and reload the "
                "index to score against current config; continuing "
                "anyway.".format(source_index, stored, expected)
            )
        return True

    def _expected_analysis_fingerprint(self, config):
        """Fingerprint of the index-settings.json + index-mappings.json that should
        have built the source index.

        entity-match.json runs in its own step, so it cannot find the source
        index's settings without being told which step owns them — hence the
        optional source_settings_step key. Absent, the check is skipped rather
        than guessed at, so projects that never adopt the key are unaffected.

        Must load index-mappings.json from that same step and pass its
        properties through, mirroring exactly what phase_index_creation hashed
        when it stamped the index — the two sides comparing different inputs
        would report a mismatch on every run regardless of whether the
        analyzers actually changed, which is worse than not checking at all.
        """
        step = getattr(config, "source_settings_step", None)
        if not step:
            self.logger.debug("No source_settings_step configured; skipping fingerprint check")
            return None
        settings_config = file_utils.load_from_project_file(
            self.project, self.project_config.configurationDir, step, "index-settings.json"
        )
        if not settings_config or not getattr(settings_config, "settings", None):
            return None
        settings = json.loads(json.dumps(settings_config.settings, default=vars))

        mapping_config = file_utils.load_from_project_file(
            self.project, self.project_config.configurationDir, step, "index-mappings.json"
        )
        mapping_properties = None
        if mapping_config and getattr(mapping_config, "mappings", None):
            properties = getattr(mapping_config.mappings, "properties", None)
            if properties is not None:
                mapping_properties = json.loads(json.dumps(properties, default=vars))

        return analysis_fingerprint.fingerprint_analysis(settings, mapping_properties)

    def _declared_ignored_values(self, config):
        """Read entity-match.json's ignore_values into a field -> values map.

        Exists so an operator can name a placeholder outright instead of
        relying on it being common enough for the frequency scan to catch.
        The two mechanisms cover different failures: frequency catches junk
        nobody knew about, while a declaration catches a value that is
        obviously meaningless but rare enough to slip under the threshold, and
        documents the intent for the next reader.

        Keyed by field path, with "*" applying to every field, because a value
        that is junk in one attribute can be legitimate in another — "0" is
        not a VIN but is a fine street number.
        """
        declared = getattr(config, "ignore_values", None)
        if declared is None:
            return {}
        # Config loads through SimpleNamespace, so the field paths are
        # attribute names on that namespace rather than dict keys.
        as_dict = declared if isinstance(declared, dict) else vars(declared)
        ignored = {}
        for field_path, values in as_dict.items():
            if not values:
                continue
            ignored[field_path] = {str(v).strip().lower() for v in values}
        if ignored:
            self.logger.info(
                "Config declares ignored values on {} field(s): {}".format(
                    len(ignored), ", ".join(sorted(ignored))
                )
            )
        return ignored

    def _shared_limits(self, config):
        """Read max_shared_records from config into a field -> limit map.

        Separate from ignore_values because the two answer different questions:
        that list names values already known to be meaningless, while this sets
        how many records may share an *unknown* value before the sweep decides
        it cannot be identifying. A dataset needs both — nobody can enumerate
        every filing service in advance.
        """
        declared = getattr(config, "max_shared_records", None)
        if declared is None:
            return {}
        as_dict = declared if isinstance(declared, dict) else vars(declared)
        limits = {k: int(v) for k, v in as_dict.items()}
        if limits:
            self.logger.info(
                "Shared-value limits from config: {}".format(
                    ", ".join("{}={}".format(k, v) for k, v in sorted(limits.items()))
                )
            )
        return limits

    def _suppressed_values(self, source_index, signals, limits):
        """Find values the corpus proves are not identifying, per field.

        Every signal that scores a shared value 1.0 is asserting that the value
        picks out one thing in the world. Real data violates that constantly,
        in two different ways that need the same treatment:

        - Outright placeholders. FMCSA crash reports carry the literal VIN
          "GGGG" on 158 carriers, "UNKNOWN" on 79, "99999999999999999" on 51,
          and the phone "(000) 000-0000" on 664.
        - Values that are entirely correct but shared. A permit-filing service,
          an insurance agency or a corporate parent puts its own phone or email
          on every carrier it files for, so one address can legitimately cover
          hundreds of unrelated carriers. These cannot be "cleaned" — the data
          is right; it just is not identity evidence.

        Both are found the same way and neither can be enumerated in advance,
        which is why this is derived from the corpus rather than hard-coded.
        The declared ignore_values list in config covers the remainder: values
        an operator knows are meaningless but which are too rare to trip the
        threshold.
        """
        suppressed = {}
        for signal in signals:
            for source_path, agg_field in signal.exact_evidence_fields():
                limit = self._shared_limit(source_path, limits, signal)
                try:
                    response = self.es.search(
                        index=source_index,
                        size=0,
                        aggs={
                            "vals": {
                                "terms": {
                                    "field": agg_field,
                                    "size": SUPPRESSION_TERMS_SIZE,
                                    "min_doc_count": limit + 1,
                                }
                            }
                        },
                    )
                except Exception as e:
                    self.logger.warning(
                        "Could not gather value frequencies for {} ({}); values shared by "
                        "unrelated records - placeholders, filing services, corporate "
                        "parents - will score as identity matches".format(agg_field, e)
                    )
                    continue
                found = {
                    str(b["key"]).strip().lower()
                    for b in response["aggregations"]["vals"]["buckets"]
                }
                if found:
                    suppressed.setdefault(source_path, set()).update(found)
                    self.logger.info(
                        "{}: {} value(s) on more than {} records are not identifying "
                        "(e.g. {})".format(
                            source_path, len(found), limit, sorted(found)[:3]
                        )
                    )
        return suppressed

    def _shared_limit(self, source_path, limits, signal):
        """How many records may share a value before it stops being identifying.

        Resolved most specific first - per-field, then the "*" default, then the
        signal's own setting - because the right number is a property of the
        attribute, not of the sweep. Two sibling carriers legitimately share a
        phone, so a phone tolerates a handful; a VIN identifies one vehicle, so
        even a few is already suspicious.
        """
        if source_path in limits:
            return int(limits[source_path])
        if "*" in limits:
            return int(limits["*"])
        return int(getattr(signal.config, "max_shared_carriers", DEFAULT_SHARED_LIMIT))


    def _build_context(self, source_index, signal_configs, config=None):
        """Gather corpus statistics once per sweep: agent rarity and ignored values."""
        signals = [build_signal(c) for c in signal_configs]
        declared_ignored = self._declared_ignored_values(config) if config else {}
        limits = self._shared_limits(config) if config else {}
        suppressed = self._suppressed_values(source_index, signals, limits)
        for field_path, values in declared_ignored.items():
            suppressed.setdefault(field_path, set()).update(values)
        agent_config = next((c for c in signal_configs if c.type == "agent"), None)
        if agent_config is None:
            return ScoringContext(ignored_values=suppressed)

        keyword_field = "{}.keyword".format(agent_config.name_field)
        try:
            response = self.es.search(
                index=source_index,
                size=0,
                aggs={
                    "agents": {"terms": {"field": keyword_field, "size": AGENT_TERMS_SIZE}}
                },
            )
        except Exception as e:
            self.logger.warning(
                "Could not gather agent frequencies ({}); agent signal has no corpus "
                "to weight against and will score every shared agent at 0.0 (no "
                "discriminating power) rather than fabricate a rarity value".format(e)
            )
            return ScoringContext(ignored_values=suppressed)

        buckets = response["aggregations"]["agents"]["buckets"]
        counts = {b["key"].strip().lower(): b["doc_count"] for b in buckets}
        total = sum(counts.values())
        if total == 0:
            # A zero-agent corpus is the signature of enrichment having
            # silently produced nothing (see the README's documented enrich
            # bugs) rather than a legitimately agent-free carrier population.
            self.logger.warning(
                "Loaded 0 distinct BOC-3 agents; boc3_agents enrichment may not "
                "have run. The agent signal will score every shared agent at 0.0."
            )
        else:
            self.logger.info(
                "Loaded {} distinct BOC-3 agents covering {} carrier filings".format(
                    len(counts), total
                )
            )
        return ScoringContext(
            agent_counts=counts,
            total_agent_carriers=total,
            ignored_values=suppressed,
        )

    def _generate_actions(
        self, selector, finder, scorer, ctx, target_index, max_pairs,
        provenance, stats,
    ):
        """Yield bulk-index actions for every kept pair, one predecessor at a time.

        A generator (rather than building a list) keeps this streaming into
        parallel_bulk so a sweep of hundreds of thousands of predecessors
        never holds every scored pair in memory at once.
        """
        seen_pairs = set()

        for pred_hit in selector.iterate():
            stats["predecessors"] += 1
            try:
                pred_doc, cand_docs, truncated = finder.find(pred_hit, ctx)
            except Exception as e:
                stats["errors"] += 1
                self.logger.error(
                    "Candidate lookup failed for {}: {}".format(pred_hit["_id"], e)
                )
                continue

            if pred_doc is None:
                continue
            stats["candidates"] += len(cand_docs)
            if truncated:
                stats["truncated"] += 1

            scored = []
            for cand_doc in cand_docs:
                try:
                    pair = scorer.score_pair(pred_doc, cand_doc, ctx)
                except Exception as e:
                    stats["errors"] += 1
                    self.logger.error(
                        "Scoring failed for {} -> {}: {}".format(
                            pred_doc.entity_key, cand_doc.entity_key, e
                        )
                    )
                    continue
                if pair is not None:
                    scored.append(pair)

            scored.sort(key=lambda p: p.total_score, reverse=True)
            for pair in scored[:max_pairs]:
                key = (pair.predecessor.entity_key, pair.successor.entity_key)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                stats["pairs"] += 1
                yield self._to_action(pair, target_index, provenance)

    def _to_action(self, pair, target_index, provenance):
        """Build one bulk-index action for a scored pair.

        _id is deterministic (predecessor+successor DOT numbers) so re-running
        a sweep overwrites the same pair's document instead of accumulating
        duplicates across runs, matching how index-populate keys carrier
        documents.

        The provenance fields travel on the document, not only on the index,
        because a pair is routinely read on its own — pulled by _id, exported
        into a review sample, quoted in a README — and at that point the index's
        _meta is not in the reader's hands. A pair with no fingerprint is a pair
        whose source index predates the stamp, which is unknown rather than
        wrong, so the field is omitted rather than filled with a placeholder
        that would later read as a real value.
        """
        pred = pair.predecessor
        succ = pair.successor

        shutdown = _latest_iso(pred.value("out_of_service_orders.oos_date"))
        registered = _latest_iso(succ.value("add_date"))
        gap_days = None
        if shutdown and registered:
            gap_days = (
                parse_flexible_date(registered) - parse_flexible_date(shutdown)
            ).days

        pred_extra = {}
        succ_extra = {}
        if shutdown is not None:
            pred_extra["shutdown_date"] = shutdown
            reason = pred.value("out_of_service_orders.oos_reason")
            pred_extra["shutdown_reason"] = (
                reason[0] if isinstance(reason, list) else reason
            )
        if registered is not None:
            succ_extra["add_date"] = registered

        document = {
            "predecessor": _entity_summary(pred, self.entity_config, pred_extra),
            "successor": _entity_summary(succ, self.entity_config, succ_extra),
            "total_score": round(pair.total_score, 6),
            "gap_days": gap_days,
            "signals_present": pair.signals_present,
            "matched_on": pair.matched_on,
            "signals": [
                {
                    "signal_type": c.signal_type,
                    "subfield": c.subfield,
                    "weight": c.weight,
                    "score": round(c.score, 6),
                    "contribution": round(c.contribution, 6),
                }
                for c in pair.signals
            ],
            "run_id": provenance.run_id,
            "generated_at": provenance.generated_at,
            "source_index": provenance.source_index,
        }
        if provenance.source_alias:
            document["source_alias"] = provenance.source_alias
        if provenance.analysis_fingerprint:
            document["analysis_fingerprint"] = provenance.analysis_fingerprint
        return {
            "_index": target_index,
            "_id": id_utils.compute_id(
                {"p": pred.entity_key, "s": succ.entity_key}, ["p", "s"]
            ),
            "_source": document,
        }


def _entity_summary(doc, entity_config, extra=None):
    """Trim an EntityDoc to the human-facing fields a reviewer needs to judge a pair.

    The output document exists to be read by a person deciding whether a
    flagged pair is real, not to carry the full record already available in
    the source index; keeping this list short is what keeps a reviewed hit
    list scannable. Which fields those are is a project's own decision, so it
    comes from entity.summary_fields rather than being fixed here -- the
    previous fixed list named FMCSA columns, so any other project's pairs
    would have carried five keys holding nulls.

    Both `entity_key` and the project's own label are emitted when a label is
    configured. Both rather than either, for the reason the provenance work
    already established about source_index: a pair is routinely read on its
    own, and at that point the project config is not in the reader's hands.
    Generic tooling reads entity_key without loading config, while the
    labelled copy keeps existing project scripts, committed baselines and
    README figures working unchanged.

    A configured field that is absent is emitted as None rather than dropped,
    so a reviewer can tell "asked for and empty" from "never asked for".
    """
    summary = {"entity_key": doc.entity_key}
    label = getattr(entity_config, "key_label", None)
    if label:
        summary[label] = doc.entity_key
    for path in getattr(entity_config, "summary_fields", None) or []:
        summary[path] = doc.value(path)
    if extra:
        summary.update(extra)
    return summary


def _latest_iso(raw):
    """Most recent parseable date from a scalar-or-list _source value, as ISO text.

    Feeds the output document's shutdown_date/add_date and the gap_days
    calculation below; returning ISO text rather than a date object keeps
    this JSON-serializable without a custom encoder.
    """
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    dates = [d for d in (parse_flexible_date(i) for i in items) if d is not None]
    return max(dates).isoformat() if dates else None
