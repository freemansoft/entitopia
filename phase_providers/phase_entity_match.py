"""entity-match phase: sweep shut-down carriers for likely successors.

The theme of the error handling here is converting silent wrong output into
loud failure. Every bug documented in this repo's README is of that shape: a
phase logs acknowledged/True, nothing errors, and the output is quietly wrong.
"""

import datetime
import logging
import uuid

from elasticsearch.helpers import parallel_bulk

from matching.candidates import CandidateFinder
from matching.documents import ScoringContext
from matching.predecessors import PredecessorSelector
from matching.scorer import PairScorer
from matching.signals import parse_flexible_date
from utils import elasticsearch_utils, file_utils, id_utils

AGENT_TERMS_SIZE = 500
# Placeholder values are a short head of a long tail -- a handful of literals
# ("UNKNOWN", "GGGG", runs of 9s) cover essentially all of them, so a small
# bucket count catches them without paying for a full cardinality sweep.
SUPPRESSION_TERMS_SIZE = 200
BULK_THREAD_COUNT = 2


class PhaseEntityMatch:
    """Sweeps shut-down carriers for likely successors and writes ranked pairs.

    This is the phase Tasks 1-12 were built for: PredecessorSelector picks the
    "shut down" population, CandidateFinder retrieves and tokenizes candidate
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
        scorer = PairScorer(config.signals, config.scoring)
        finder = CandidateFinder(
            self.es, source_index, config.candidates, config.signals
        )
        selector = PredecessorSelector(self.es, source_index, config.predecessors)

        if not self._preflight(source_index, finder.scored_subfields()):
            return

        ctx = self._build_context(source_index, config.signals)
        max_pairs = int(getattr(config.scoring, "max_pairs_per_predecessor", 10))
        run_id = uuid.uuid4().hex
        generated_at = datetime.datetime.now(datetime.UTC).isoformat()

        stats = {
            "predecessors": 0,
            "candidates": 0,
            "pairs": 0,
            "truncated": 0,
            "errors": 0,
        }

        actions = self._generate_actions(
            selector, finder, scorer, ctx, index_config.index, max_pairs,
            run_id, generated_at, stats,
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

    def _preflight(self, source_index, required_subfields):
        """Fail loudly before sweeping rather than emitting a silently empty result.

        Running against an older carriers index that lacks .phonetic_bm would
        make _mtermvectors return nothing for that field, turn every phonetic
        score into None, and produce an empty result set with no error anywhere.
        """
        try:
            self.es.indices.refresh(index=source_index)
        except Exception as e:
            self.logger.error("Cannot refresh source index {}: {}".format(source_index, e))
            return False

        count = self.es.count(index=source_index)["count"]
        if count == 0:
            self.logger.error("Source index {} is empty; nothing to sweep".format(source_index))
            return False
        self.logger.info("Sweeping against {} ({} documents)".format(source_index, count))

        mapping = self.es.indices.get_mapping(index=source_index)
        properties = {}
        for index_mapping in mapping.body.values():
            properties = index_mapping.get("mappings", {}).get("properties", {})
            break

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
            return False
        return True

    def _suppressed_tokens(self, source_index, signal_configs):
        """Find "unique" token values that the corpus proves are not unique.

        SharedTokenSignal scores a shared value 1.0 on the premise that it
        identifies one physical thing worldwide. FMCSA crash reports violate
        that premise constantly: the literal VIN "GGGG" appears on 158
        carriers, "UNKNOWN" on 79, "99999999999999999" on 51. Left alone,
        every pair of carriers that both filed a placeholder scores a perfect
        identity match, and seeding on one retrieves all 158 at once.

        Derived from the data rather than a hard-coded list of known junk
        values, because which placeholders a dataset uses is a property of
        that dataset. Any value attached to more than max_shared_carriers
        carriers is treated as non-identifying, which is the signal's own
        premise applied as a test.
        """
        suppressed = set()
        for config in signal_configs:
            if config.type not in ("vin-overlap", "shared-token"):
                continue
            limit = int(getattr(config, "max_shared_carriers", 5))
            for field_name in config.fields:
                try:
                    response = self.es.search(
                        index=source_index,
                        size=0,
                        aggs={
                            "vals": {
                                "terms": {
                                    "field": field_name,
                                    "size": SUPPRESSION_TERMS_SIZE,
                                    "min_doc_count": limit + 1,
                                }
                            }
                        },
                    )
                except Exception as e:
                    self.logger.warning(
                        "Could not gather token frequencies for {} ({}); placeholder "
                        "values like the literal VIN 'UNKNOWN' will score as identity "
                        "matches".format(field_name, e)
                    )
                    continue
                for bucket in response["aggregations"]["vals"]["buckets"]:
                    suppressed.add(str(bucket["key"]).strip().lower())
        if suppressed:
            self.logger.info(
                "Suppressing {} non-unique token values (e.g. {})".format(
                    len(suppressed), sorted(suppressed)[:5]
                )
            )
        return suppressed

    def _build_context(self, source_index, signal_configs):
        """Gather BOC-3 agent frequencies once for IDF weighting."""
        suppressed = self._suppressed_tokens(source_index, signal_configs)
        agent_config = next((c for c in signal_configs if c.type == "agent"), None)
        if agent_config is None:
            return ScoringContext(suppressed_tokens=suppressed)

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
            return ScoringContext(suppressed_tokens=suppressed)

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
            suppressed_tokens=suppressed,
        )

    def _generate_actions(
        self, selector, finder, scorer, ctx, target_index, max_pairs,
        run_id, generated_at, stats,
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
                            pred_doc.dot_number, cand_doc.dot_number, e
                        )
                    )
                    continue
                if pair is not None:
                    scored.append(pair)

            scored.sort(key=lambda p: p.total_score, reverse=True)
            for pair in scored[:max_pairs]:
                key = (pair.predecessor.dot_number, pair.successor.dot_number)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                stats["pairs"] += 1
                yield self._to_action(pair, target_index, run_id, generated_at)

    def _to_action(self, pair, target_index, run_id, generated_at):
        """Build one bulk-index action for a scored pair.

        _id is deterministic (predecessor+successor DOT numbers) so re-running
        a sweep overwrites the same pair's document instead of accumulating
        duplicates across runs, matching how index-populate keys carrier
        documents.
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

        document = {
            "predecessor": _carrier_summary(pred, shutdown_date=shutdown),
            "successor": _carrier_summary(succ, add_date=registered),
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
            "run_id": run_id,
            "generated_at": generated_at,
        }
        return {
            "_index": target_index,
            "_id": id_utils.compute_id(
                {"p": pred.dot_number, "s": succ.dot_number}, ["p", "s"]
            ),
            "_source": document,
        }


def _carrier_summary(doc, shutdown_date=None, add_date=None):
    """Trim a CarrierDoc to the human-facing fields a reviewer needs to judge a pair.

    The output document exists to be read by a person deciding whether a
    flagged pair is a real chameleon, not to carry the full carrier record
    already available in the source index; keeping this list short is what
    keeps a reviewed hit list scannable.
    """
    summary = {
        "dot_number": doc.dot_number,
        "legal_name": doc.value("legal_name"),
        "dba_name": doc.value("dba_name"),
        "phy_street": doc.value("phy_street"),
        "phy_city": doc.value("phy_city"),
        "phy_state": doc.value("phy_state"),
    }
    if shutdown_date is not None:
        summary["shutdown_date"] = shutdown_date
        reason = doc.value("out_of_service_orders.oos_reason")
        summary["shutdown_reason"] = reason[0] if isinstance(reason, list) else reason
    if add_date is not None:
        summary["add_date"] = add_date
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
