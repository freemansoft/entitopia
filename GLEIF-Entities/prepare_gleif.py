"""Flatten one GLEIF golden-copy download into the entities.csv the sweeps load.

The GLEIF sweeps tag a pair by comparing fields on the two entity documents
they are handed (`DeclaredLinks.classify`), and never query anything else. So
everything a declared-link rule reads has to sit on the entity record itself:

- the registered successor LEIs, cleaned of values no sweep could retrieve;
- the end of each successor chain, so a pair two steps apart in one chain is
  explained rather than reported as an undeclared succession, which needs the
  whole successor map at once and so cannot be done per pair;
- each entity's parent LEIs from the Level 2 file. They are copied here rather
  than through an Elasticsearch enrich policy because a policy serves a frozen
  snapshot: reloading relationships without re-executing it would load
  entities with stale parents and still report success. Joining both files of
  one download here keeps parents and entities from the same publish.

The Level 1 file is read twice, once to collect the successor map and once to
write rows, because its 5 GB of CSV should not be held in memory.

Downloaded zips are untrusted input. They are streamed with zipfile and csv,
never extracted.

Run from the repository root, after fetch_gleif.py:
    .venv/bin/python GLEIF-Entities/prepare_gleif.py
    .venv/bin/python GLEIF-Entities/prepare_gleif.py --print-header
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path

_PROJECT = Path(__file__).parent
DEFAULT_LEI2 = _PROJECT / "data" / "raw" / "lei2.zip"
DEFAULT_RR = _PROJECT / "data" / "raw" / "rr.zip"
DEFAULT_OUT = _PROJECT / "data" / "entities" / "entities.csv"

# The ingest pipeline splits list-valued columns on this, so it must never
# appear inside a value.
SEPARATOR = "|"

# ISO 17442: 18 alphanumerics and two check digits. A value of any other shape
# cannot be a document id in the index, so as a successor it could only ever
# count as a recall miss.
LEI_SHAPE = re.compile(r"^[0-9A-Z]{18}[0-9]{2}$")

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The golden-copy CSV flattens up to five of each repeated element (names,
# successors, events) into numbered columns. Anything past the fifth is not in
# the file at all.
_SLOTS = range(1, 6)

# Headroom over csv's 128 KB default, set by read_zip_csv rather than at
# import so loading this module (as the tests do) changes no global state.
# Every one of the 338 raw columns is parsed, not only those written, and one
# oversized free-text field anywhere would abort the run part-way.
_FIELD_SIZE_LIMIT = 10_000_000

# A care-of first line names the filing agent or manager, not a street.
_CARE_OF = re.compile(r"^(?:C/O|CARE\s+OF)(?![A-Z])", re.IGNORECASE)

# Golden-copy member names start with the publish they belong to, as in
# 20261126-1600-gleif-goldencopy-lei2-golden-copy.csv.
_PUBLISH_STAMP = re.compile(r"^(\d{8}-\d{4})-gleif-goldencopy-")

# Field metadata saying where an output column's value comes from, so callers
# (the ingest pipeline's test, the header check) read it from the dataclass
# rather than from a second list that could drift.
_LIST = {"list_valued": True}
_COMPUTED = {"list_valued": True, "computed": True}
_PARENT = {"list_valued": True, "parent": True}
_INPUT = {"input_only": True}


@dataclass(frozen=True)
class EventColumns:
    """The columns of one flattened legal-entity event slot."""

    event_type: str
    effective_date: str
    status: str


def _event_slot(i):
    """Columns of the i-th event; the CSV numbers each repeated element."""
    prefix = "Entity.LegalEntityEvents.LegalEntityEvent.{}.".format(i)
    return EventColumns(
        prefix + "LegalEntityEventType",
        prefix + "LegalEntityEventEffectiveDate",
        prefix + "event_status",
    )


def _address(kind, part):
    """Legal and headquarters addresses share one sub-column layout."""
    return "Entity.{}.{}".format(kind, part)


@dataclass(frozen=True)
class EntityColumns:
    """Each output column of entities.csv, in order, mapped to its source.

    A `str` value is one Level 1 column copied as is. A tuple is list-valued:
    its sources are joined with `|`, which the ingest pipeline splits again.
    Parent columns hold the Level 2 relationship types they collect, and
    `SUCCESSOR_CHAIN_END` is computed from the successor map, so neither has a
    Level 1 source. Lower-case fields are inputs only, never written.

    Source names were confirmed against the header of the 2026-11-26 publish
    (LEI-CDF 3.1). `read_zip_csv` refuses a header missing any of them, so a
    renamed column fails the prepare step instead of loading as blanks.
    """

    LEI: str = "LEI"
    LEGAL_NAME: str = "Entity.LegalName"
    # Transliterated names are kept with the others: a Latin-script spelling
    # of a non-Latin legal name is how a US look-alike would be found at all.
    OTHER_NAMES: tuple[str, ...] = field(
        default=(
            *("Entity.OtherEntityNames.OtherEntityName.{}".format(i) for i in _SLOTS),
            *(
                "Entity.TransliteratedOtherEntityNames.TransliteratedOtherEntityName.{}".format(i)
                for i in _SLOTS
            ),
        ),
        metadata=_LIST,
    )
    # The first address line, which holds the street on most records. A
    # care-of first line holds the agent's name instead, and the street moves
    # to the first additional line (see care_of_streets).
    LEGAL_STREET: str = _address("LegalAddress", "FirstAddressLine")
    LEGAL_CITY: str = _address("LegalAddress", "City")
    LEGAL_REGION: str = _address("LegalAddress", "Region")
    LEGAL_COUNTRY: str = _address("LegalAddress", "Country")
    LEGAL_POSTAL: str = _address("LegalAddress", "PostalCode")
    HQ_STREET: str = _address("HeadquartersAddress", "FirstAddressLine")
    HQ_CITY: str = _address("HeadquartersAddress", "City")
    HQ_REGION: str = _address("HeadquartersAddress", "Region")
    HQ_COUNTRY: str = _address("HeadquartersAddress", "Country")
    HQ_POSTAL: str = _address("HeadquartersAddress", "PostalCode")
    CATEGORY: str = "Entity.EntityCategory"
    LEGAL_FORM: str = "Entity.LegalForm.EntityLegalFormCode"
    ENTITY_STATUS: str = "Entity.EntityStatus"
    REGISTRATION_STATUS: str = "Registration.RegistrationStatus"
    # The legal creation date, never the LEI registration date: an LEI is
    # often issued years after the entity was formed, which would put old
    # entities in a reappearance window. The spec forbids the substitute.
    CREATION_DATE: str = "Entity.EntityCreationDate"
    # The entity's own end date, preferred over its events (see shutdown_day).
    # Blank on every record of the 2026-11-26 publish, so in practice the
    # events date every shutdown; it is read so a publish that fills it wins.
    SHUTDOWN_DATE: str = "Entity.EntityExpirationDate"
    SUCCESSOR_LEI: tuple[str, ...] = field(
        default=tuple("Entity.SuccessorEntity.{}.SuccessorLEI".format(i) for i in _SLOTS),
        metadata=_LIST,
    )
    SUCCESSOR_CHAIN_END: tuple[str, ...] = field(default=(), metadata=_COMPUTED)
    DIRECT_PARENT_LEI: tuple[str, ...] = field(
        default=("IS_DIRECTLY_CONSOLIDATED_BY",), metadata=_PARENT
    )
    ULTIMATE_PARENT_LEI: tuple[str, ...] = field(
        default=("IS_ULTIMATELY_CONSOLIDATED_BY",), metadata=_PARENT
    )
    # Further relationship types kept as parent columns, decided from the
    # 2026-11-26 profile (README, "Decisions from the profile"). Each lets a shared or
    # parent-child explains rule recognize a fund and its manager, a branch
    # and its head office, or a sub-fund and its umbrella as related. Two funds of
    # one manager are not covered: the configs hold no `shared` rule on these columns.
    BRANCH_OF_LEI: tuple[str, ...] = field(
        default=("IS_INTERNATIONAL_BRANCH_OF",), metadata=_PARENT
    )
    FUND_MANAGER_LEI: tuple[str, ...] = field(default=("IS_FUND-MANAGED_BY",), metadata=_PARENT)
    UMBRELLA_FUND_LEI: tuple[str, ...] = field(default=("IS_SUBFUND_OF",), metadata=_PARENT)
    MASTER_FUND_LEI: tuple[str, ...] = field(default=("IS_FEEDER_TO",), metadata=_PARENT)

    # Where the street is when the first line is a care-of line. On
    # 2026-11-26, 78.5% of US legal and 17.1% of US headquarters first lines
    # started C/O. Left in place, the *_LOCAL fields derived from the street
    # would hold the filing agent's name, and every client of one agent
    # would match every other on address.
    care_of_streets: tuple[tuple[str, str], ...] = field(
        default=(
            ("LEGAL_STREET", _address("LegalAddress", "AdditionalAddressLine.1")),
            ("HQ_STREET", _address("HeadquartersAddress", "AdditionalAddressLine.1")),
        ),
        metadata=_INPUT,
    )
    # A successor named with no LEI is counted, so the README can say how many
    # successions the truth set cannot hold.
    successor_names: tuple[str, ...] = field(
        default=tuple("Entity.SuccessorEntity.{}.SuccessorEntityName".format(i) for i in _SLOTS),
        metadata=_INPUT,
    )
    events: tuple[EventColumns, ...] = field(
        default=tuple(_event_slot(i) for i in _SLOTS), metadata=_INPUT
    )
    # Event types after which the entity no longer exists, chosen from the
    # 2026-11-26 profile of every event type (README, "Shutdown date").
    # ABSORPTION and BREAKUP end the entity by definition and were missing from
    # the expected list. BANKRUPTCY and INSOLVENCY are left out: an entity
    # survives both, and its later DISSOLUTION or LIQUIDATION dates the end.
    terminal_events: frozenset[str] = field(
        default=frozenset(
            {"MERGERS_AND_ACQUISITIONS", "ABSORPTION", "BREAKUP", "DISSOLUTION", "LIQUIDATION"}
        ),
        metadata=_INPUT,
    )
    # Pending and withdrawn events have effective dates too; only a completed
    # one has happened.
    completed_status: str = field(default="COMPLETED", metadata=_INPUT)
    inactive_status: str = field(default="INACTIVE", metadata=_INPUT)

    @classmethod
    def output_columns(cls) -> list[str]:
        """The entities.csv header, in order."""
        return [f.name for f in fields(cls) if not f.metadata.get("input_only")]

    @classmethod
    def list_valued(cls) -> list[str]:
        """Output columns joined with `|`, each of which the pipeline must split."""
        return [f.name for f in fields(cls) if f.metadata.get("list_valued")]

    @classmethod
    def parent_columns(cls) -> list[str]:
        """Output columns filled from Level 2 rather than from the Level 1 row."""
        return [f.name for f in fields(cls) if f.metadata.get("parent")]

    def scalar_columns(self) -> list[tuple[str, str]]:
        """(output, source) for every column copied as is, dates excluded."""
        dates = {"CREATION_DATE", "SHUTDOWN_DATE"}
        return [
            (f.name, getattr(self, f.name))
            for f in fields(self)
            if not f.metadata and f.name not in dates
        ]

    def relationship_types(self) -> dict[str, str]:
        """Level 2 relationship type -> the parent column that collects it."""
        return {kind: name for name in self.parent_columns() for kind in getattr(self, name)}

    def required(self) -> list[str]:
        """Every Level 1 column this map reads, for the header check."""
        columns = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.metadata.get("parent") or f.metadata.get("computed"):
                continue
            if f.name == "events":
                for slot in value:
                    columns += [slot.event_type, slot.effective_date, slot.status]
            elif f.name == "care_of_streets":
                columns += [source for _, source in value]
            elif isinstance(value, str) and not f.metadata.get("input_only"):
                columns.append(value)
            elif isinstance(value, tuple):
                columns += value
        return columns


@dataclass(frozen=True)
class RelationshipColumns:
    """The Level 2 columns the parent pivot reads.

    The start node is the child: GLEIF writes "A IS_DIRECTLY_CONSOLIDATED_BY
    B" with A as the start node.
    """

    child: str = "Relationship.StartNode.NodeID"
    parent: str = "Relationship.EndNode.NodeID"
    relationship_type: str = "Relationship.RelationshipType"
    status: str = "Relationship.RelationshipStatus"
    parent_columns: dict[str, str] = field(
        default_factory=lambda: EntityColumns().relationship_types()
    )

    def required(self) -> list[str]:
        """The Level 2 columns the header check demands."""
        return [self.child, self.parent, self.relationship_type, self.status]


class PrepStats(Counter):
    """Records dropped or repaired at prepare time, written to the README.

    Each counter names a value the CSV does not carry as GLEIF published it,
    so the totals stay checkable against the raw file. All start at zero, so
    a counter that never fired is reported as 0 rather than missing.
    """

    KEYS = (
        "self_successor",
        "successor_without_lei",
        "successor_bad_format",
        "separator_replaced",
        "undated_creation",
        "no_shutdown_date",
        "care_of_street_replaced",
        "care_of_no_street",
    )

    def __init__(self):
        super().__init__(dict.fromkeys(self.KEYS, 0))


def iso_day(raw: str | None) -> str | None:
    """The calendar day a GLEIF timestamp names, or None when it names none.

    Truncated rather than converted to UTC: 1996-01-01T00:00:00+01:00 in UTC
    is 1995-12-31, and one day is enough to move a same-day successor between
    the reappearance and absorption sweeps.
    """
    if not raw:
        return None
    day = raw[:10]
    if not _ISO_DAY.match(day):
        return None
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        return None
    return day


def _clean(value, stats):
    """Strip a value and keep the list separator out of it, counting each repair."""
    value = (value or "").strip()
    if SEPARATOR in value:
        stats["separator_replaced"] += 1
        value = value.replace(SEPARATOR, " ")
    return value


def _join(values):
    """Join non-blank values once each, in first-seen order, for a list column."""
    return SEPARATOR.join(dict.fromkeys(v for v in values if v))


def clean_successors(row: dict, columns: EntityColumns, stats: Counter) -> list[str]:
    """The successor LEIs on a row that a sweep could ever retrieve.

    A self-successor is dropped because the candidate search excludes the
    predecessor's own id, so it would be a recall miss no tuning could fix. A
    malformed or missing LEI cannot be a document id. Each is counted.
    """
    own = row[columns.LEI].strip()
    found = []
    for lei_column, name_column in zip(columns.SUCCESSOR_LEI, columns.successor_names, strict=True):
        lei = row[lei_column].strip()
        if not lei:
            if row[name_column].strip():
                stats["successor_without_lei"] += 1
            continue
        if not LEI_SHAPE.match(lei):
            stats["successor_bad_format"] += 1
        elif lei == own:
            stats["self_successor"] += 1
        elif lei not in found:
            found.append(lei)
    return found


def collect_successors(rows: Iterable[dict], columns: EntityColumns) -> dict[str, list[str]]:
    """The cleaned successor map, the first of the two Level 1 passes.

    Built from the cleaned values so a dropped link cannot extend a chain. Its
    counts are discarded; the writing pass counts the same drops once.
    """
    successors = {}
    for row in rows:
        found = clean_successors(row, columns, Counter())
        if found:
            successors[row[columns.LEI].strip()] = found
    return successors


def _strongly_connected(graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's components, iteratively, emitted sinks first.

    Iterative because a successor chain's length is set by the data, and a
    recursive walk would hit Python's recursion limit on a long one.
    """
    index, low, on_stack, stack, components = {}, {}, set(), [], []
    counter = 0
    for root, targets in graph.items():
        if root in index:
            continue
        work = [(root, iter(targets))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph[child])))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def successor_chain_ends(
    successors: dict[str, list[str]],
) -> tuple[dict[str, list[str]], Counter]:
    """Map every LEI in a successor chain to the sorted LEIs its chain ends at.

    This is GLEIF's version of FDIC's ULTCERT. In a chain A->B->C, GLEIF links
    A to B and B to C but never A to C, so without a shared chain end a
    matched A-C pair would be reported as an undeclared succession although
    the registrar recorded it. The end entity carries its own LEI, as an
    active FDIC bank carries its own CERT, so A-C shares a value as well as
    A-B. An entity in no chain gets nothing, and a shared rule never fires on
    a blank.

    A successor absent from the file is still an end: its LEI is known. A
    cycle, which only bad data produces, would otherwise loop forever, so
    each cycle is collapsed into one node. A cycle with an exit (A->B, B->A,
    B->C) resolves to the ends that exit reaches, like any other link. Only a
    closed cycle, which leads nowhere, takes its smallest LEI as the end of
    all its members, so the result is reproducible and the pairs stay
    explained. Strongly connected components find cycles of any length.

    chain_length_<n> counts members whose farthest end is n links away.
    """
    graph = defaultdict(list)
    for lei, targets in successors.items():
        graph[lei].extend(targets)
        for target in targets:
            graph.setdefault(target, [])
    stats = Counter()
    stats["split_successor"] = sum(1 for targets in successors.values() if len(targets) > 1)

    components = _strongly_connected(graph)

    # Tarjan emits components sinks first, so every exit is resolved before
    # the component that leads to it.
    component_of, ends_of, depth_of = {}, [], []
    for number, component in enumerate(components):
        for member in component:
            component_of[member] = number
        exits = {
            component_of[t] for m in component for t in graph[m] if component_of.get(t) != number
        }
        if len(component) > 1:
            stats["successor_cycle"] += 1
        if exits:
            ends_of.append(sorted({end for e in exits for end in ends_of[e]}))
            depth_of.append(1 + max(depth_of[e] for e in exits))
        else:
            ends_of.append([min(component)])
            depth_of.append(0)

    ends = {}
    for lei, number in component_of.items():
        ends[lei] = ends_of[number]
        stats["chain_length_{}".format(depth_of[number])] += 1
    stats["chain_members"] = len(ends)
    stats.setdefault("successor_cycle", 0)
    return ends, stats


