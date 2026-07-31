"""Predecessor selection: which carriers count as 'shut down'.

Population sizes measured against the July 2026 FMCSA extracts:

    out-of-service        340,352
    revoked-authority   1,008,619
    both                  182,774
    either              1,166,197

revoked-authority covers roughly half of every carrier ever registered.
Involuntary revocation for lapsed insurance is routine and is not by itself
evidence of a chameleon, so out-of-service is the default.
"""

import logging

logger = logging.getLogger(__name__)

SELECTORS = frozenset({"out-of-service", "revoked-authority", "both", "either"})

# auth_history.original_action_desc == 'INVOLUNTARY REVOCATION' occurs 2,215,957
# times, but 2,208,586 dispositions are 'DISCONTINUED REVOCATION' — the
# revocation was reversed. Selecting on the filing would gather millions of
# carriers that were never shut down, so selectors key on the disposition.
REVOKED_DISPOSITION = "REVOKED"

PAGE_SIZE = 500


class PredecessorSelector:
    """Builds the "shut down" query and walks the matching carriers.

    Chameleon detection needs a defensible definition of "predecessor" before
    it can look for successors reusing its identity. Which definition is
    right is a policy call the caller makes via config.selector, not
    something this module should hard-code — hence four selectors rather than
    one query.
    """

    def __init__(self, es, source_index, config):
        self.es = es
        self.source_index = source_index
        self.selector = getattr(config, "selector", "out-of-service")
        if self.selector not in SELECTORS:
            raise ValueError(
                "unknown selector {!r}; known selectors are {}".format(
                    self.selector, ", ".join(sorted(SELECTORS))
                )
            )
        self.oos_status = list(getattr(config, "oos_status", []) or [])
        self.oos_date_from = getattr(config, "oos_date_from", None)
        self.max_predecessors = getattr(config, "max_predecessors", None)

    def _out_of_service_clause(self):
        """Carriers with an out-of-service order, optionally narrowed further.

        Only oos_date is required; status and date-from are operator knobs
        for tightening the sweep (e.g. only ACTIVE orders, only recent ones)
        rather than fields every deployment needs to set.
        """
        must = [{"exists": {"field": "out_of_service_orders.oos_date"}}]
        if self.oos_status:
            must.append({"terms": {"out_of_service_orders.status": self.oos_status}})
        if self.oos_date_from:
            # oos_date is mapped as keyword, but ISO dates sort lexicographically
            # so a range query still behaves correctly.
            must.append(
                {"range": {"out_of_service_orders.oos_date": {"gte": self.oos_date_from}}}
            )
        return {"bool": {"must": must}}

    def _revoked_clause(self):
        """Carriers whose authority was actually revoked, not merely filed against.

        See the module docstring: keying on disp_action_desc rather than
        original_action_desc excludes the ~2.2M involuntary-revocation filings
        that were reversed and never took effect.
        """
        return {
            "bool": {
                "must": [
                    {"term": {"auth_history.disp_action_desc": REVOKED_DISPOSITION}}
                ]
            }
        }

    def build_query(self):
        """Compose the selected population's query.

        'both' intersects and 'either' unions the same two clauses used
        individually, so the four selectors stay consistent with each other
        by construction rather than by four independently maintained queries.
        """
        if self.selector == "out-of-service":
            return self._out_of_service_clause()
        if self.selector == "revoked-authority":
            return self._revoked_clause()
        if self.selector == "both":
            return {
                "bool": {"must": [self._out_of_service_clause(), self._revoked_clause()]}
            }
        return {
            "bool": {
                "should": [self._out_of_service_clause(), self._revoked_clause()],
                "minimum_should_match": 1,
            }
        }

    def iterate(self):
        """Yield predecessor hits using a point-in-time and search_after.

        A PIT gives a consistent snapshot across a sweep that may run for hours.
        from/size would break past 10,000 results.
        """
        pit = self.es.open_point_in_time(index=self.source_index, keep_alive="10m")
        pit_id = pit["id"]
        search_after = None
        yielded = 0

        try:
            while True:
                if self.max_predecessors is not None:
                    remaining = self.max_predecessors - yielded
                    if remaining <= 0:
                        return
                    page_size = min(PAGE_SIZE, remaining)
                else:
                    page_size = PAGE_SIZE

                params = {
                    "size": page_size,
                    "query": self.build_query(),
                    "pit": {"id": pit_id, "keep_alive": "10m"},
                    "sort": [{"dot_number": "asc"}],
                    "track_total_hits": False,
                }
                if search_after is not None:
                    params["search_after"] = search_after

                # No index= when a pit is supplied; the pit carries the target.
                response = self.es.search(**params)
                hits = response["hits"]["hits"]
                if not hits:
                    return

                for hit in hits:
                    yield hit
                    yielded += 1
                    if self.max_predecessors is not None and yielded >= self.max_predecessors:
                        return

                search_after = hits[-1]["sort"]
                pit_id = response.get("pit_id", pit_id)
        finally:
            try:
                self.es.close_point_in_time(id=pit_id)
            except Exception as e:
                logger.warning("Failed to close point in time: {}".format(e))
