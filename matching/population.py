"""Population selection: which records the sweep treats as its starting set.

The population decides what everything downstream scores, so an error here does
not fail — it quietly changes what the whole sweep is about. That asymmetry is
why every unrecognized input in this module raises: a clause kind nobody
implemented, a selector name nobody defined, and a reference cycle all abort
rather than contributing nothing and leaving a plausible-looking run behind.

Two modes. `lifecycle` selects records carrying a shutdown-shaped event, which
is what succession detection needs. `all-entities` sweeps everything, which is
the only honest option for a project whose data carries no lifecycle events at
all — there a pair asserts resemblance, not succession.

Selector definitions live in project configuration rather than here. They used
to be four hardcoded FMCSA queries, which meant using `entity-match` on another
dataset required editing framework code rather than writing configuration. The
clause vocabulary stays closed and code-backed — `nested-exists`, `term`,
`all`, `any`, and nothing else — so this is a fixed menu rather than an
Elasticsearch query DSL smuggled into JSON.
"""

import logging

logger = logging.getLogger(__name__)

MODES = frozenset({"lifecycle", "all-entities"})

CLAUSE_KINDS = ("nested-exists", "term", "all", "any")

PAGE_SIZE = 500


class PopulationSelector:
    """Builds the population query and walks the matching records.

    Which records count as the starting population is a policy call the caller
    makes through config, not something this module should decide — hence a
    menu of named selectors rather than one query. It was named
    PredecessorSelector, which presumed succession; a duplicate-detection
    project has a population but no predecessors.
    """

    def __init__(self, es, source_index, config):
        self.es = es
        self.source_index = source_index
        self.mode = getattr(config, "mode", "lifecycle")
        if self.mode not in MODES:
            raise ValueError(
                "unknown population mode {!r}; known modes are {}".format(
                    self.mode, ", ".join(sorted(MODES))
                )
            )
        self.selector = getattr(config, "selector", None)
        selectors = getattr(config, "selectors", None)
        # Config arrives as SimpleNamespace, so the selector table is an
        # attribute bag rather than a dict; vars() gives the mapping without
        # forcing every caller to know how config was loaded.
        self.selectors = vars(selectors) if selectors is not None else {}
        self.max_records = getattr(config, "max_records", None)
        # Paging under a point-in-time needs a stable total order. Which field
        # provides it is per-project — this was a dot_number literal. A sort on
        # a field the index lacks fails the search outright rather than
        # silently reordering, which is the behaviour worth having.
        self.sort_field = getattr(config, "sort_field", None)

    def build_query(self):
        """Compose the selected population's query, or None to match everything.

        None rather than {"match_all": {}} so a caller can distinguish "sweep
        everything" from "a filter that happened to match everything" — the
        same result, very different intents, and only one of them is a bug
        when it turns up unexpectedly.
        """
        if self.mode == "all-entities":
            return None
        if self.selector is None:
            raise ValueError(
                "population.selector is required in lifecycle mode; set it to one "
                "of the names defined in population.selectors, or use "
                "mode 'all-entities' to sweep every record"
            )
        return self._resolve(self.selector, seen=())

    def _resolve(self, name, seen):
        """Build one named selector, following `all` and `any` references.

        `seen` is a tuple rather than a set so a cycle can be reported as the
        path that closed it. Without the check a cycle recurses until the
        interpreter's stack limit and reports nothing a reader could act on.
        """
        if name in seen:
            raise ValueError("selector cycle: {}".format(" -> ".join([*seen, name])))
        if name not in self.selectors:
            raise ValueError(
                "unknown selector {!r}; defined selectors are {}".format(
                    name, ", ".join(sorted(self.selectors)) or "(none)"
                )
            )
        definition = self.selectors[name]
        kinds = list(vars(definition))
        if len(kinds) != 1:
            raise ValueError(
                "selector {!r} must declare exactly one clause kind, found {}".format(
                    name, ", ".join(kinds) or "(none)"
                )
            )
        kind = kinds[0]
        body = getattr(definition, kind)
        if kind == "nested-exists":
            return self._nested_exists(body)
        if kind == "term":
            field, value = next(iter(vars(body).items()))
            return {"bool": {"must": [{"term": {field: value}}]}}
        if kind == "all":
            return {"bool": {"must": [self._resolve(n, (*seen, name)) for n in body]}}
        if kind == "any":
            return {
                "bool": {
                    "should": [self._resolve(n, (*seen, name)) for n in body],
                    "minimum_should_match": 1,
                }
            }
        raise ValueError(
            "unknown clause kind {!r} in selector {!r}; known kinds are {}".format(
                kind, name, ", ".join(CLAUSE_KINDS)
            )
        )

    def _nested_exists(self, body):
        """Records having a single array element that matches every filter.

        Nested rather than a plain bool over dotted paths because an object
        mapping matches each filter against the flattened union of all a
        record's elements: a record with an ACTIVE 2015 order and an INACTIVE
        2022 order satisfied status=ACTIVE and date >= 2020 from two different
        orders and was swept even though no single order qualified. That also
        let the temporal signal report a date from an element the selector
        never intended to match, so the emitted pair's gap described the wrong
        event.

        This is one clause kind rather than three composable ones for exactly
        that reason: if `exists`, `terms` and `range` could be combined freely,
        a project could rebuild the broken flattened shape out of config that
        looks entirely reasonable.

        Only `require` is mandatory. `terms` and `range` are operator knobs for
        tightening a sweep rather than fields every deployment sets.
        """
        path = body.path
        must = [{"exists": {"field": "{}.{}".format(path, body.require)}}]
        terms = getattr(body, "terms", None)
        if terms is not None:
            for field, values in vars(terms).items():
                must.append({"terms": {"{}.{}".format(path, field): values}})
        ranges = getattr(body, "range", None)
        if ranges is not None:
            for field, bounds in vars(ranges).items():
                must.append({"range": {"{}.{}".format(path, field): vars(bounds)}})
        return {"nested": {"path": path, "query": {"bool": {"must": must}}}}

    def iterate(self):
        """Yield population hits using a point-in-time and search_after.

        A PIT gives a consistent snapshot across a sweep that may run for
        hours; from/size would break past 10,000 results.
        """
        pit = self.es.open_point_in_time(index=self.source_index, keep_alive="10m")
        pit_id = pit["id"]
        search_after = None
        yielded = 0
        query = self.build_query()

        try:
            while True:
                if self.max_records is not None:
                    remaining = self.max_records - yielded
                    if remaining <= 0:
                        return
                    page_size = min(PAGE_SIZE, remaining)
                else:
                    page_size = PAGE_SIZE

                params = {
                    "size": page_size,
                    "pit": {"id": pit_id, "keep_alive": "10m"},
                    "sort": [{self.sort_field: "asc"}],
                    "track_total_hits": False,
                }
                # Omitted rather than sent as match_all so an all-entities
                # sweep and a filtered one differ in the request itself, which
                # is what a slow-query log or a proxy trace will show.
                if query is not None:
                    params["query"] = query
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
                    if self.max_records is not None and yielded >= self.max_records:
                        return

                search_after = hits[-1]["sort"]
                pit_id = response.get("pit_id", pit_id)
        finally:
            try:
                self.es.close_point_in_time(id=pit_id)
            except Exception as e:
                logger.warning("Failed to close point in time: {}".format(e))