def pivot_relationships(
    rows: Iterable[dict], columns: RelationshipColumns
) -> tuple[dict[str, dict[str, list[str]]], Counter]:
    """Map each child LEI to {parent column: sorted parent LEIs}.

    Every relationship status is kept. An inactive entity's relationships have
    usually lapsed with it, and a lapsed parent still explains a pair formed
    while the parent held it. The counter records every row by (type, status),
    kept or not, so the README totals can be checked against the raw file.

    A configured type that matches no row is refused. A renamed type in a new
    RR-CDF release would otherwise leave its parent column blank on every
    entity, and the explains rules reading it would go quiet while the load
    reported success.
    """
    collected = defaultdict(lambda: defaultdict(set))
    counts = Counter()
    for row in rows:
        kind = row[columns.relationship_type]
        counts[(kind, row[columns.status])] += 1
        column = columns.parent_columns.get(kind)
        if column:
            collected[row[columns.child].strip()][column].add(row[columns.parent].strip())
    seen = {kind for kind, _ in counts}
    unmatched = sorted(kind for kind in columns.parent_columns if kind not in seen)
    if unmatched:
        raise RuntimeError(
            "configured relationship types match no Level 2 row: {}".format(unmatched)
        )
    parents = {
        child: {column: sorted(leis) for column, leis in by_column.items()}
        for child, by_column in collected.items()
    }
    return parents, counts


