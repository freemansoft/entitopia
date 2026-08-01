"""Candidate retrieval and analyzed-token fetching.

Two round trips per predecessor: one bool.should query to pull candidates, one
_mtermvectors call to fetch the analyzed tokens for the predecessor and every
candidate.

Tokens come from Elasticsearch rather than being recomputed in Python so that
scoring always sees exactly what the index sees — no risk of a local
double-metaphone implementation drifting from the plugin's.
"""

import logging

from matching.documents import CarrierDoc

logger = logging.getLogger(__name__)

# Signal types that can seed candidate generation, mapped to how their clauses
# are built. agent is deliberately absent: only 89 distinct BOC-3 agents cover
# 1.43M filings, so seeding on it returns essentially random carriers.
SEEDABLE = {"name-phonetic", "name-token", "address", "exact-identifier"}


class CandidateFinder:
    """Finds successor candidates for a predecessor and fetches their tokens.

    Scoring (matching/signals.py) needs analyzed tokens, not raw _source, but
    calling _analyze per string would be one call per field per carrier across
    a 340,000-carrier sweep. Retrieving candidates with a search and then
    fetching every candidate's tokens in a single _mtermvectors call keeps the
    per-predecessor cost at exactly two Elasticsearch round trips.
    """

    def __init__(self, es, source_index, candidates_config, signal_configs):
        """Bind to the ES client/index and the configured signals.

        signal_configs drives both which fields seed the candidate search
        (via SEEDABLE) and which "field.subfield" pairs get fetched by
        _mtermvectors, so this class stays in sync with scorer.py's signal
        list without duplicating it.
        """
        self.es = es
        self.source_index = source_index
        self.max_candidates = int(getattr(candidates_config, "max_candidates", 100))
        self.seed_signals = set(getattr(candidates_config, "seed_signals", []) or [])
        self.signal_configs = list(signal_configs)

    def scored_subfields(self) -> set[str]:
        """Every "field.subfield" the configured signals read tokens from."""
        wanted = set()
        for config in self.signal_configs:
            if config.type in ("name-phonetic", "name-token"):
                for field_name in config.fields:
                    wanted.add("{}.{}".format(field_name, config.subfield))
            elif config.type == "address":
                for field_name in config.fields:
                    wanted.add("{}.{}".format(field_name, config.exact_subfield))
                    wanted.add("{}.{}".format(field_name, config.fuzzy_subfield))
        return wanted

    def _seed_clauses(self, source):
        """Build the bool.should clauses that pull candidates for one predecessor.

        Restricted to SEEDABLE ∩ seed_signals so an operator's config choice
        (which signals count as evidence) and the candidate-generation query
        stay consistent: a signal that isn't trusted to seed candidates can't
        silently narrow the search anyway, and one that is configured but not
        seedable (agent) is skipped rather than raising. Dispatched to one
        helper per signal type (rather than one large if/elif chain) purely
        to keep this under ruff's branch-count limit; the query shape below
        is unchanged from a single-method version.
        """
        clauses = []
        for config in self.signal_configs:
            if config.type not in self.seed_signals or config.type not in SEEDABLE:
                continue
            if config.type in ("name-phonetic", "name-token"):
                clauses.extend(_name_clauses(config, source))
            elif config.type == "address":
                clauses.extend(_address_clauses(config, source))
            elif config.type == "exact-identifier":
                clauses.extend(_exact_identifier_clauses(config, source))
        return clauses

    def find(self, pred_hit):
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

        clauses = self._seed_clauses(pred_source)
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

        pred_doc = _to_carrier_doc(pred_hit, tokens_by_id.get(pred_id, {}))
        cand_docs = [_to_carrier_doc(h, tokens_by_id.get(h["_id"], {})) for h in hits]
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


def _name_clauses(config, source):
    """match clauses on the phonetic/token subfield of each configured name field."""
    clauses = []
    for field_name in config.fields:
        text = source.get(field_name)
        if text:
            clauses.append(
                {"match": {"{}.{}".format(field_name, config.subfield): {"query": text}}}
            )
    return clauses


def _address_clauses(config, source):
    """match clauses on the exact (keyword-tokenized) subfield of each address field.

    Seeding on the exact subfield rather than the fuzzy one keeps candidate
    generation precise; the fuzzy/synonym comparison still happens later
    during scoring, once tokens are fetched.
    """
    clauses = []
    for field_name in config.fields:
        text = source.get(field_name)
        if text:
            clauses.append(
                {
                    "match": {
                        "{}.{}".format(field_name, config.exact_subfield): {"query": text}
                    }
                }
            )
    return clauses


def _exact_identifier_clauses(config, source):
    """match/term clauses on shared phone and text identifiers.

    Phones go through the normalized `.clean` subfield (a match, since ES did
    the normalizing at index time); text identifiers like email use `.keyword`
    term equality since they need no normalization pass.
    """
    clauses = []
    for field_name in getattr(config, "phone_fields", []):
        value = source.get(field_name)
        if value:
            clauses.append({"match": {"{}.clean".format(field_name): {"query": value}}})
    for field_name in getattr(config, "text_fields", []):
        value = source.get(field_name)
        if value:
            clauses.append({"term": {"{}.keyword".format(field_name): value}})
    return clauses


def _to_carrier_doc(hit, tokens):
    """Combine a search hit's _source with its fetched tokens into a CarrierDoc.

    Falls back to the ES _id for dot_number when _source lacks it (the probe
    documents in the near-empty dev index do), so this stays usable against a
    sparsely-populated index rather than raising on missing test data.
    """
    source = hit["_source"]
    return CarrierDoc(
        dot_number=str(source.get("dot_number", hit["_id"])),
        source=source,
        tokens=tokens,
    )
