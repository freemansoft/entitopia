"""Candidate retrieval and analyzed-token fetching.

Two round trips per predecessor: one bool.should query to pull candidates, one
_mtermvectors call to fetch the analyzed tokens for the predecessor and every
candidate.

Tokens come from Elasticsearch rather than being recomputed in Python so that
scoring always sees exactly what the index sees — no risk of a local
double-metaphone implementation drifting from the plugin's.
"""

import logging

from matching.documents import EntityDoc
from matching.signals import build_signal

logger = logging.getLogger(__name__)


class CandidateFinder:
    """Finds successor candidates for a predecessor and fetches their tokens.

    Scoring (matching/signals.py) needs analyzed tokens, not raw _source, but
    calling _analyze per string would be one call per field per carrier across
    a 340,000-carrier sweep. Retrieving candidates with a search and then
    fetching every candidate's tokens in a single _mtermvectors call keeps the
    per-predecessor cost at exactly two Elasticsearch round trips.
    """

    def __init__(
        self,
        es,
        source_index,
        candidates_config,
        signal_configs,
        entity_config=None,
        lifecycle=None,
    ):
        """Bind to the ES client/index and the configured signals.

        signal_configs drives both which fields seed the candidate search and
        which "field.subfield" pairs get fetched by _mtermvectors, so this
        class stays in sync with scorer.py's signal list without duplicating
        it. Both are answered by the signals themselves rather than by type
        checks here.
        """
        self.es = es
        self.source_index = source_index
        # The column this project calls its entity's identity. Defaulted only
        # so this change does not have to update every caller at once; the
        # entity block becomes required once every shipped config declares it.
        self.entity_key_field = getattr(entity_config, "key", "dot_number")
        self.max_candidates = int(getattr(candidates_config, "max_candidates", 100))
        self.seed_signals = set(getattr(candidates_config, "seed_signals", []) or [])
        self.signal_configs = list(signal_configs)
        # Built from the same configs scorer.py uses, so retrieval and scoring
        # can never disagree about what a signal reads.
        # lifecycle is threaded through even though no dated signal seeds a
        # candidate query: the signals are built from the whole config list, so
        # a temporal entry is constructed here too and would refuse without it.
        self.signals = [build_signal(c, lifecycle) for c in self.signal_configs]

    def scored_subfields(self) -> set[str]:
        """Every "field.subfield" the configured signals read tokens from."""
        wanted = set()
        for signal in self.signals:
            wanted |= signal.token_subfields()
        return wanted

    def _seed_clauses(self, source, ctx=None):
        """Build the bool.should clauses that pull candidates for one predecessor.

        Restricted to seed_signals so an operator's config choice stays
        authoritative: a signal not trusted to seed cannot silently widen the
        search. A signal that is listed but declines to seed (returning no
        clauses, as AgentSignal does) is simply skipped rather than raising.

        This method used to hold a whitelist of seedable types plus one helper
        per type, which meant candidate retrieval knew about phone numbers and
        vehicle identifiers. Asking each signal for its own clauses moves that
        knowledge to where it belongs and makes seeding on a new kind of
        evidence a signal-level change, not an edit here.
        """
        clauses = []
        for signal in self.signals:
            if signal.signal_type not in self.seed_signals:
                continue
            clauses.extend(signal.seed_clauses(source, ctx))
        return clauses

    def find(self, pred_hit, ctx=None):
        """Retrieve successor candidates for one predecessor hit and their tokens.

        Returns (predecessor_doc, candidate_docs, truncated). Excludes the
        predecessor itself via must_not on _id so a shutdown carrier never
        appears as its own candidate successor. truncated signals the
        candidate search hit max_candidates, so a caller weighing recall can
        tell "no more matches existed" apart from "the cap cut this search
        off"; the value is derived from the query's own page size rather than
        a separate count so it can never disagree with what was actually
        returned.
        """
        pred_id = pred_hit["_id"]
        pred_source = pred_hit["_source"]

        clauses = self._seed_clauses(pred_source, ctx)
        if not clauses:
            logger.debug("No seed clauses for predecessor %s", pred_id)
            return None, [], False

        response = self.es.search(
            index=self.source_index,
            size=self.max_candidates,
            query={
                "bool": {
                    "should": clauses,
                    "minimum_should_match": 1,
                    "must_not": [{"ids": {"values": [pred_id]}}],
                }
            },
            track_total_hits=False,
        )
        hits = response["hits"]["hits"]
        truncated = len(hits) >= self.max_candidates

        all_ids = [pred_id] + [h["_id"] for h in hits]
        tokens_by_id = self._fetch_tokens(all_ids)

        pred_doc = to_entity_doc(
            pred_hit, tokens_by_id.get(pred_id, {}), self.entity_key_field
        )
        cand_docs = [
            to_entity_doc(h, tokens_by_id.get(h["_id"], {}), self.entity_key_field)
            for h in hits
        ]
        return pred_doc, cand_docs, truncated

    def _fetch_tokens(self, doc_ids):
        """One _mtermvectors call for every id, keyed back by document _id.

        Batching the predecessor and all of its candidates into a single call
        is what keeps a sweep at two round trips per predecessor instead of
        one per candidate; term vectors are generated on the fly here rather
        than stored, so this stays cheap without a term_vector mapping
        setting that would otherwise bloat the index (notably the
        Beider-Morse phonetic subfield).
        """
        fields = sorted(self.scored_subfields())
        if not fields or not doc_ids:
            return {}

        response = self.es.mtermvectors(
            index=self.source_index,
            ids=doc_ids,
            fields=fields,
            term_statistics=False,
            field_statistics=False,
            positions=False,
            offsets=False,
            payloads=False,
        )

        tokens_by_id = {}
        for doc in response.get("docs", []):
            doc_tokens = {}
            for field_name, field_data in (doc.get("term_vectors") or {}).items():
                doc_tokens[field_name] = set((field_data.get("terms") or {}).keys())
            tokens_by_id[doc["_id"]] = doc_tokens
        return tokens_by_id



def to_entity_doc(hit, tokens, key_field):
    """Combine a search hit's _source with its fetched tokens into an EntityDoc.

    `key_field` is configuration rather than a literal: it is whichever column
    a project calls its entity's identity, and framework code cannot know it.

    Public rather than private because it is the single place the configured
    key is applied to a raw hit, so it is the thing worth testing directly.

    Falls back to the Elasticsearch _id when _source lacks that column (the
    probe documents in a near-empty dev index do), so this stays usable
    against a sparsely-populated index rather than raising on test data.
    Stringified because the same logical key arrives as a JSON integer from
    some indexes and a string from others, and a pair keyed on it must not
    depend on which.
    """
    source = hit["_source"]
    return EntityDoc(
        entity_key=str(source.get(key_field, hit["_id"])),
        source=source,
        tokens=tokens,
    )