def shutdown_day(row: dict, columns: EntityColumns) -> str | None:
    """The day an entity ended, or None when the record does not say.

    Its own expiration date wins. Without one, the latest completed terminal
    event stands in, because GLEIF records a merger or dissolution as an event
    and leaves the expiration date blank. A pending or withdrawn event has not
    happened, so it never dates a shutdown.

    Events date only an inactive entity. The surviving side of a merger
    carries the same completed MERGERS_AND_ACQUISITIONS event (3,574 such
    events sat on active records on 2026-11-26), and a shutdown date on a
    live acquirer would read as its end.
    """
    own = iso_day(row[columns.SHUTDOWN_DATE])
    if own:
        return own
    if row[columns.ENTITY_STATUS].strip() != columns.inactive_status:
        return None
    days = [
        iso_day(row[slot.effective_date])
        for slot in columns.events
        if row[slot.event_type] in columns.terminal_events
        and row[slot.status] == columns.completed_status
    ]
    days = [day for day in days if day]
    return max(days) if days else None


def _move_past_care_of(out, row, columns, stats):
    """Replace a care-of street with the next address line, or blank it.

    Blank rather than kept when there is no next line: an agent's name in a
    street field matches every other client of that agent.
    """
    for name, additional in columns.care_of_streets:
        if not _CARE_OF.match(out[name]):
            continue
        street = _clean(row[additional], stats)
        out[name] = street
        stats["care_of_street_replaced" if street else "care_of_no_street"] += 1


def prepare_entities(
    rows: Iterable[dict],
    parents: dict[str, dict[str, list[str]]],
    columns: EntityColumns,
    chain_ends: dict[str, list[str]],
    stats: Counter | None = None,
) -> Iterator[dict]:
    """Yield one entities.csv row per Level 1 row, the second of the two passes.

    `chain_ends` has no default: a caller that forgot it would write every
    row without SUCCESSOR_CHAIN_END, and the chain rule would report each
    two-step pair as a finding.

    `no_shutdown_date` counts inactive records only: an active entity has no
    shutdown to date, and counting it would bury the figure the stop gate
    reads. A run in which no inactive record is dated is refused when the
    rows run out, whether the inactive records went undated (a terminal-event
    vocabulary that no longer matches the file) or none were seen at all (an
    inactive_status that no longer matches, which would leave every record
    looking active). Neither is a register with no shutdowns.
    """
    stats = PrepStats() if stats is None else stats
    parent_columns = columns.parent_columns()
    inactive = dated = 0
    for row in rows:
        out = {}
        lei = row[columns.LEI].strip()
        for name, source in columns.scalar_columns():
            out[name] = _clean(row[source], stats)
        out["OTHER_NAMES"] = _join(_clean(row[source], stats) for source in columns.OTHER_NAMES)
        created = iso_day(row[columns.CREATION_DATE])
        if not created:
            stats["undated_creation"] += 1
        _move_past_care_of(out, row, columns, stats)
        ended = shutdown_day(row, columns)
        if out["ENTITY_STATUS"] == columns.inactive_status:
            inactive += 1
            if ended:
                dated += 1
            else:
                stats["no_shutdown_date"] += 1
        out["CREATION_DATE"] = created or ""
        out["SHUTDOWN_DATE"] = ended or ""
        out["SUCCESSOR_LEI"] = _join(clean_successors(row, columns, stats))
        out["SUCCESSOR_CHAIN_END"] = _join(chain_ends.get(lei, ()))
        own_parents = parents.get(lei, {})
        for name in parent_columns:
            out[name] = _join(own_parents.get(name, ()))
        yield {name: out[name] for name in columns.output_columns()}
    if not dated:
        raise RuntimeError(
            "no inactive record got a SHUTDOWN_DATE ({} records had ENTITY_STATUS {!r}); "
            "check inactive_status, terminal_events and completed_status against the "
            "file".format(inactive, columns.inactive_status)
        )


def _open_single_csv(path):
    """Open the zip's one CSV member as text, shared by the reader and --print-header."""
    archive = zipfile.ZipFile(path)
    members = archive.infolist()
    if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
        archive.close()
        raise RuntimeError(
            "{} must hold exactly one .csv member, found {}".format(
                path, [m.filename for m in members]
            )
        )
    handle = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8", newline="")
    return archive, handle


def read_zip_csv(path: Path, required: Iterable[str]) -> Iterator[dict]:
    """Stream the rows of the one CSV inside a golden-copy zip.

    Both checks run before the first row is returned. A zip holding something
    else is not the file the column map was written for. A header missing a
    configured column would read as blank on every row and load as millions
    of entities with no successor or no date, which is FDIC hazard 3 again.
    `required` has no default for that reason: an empty list checks nothing.
    """
    csv.field_size_limit(_FIELD_SIZE_LIMIT)
    archive, handle = _open_single_csv(path)
    reader = csv.DictReader(handle)
    header = reader.fieldnames or []
    absent = [name for name in required if name not in header]
    if absent:
        handle.close()
        archive.close()
        raise RuntimeError("{} header lacks configured columns {}".format(path, absent))

    def rows():
        try:
            yield from reader
        finally:
            handle.close()
            archive.close()

    return rows()


def _publish_stamp(path):
    """The publish a golden-copy zip belongs to, read from its member's name."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    match = _PUBLISH_STAMP.match(names[0]) if len(names) == 1 else None
    if not match:
        raise RuntimeError("{} has no publish stamp in its member names {}".format(path, names))
    return match.group(1)


def require_same_publish(lei2: Path, rr: Path) -> str:
    """Refuse a Level 1 and Level 2 file from different publishes.

    fetch_gleif.py removes a file whose download failed, but a run killed
    between the two downloads leaves a new lei2.zip beside the previous
    rr.zip. Joined, that pair would attach stale parents to every entity and
    report success.
    """
    stamps = _publish_stamp(lei2), _publish_stamp(rr)
    if stamps[0] != stamps[1]:
        raise RuntimeError(
            "{} ({}) and {} ({}) come from different publishes; re-run fetch_gleif.py".format(
                lei2, stamps[0], rr, stamps[1]
            )
        )
    return stamps[0]


def _print_header(path):
    """Numbered header listing, for re-checking the column map after a CDF change."""
    archive, handle = _open_single_csv(path)
    with archive, handle:
        print("{}:".format(path))
        for number, name in enumerate(next(csv.reader(handle))):
            print("  {:>3} {}".format(number, name))


def main():
    """Entry point: pivot Level 2, collect successors, then write entities.csv."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lei2", type=Path, default=DEFAULT_LEI2)
    parser.add_argument("--rr", type=Path, default=DEFAULT_RR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--print-header", action="store_true")
    args = parser.parse_args()
    if args.print_header:
        _print_header(args.lei2)
        _print_header(args.rr)
        return 0

    publish = require_same_publish(args.lei2, args.rr)
    entity_columns = EntityColumns()
    relationship_columns = RelationshipColumns()
    required = entity_columns.required()
    parents, relationship_counts = pivot_relationships(
        read_zip_csv(args.rr, relationship_columns.required()), relationship_columns
    )
    successors = collect_successors(read_zip_csv(args.lei2, required), entity_columns)
    chain_ends, chain_stats = successor_chain_ends(successors)

    # Written beside the destination and moved into place only when complete,
    # so an interrupted run never leaves a partial entities.csv to be loaded.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    part = args.out.with_name(args.out.name + ".part")
    stats = PrepStats()
    written = 0
    try:
        with open(part, "w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=entity_columns.output_columns())
            writer.writeheader()
            for row in prepare_entities(
                read_zip_csv(args.lei2, required),
                parents,
                entity_columns,
                chain_ends=chain_ends,
                stats=stats,
            ):
                writer.writerow(row)
                written += 1
        os.replace(part, args.out)
    finally:
        part.unlink(missing_ok=True)

    report = {
        "publish": publish,
        "rows": written,
        "prep": dict(stats),
        "chains": dict(sorted(chain_stats.items())),
        "relationships": {
            "{} {}".format(kind, status): count
            for (kind, status), count in sorted(relationship_counts.items())
        },
        "children_with_parents": len(parents),
    }
    stats_path = args.out.with_name("prepare-stats.json")
    stats_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("Wrote {:,} rows to {}".format(written, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
