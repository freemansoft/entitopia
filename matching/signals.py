"""Signal implementations.

Each signal scores one kind of evidence that two carriers are the same
operation. Every score() returns a float in [0.0, 1.0] or None.

None means "not evaluable" — data missing on one or both sides. It is not the
same as 0.0, which means "evaluated, no similarity". Returning 0.0 for missing
data would penalize carriers for absent records rather than judging them
neutrally.
"""

import datetime
import logging

from matching.documents import CarrierDoc, ScoringContext, read_path
from matching.tokens import (
    blended_overlap,
    containment,
    normalize_phone,
    normalize_text_identifier,
)

logger = logging.getLogger(__name__)


# Config keys whose values name the source fields a signal reads. Used to
# decide which signals are looking at the same underlying evidence.
_FIELD_CONFIG_KEYS = (
    "fields",
    "phone_fields",
    "text_fields",
    "name_field",
    "address_field",
    "predecessor_date",
    "successor_date",
)


class Signal:
    """Base contract every scoring signal implements.

    Subclasses turn one kind of evidence (name overlap, shared phone, timing
    between shutdown and re-registration, ...) into a score in [0.0, 1.0], or
    None when that evidence can't be evaluated for this pair. scorer.py relies
    on None being distinct from 0.0 to drop the signal and renormalize the
    remaining weights, rather than treating missing data as "evaluated and
    dissimilar" and penalizing a carrier for a gap in its records.
    """

    type_names: tuple[str, ...] = ()

    def __init__(self, config):
        """Bind the signal to its config entry, including its blend weight."""
        self.config = config
        self.signal_type = config.type
        self.weight = float(config.weight)
        # A conclusive signal carries a match on its own: when it fires, the
        # pair is reported even if the blended total lands under
        # min_total_score. Reserved for evidence that is decisive rather than
        # merely strong -- a shared, non-placeholder VIN means two carriers
        # operated the same physical vehicle, which a weighted average of
        # eight signals will always dilute below a floor tuned for name and
        # address similarity. Defaults False, so this is opt-in per config
        # entry rather than a property of any particular signal class.
        self.conclusive = bool(getattr(config, "conclusive", False))

    @property
    def evidence_key(self) -> frozenset[str]:
        """The set of source fields this signal reads.

        Two signals sharing a key are two readings of the same evidence, not
        two independent corroborations of it. `PairScorer` counts distinct
        keys rather than signal instances when applying `min_signals`.

        This matters because config deliberately lists name signals more than
        once over the same two fields — the two phonetic encoders are separate
        arms so they can be weighted apart, and `name-token` reads the cleaned
        form of the same text. Counting instances let a pair matching on
        nothing but a name satisfy a floor written to require corroboration
        from somewhere else.

        Deliberately ignores `subfield`: different encodings of one field are
        the same evidence, which is the entire reason the arms exist.
        """
        names: list[str] = []
        for key in _FIELD_CONFIG_KEYS:
            value = getattr(self.config, key, None)
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, list):
                names.extend(value)
        # A signal naming no source fields still counts as its own evidence
        # rather than collapsing together with every other such signal.
        return frozenset(names) or frozenset({self.signal_type})

    def score(self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext) -> float | None:
        """Score one carrier pair. Subclasses implement; see the class
        docstring for the None-vs-0.0 contract every implementation must honor.
        """
        raise NotImplementedError

    def seed_clauses(self, source: dict, ctx=None) -> list[dict]:
        """Elasticsearch bool.should clauses that retrieve candidates for this predecessor.

        Returning [] means "this signal cannot retrieve, only corroborate" —
        it will still score a pair that some other signal pulled in, but it
        will never widen the candidate set. That is the right answer for a
        signal with no discriminating power to retrieve on: AgentSignal
        deliberately declines, because 87 BOC-3 agents cover 519,139 filings
        and seeding on one returns essentially random carriers.

        This lives on the signal rather than in CandidateFinder because a
        signal is the only thing that knows what evidence it reads. The
        retrieval engine previously carried a hard-coded whitelist of which
        signal types could seed plus a per-type if/elif to build their
        clauses, which meant adding a signal meant editing retrieval code
        that has no business knowing about phone numbers or vehicle
        identifiers. Now retrieval just asks.
        """
        return []

    def token_subfields(self) -> set[str]:
        """"field.subfield" pairs whose analyzed tokens this signal needs fetched.

        Empty for signals that read raw _source instead of analyzed tokens.
        Same rationale as seed_clauses: the signal knows what it reads, so
        CandidateFinder can batch exactly those fields into one
        _mtermvectors call without enumerating signal types itself.
        """
        return set()

    def exact_evidence_fields(self) -> list[tuple[str, str]]:
        """(source path, aggregatable field) for values this signal treats as identity.

        A signal that scores a shared value 1.0 is asserting that the value
        identifies one thing in the world. That assertion is routinely false in
        real data, and the corpus is the only thing that can say so — hence a
        frequency scan, which needs to know both where to read the value and
        which Elasticsearch field to aggregate. The two differ: a VIN is mapped
        `keyword` and aggregates on itself, while `telephone` is `text` and
        aggregates on `telephone.keyword`.

        Empty for signals that score similarity rather than equality. A
        graded name overlap has no single value to suppress — a common token
        is handled by weighting, not exclusion.
        """
        return []


class NameOverlapSignal(Signal):
    """Token-set overlap over name fields.

    Registered for both name-phonetic and name-token: the math is identical and
    only the subfield differs. Listing the same type twice in config with
    different subfields is how the double-metaphone and Beider-Morse arms get
    weighted independently.
    """

    type_names = ("name-phonetic", "name-token")

    def score(self, pred, cand, ctx):
        fields = list(self.config.fields)
        subfield = self.config.subfield
        cross_field = getattr(self.config, "cross_field", False)

        if cross_field:
            pairings = [(p, c) for p in fields for c in fields]
        else:
            pairings = [(f, f) for f in fields]

        best = None
        for pred_field, cand_field in pairings:
            score = blended_overlap(
                pred.token_set(pred_field, subfield),
                cand.token_set(cand_field, subfield),
            )
            if score is not None and (best is None or score > best):
                best = score
        return best

    def seed_clauses(self, source, ctx=None):
        """match on the phonetic/token subfield of each configured name field."""
        clauses = []
        for field_name in self.config.fields:
            text = read_path(source, field_name)
            if text:
                clauses.append(
                    {
                        "match": {
                            "{}.{}".format(field_name, self.config.subfield): {"query": text}
                        }
                    }
                )
        return clauses

    def token_subfields(self):
        return {"{}.{}".format(f, self.config.subfield) for f in self.config.fields}


CROSS_STATE_FUZZY_PENALTY = 0.5


class AddressSignal(Signal):
    """Street similarity, exact first then fuzzy.

    The exact subfield uses a keyword tokenizer, so its "token set" is a single
    normalized string. The fuzzy subfield is standard-tokenized with street
    suffix synonyms applied.
    """

    type_names = ("address",)

    def score(self, pred, cand, ctx):
        fields = list(self.config.fields)
        exact_subfield = self.config.exact_subfield
        fuzzy_subfield = self.config.fuzzy_subfield
        fuzzy_scale = float(self.config.fuzzy_scale)

        pred_state = pred.value("phy_state")
        cand_state = cand.value("phy_state")
        same_state = bool(pred_state) and pred_state == cand_state

        best = None
        saw_any_data = False

        for pred_field in fields:
            for cand_field in fields:
                pred_exact = pred.token_set(pred_field, exact_subfield)
                cand_exact = cand.token_set(cand_field, exact_subfield)
                pred_fuzzy = pred.token_set(pred_field, fuzzy_subfield)
                cand_fuzzy = cand.token_set(cand_field, fuzzy_subfield)

                if not (pred_exact or pred_fuzzy) or not (cand_exact or cand_fuzzy):
                    continue
                saw_any_data = True

                if pred_exact and pred_exact == cand_exact:
                    score = 1.0
                else:
                    score = containment(pred_fuzzy, cand_fuzzy) * fuzzy_scale
                    if not same_state:
                        # "100 MAIN ST" exists in every state. An exact match
                        # across states stays strong; a fuzzy one does not.
                        score *= CROSS_STATE_FUZZY_PENALTY

                if best is None or score > best:
                    best = score

        return best if saw_any_data else None

    def seed_clauses(self, source, ctx=None):
        """match on the exact (keyword-tokenized) subfield of each address field.

        Seeding on the exact subfield rather than the fuzzy one keeps candidate
        generation precise; the fuzzy/synonym comparison still happens later
        during scoring, once tokens are fetched.
        """
        clauses = []
        for field_name in self.config.fields:
            text = read_path(source, field_name)
            if text:
                clauses.append(
                    {
                        "match": {
                            "{}.{}".format(field_name, self.config.exact_subfield): {
                                "query": text
                            }
                        }
                    }
                )
        return clauses

    def token_subfields(self):
        wanted = set()
        for field_name in self.config.fields:
            wanted.add("{}.{}".format(field_name, self.config.exact_subfield))
            wanted.add("{}.{}".format(field_name, self.config.fuzzy_subfield))
        return wanted


class ExactIdentifierSignal(Signal):
    """Shared phone, fax, or email. Binary.

    Reads raw _source rather than analyzed tokens, so placeholder rejection
    happens here rather than relying on the analyzer.

    Contact details are shared far more often than they look. Besides outright
    placeholders like `(000) 000-0000`, whole populations of unrelated carriers
    legitimately share one value: a permit-filing service, an insurance agency,
    or a corporate parent puts its own address on every carrier it files for.
    Those values are perfectly correct data and completely non-identifying, so
    they are excluded via ScoringContext rather than repaired in the source.
    Scoring one 1.0 is worse here than in most signals: this is an *identity*
    signal, so it also satisfies require_identity_signal and can carry a pair
    on its own.
    """

    type_names = ("exact-identifier",)

    def _phone_fields(self):
        return list(getattr(self.config, "phone_fields", []))

    def _text_fields(self):
        return list(getattr(self.config, "text_fields", []))

    def score(self, pred, cand, ctx):
        pred_values = self._values(pred.value, ctx)
        cand_values = self._values(cand.value, ctx)

        if not pred_values or not cand_values:
            return None
        return 1.0 if pred_values & cand_values else 0.0

    def _values(self, reader, ctx) -> set[str]:
        """Normalized, non-ignored identifiers from every configured field.

        Tests the ignore list against both the raw value and its normalized
        form. The frequency scan reports what Elasticsearch indexed —
        `(000) 000-0000` — while `normalize_phone` reduces that to digits, so
        checking only one form would let the other slip through depending on
        whether a value was discovered by the scan or typed into config.
        """
        values: set[str] = set()
        for field_name in self._phone_fields():
            _collect_unignored(values, reader(field_name), normalize_phone, field_name, ctx)
        for field_name in self._text_fields():
            _collect_unignored(
                values, reader(field_name), normalize_text_identifier, field_name, ctx
            )
        return values

    def seed_clauses(self, source, ctx=None):
        """match/term clauses on shared phone and text identifiers.

        Phones go through the normalized `.clean` subfield (a match, since ES
        did the normalizing at index time); text identifiers like email use
        `.keyword` term equality since they need no normalization pass.

        Ignored values are skipped, otherwise a carrier carrying a filing
        service's email seeds on it and retrieves every other carrier that
        service ever filed for — hundreds of unrelated candidates that then
        crowd out real ones under max_candidates.
        """
        clauses = []
        for field_name in self._phone_fields():
            value = read_path(source, field_name)
            if value and not _is_ignored(ctx, field_name, value, normalize_phone):
                clauses.append({"match": {"{}.clean".format(field_name): {"query": value}}})
        for field_name in self._text_fields():
            value = read_path(source, field_name)
            if value and not _is_ignored(ctx, field_name, value, normalize_text_identifier):
                clauses.append({"term": {"{}.keyword".format(field_name): value}})
        return clauses

    def exact_evidence_fields(self):
        """Aggregate on `.keyword`: these fields are `text`, unlike a keyword VIN."""
        return [
            (f, "{}.keyword".format(f)) for f in self._phone_fields() + self._text_fields()
        ]


def _is_ignored(ctx, field_name: str, value, normalize) -> bool:
    """Whether a value is excluded on this field, testing raw and normalized forms.

    Both forms matter because the ignore list has two sources that disagree on
    shape: the corpus frequency scan contributes what Elasticsearch indexed
    (`(000) 000-0000`), while an operator may reasonably write either that or
    the normalized `0000000000` into config.
    """
    if ctx is None:
        return False
    if ctx.is_ignored(field_name, str(value)):
        return True
    normalized = normalize(value)
    return normalized is not None and ctx.is_ignored(field_name, normalized)


def _collect_unignored(target: set, raw, normalize, field_name: str, ctx) -> None:
    """_collect, minus any value excluded for this field."""
    if raw is None:
        return
    for item in raw if isinstance(raw, list) else [raw]:
        if _is_ignored(ctx, field_name, item, normalize):
            continue
        normalized = normalize(item)
        if normalized is not None:
            target.add(normalized)


def _collect(target: set, raw, normalize) -> None:
    """Accumulate normalized values from a scalar-or-list field into a set.

    A carrier can carry more than one value for a field (multiple phones,
    agent names across amendments); a set lets a signal check for any shared
    value without caring how many there are. Values that fail normalization
    (blanks, placeholders) are dropped silently rather than raising, so one
    bad value on a record doesn't block comparison of the rest.
    """
    if raw is None:
        return
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        normalized = normalize(item)
        if normalized is not None:
            target.add(normalized)


# Two-digit years above this pivot are 19xx. FMCSA carrier registrations go
# back to the 1970s, so "01-JUN-74" is 1974. Java's yy pattern would render it
# as 2074, which is why add_date needs explicit handling rather than a naive
# dd-MMM-yy date mapping.
CENTURY_PIVOT = 30

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

BACKWARD_WINDOW_DAYS = 180
BACKWARD_SCALE = 0.5

# A dd-MMM-yy Oracle export splits on "-" into exactly 3 parts; anything else
# isn't this format.
ORACLE_DATE_PART_COUNT = 3
# A 2-digit part means the year needs CENTURY_PIVOT to resolve its century, as
# with "01-JUN-74" -> 1974.
TWO_DIGIT_YEAR_LENGTH = 2


def parse_flexible_date(value) -> datetime.date | None:  # noqa: PLR0911
    """Parse ISO (2022-07-09) or Oracle (01-JUN-74) dates. None on failure.

    FMCSA data mixes both: newer records come through as ISO, but older
    out-of-service and registration dates are exports from a legacy Oracle
    system using the two-digit-year dd-MMM-yy format. See CENTURY_PIVOT below
    for why that two-digit year can't be resolved naively.

    The early returns below are deliberate bail-outs for each way the input
    can fail to parse; flattening them into fewer returns would nest the
    format checks instead of listing them, making the function harder to
    follow, not easier.
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None

    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        pass

    parts = text.split("-")
    if len(parts) == ORACLE_DATE_PART_COUNT and parts[1] in _MONTHS:
        try:
            day = int(parts[0])
            year = int(parts[2])
        except ValueError:
            return None
        if len(parts[2]) == TWO_DIGIT_YEAR_LENGTH:
            year += 1900 if year > CENTURY_PIVOT else 2000
        try:
            return datetime.date(year, _MONTHS[parts[1]], day)
        except ValueError:
            return None
    return None


class AgentSignal(Signal):
    """Shared BOC-3 process agent, weighted by how rare the agent is.

    Only 89 distinct agents cover 1.43M filings, so an unweighted version of
    this signal fires on roughly 7% of random pairs. Weight is deliberately low.
    """

    type_names = ("agent",)

    def score(self, pred, cand, ctx):
        pred_agents = set()
        cand_agents = set()
        _collect(pred_agents, pred.value(self.config.name_field), normalize_text_identifier)
        _collect(cand_agents, cand.value(self.config.name_field), normalize_text_identifier)

        if not pred_agents or not cand_agents:
            return None

        shared = pred_agents & cand_agents
        if not shared:
            return 0.0
        return max(ctx.agent_rarity(name) for name in shared)


class TemporalSignal(Signal):
    """Closeness between the predecessor's shutdown and the successor's registration.

    A chameleon carrier typically re-registers under a new DOT number soon
    after being ordered out of service, to resume operating with minimal
    downtime. A short gap is therefore corroborating evidence of
    reincarnation; a gap of years is more likely coincidence.
    """

    type_names = ("temporal",)

    def score(self, pred, cand, ctx):
        shutdown = _latest_date(pred.value(self.config.predecessor_date))
        registered = _latest_date(cand.value(self.config.successor_date))
        if shutdown is None or registered is None:
            return None

        gap_days = (registered - shutdown).days
        max_gap = float(self.config.max_gap_days)

        if gap_days >= 0:
            return max(0.0, 1.0 - (gap_days / max_gap))

        # Registered before the shutdown: a pre-positioned shell is a real
        # tactic, but weaker evidence than reopening days afterward.
        backward = min(1.0, abs(gap_days) / float(BACKWARD_WINDOW_DAYS))
        return max(0.0, (1.0 - backward) * BACKWARD_SCALE)


# A carrier's enriched history can carry thousands of vehicle identifiers, and
# every one becomes a term in the seed query. Capping keeps a large fleet from
# building a terms clause big enough to slow the whole sweep; the values are
# sorted first so the same carrier always contributes the same subset rather
# than whichever ones enrichment happened to order first that run.
MAX_SEED_TOKENS = 512


class SharedTokenSignal(Signal):
    """Any shared globally-unique token. Binary — one match is damning.

    Registered as both "vin-overlap" and "shared-token" because the logic is
    not about vehicles: it is "these two records name the same physical thing,
    and that name is unique worldwide". A VIN is one instance; a container
    number, aircraft tail number, serial number or NPI behaves identically.
    Only the `fields` config is domain-specific, which is where domain
    knowledge belongs. The "vin-overlap" name is retained so existing
    DOT-Commercial configuration keeps working.

    Unlike the name and address signals, this one is worth seeding on: a token
    that is unique worldwide has no false-positive rate to speak of, so a
    terms clause on it retrieves the right carrier or nothing at all.
    """

    type_names = ("vin-overlap", "shared-token")

    def score(self, pred, cand, ctx):
        pred_tokens = self._tokens(pred.value, ctx)
        cand_tokens = self._tokens(cand.value, ctx)

        if not pred_tokens or not cand_tokens:
            return None
        return 1.0 if pred_tokens & cand_tokens else 0.0

    def _tokens(self, reader, ctx) -> set[str]:
        """Normalized, non-suppressed tokens from every configured field.

        Takes the reader rather than the document so scoring (CarrierDoc.value)
        and seeding (read_path over a raw hit) collect tokens identically —
        seeding on values that scoring would normalize differently would
        retrieve candidates that then score 0.0.

        Dropping suppressed values here rather than scoring them 0.0 is what
        makes a carrier whose only VIN is "UNKNOWN" come back None (no usable
        evidence) instead of "evaluated, no match" — the same
        None-versus-0.0 distinction the module docstring describes, applied to
        a value that cannot support the signal's premise.
        """
        tokens: set[str] = set()
        for path in self.config.fields:
            found: set[str] = set()
            _collect(found, reader(path), normalize_text_identifier)
            if ctx is not None:
                found = {t for t in found if not ctx.is_ignored(path, t)}
            tokens |= found
        return tokens

    def seed_clauses(self, source, ctx=None):
        """terms clauses that retrieve any carrier sharing one of these tokens.

        This is the only signal that can retrieve a successor which shares
        nothing else. A carrier that re-registers under a new name, at a new
        address, with a new phone, but keeps driving the same trucks is
        invisible to name/address/phone seeding at any max_candidates value —
        the seed query never returns it, so no amount of scoring can find it.
        That is the most deliberate chameleon profile there is, which is why
        this signal seeds despite the others' domain fields staying inert.

        Seeds on RAW values, not the normalized ones score() compares. The
        fields are mapped `keyword`, so Elasticsearch stores each value
        verbatim — FMCSA writes VINs uppercase, while normalize_text_identifier
        casefolds. Querying a keyword field with the normalized form matches
        nothing at all, which would make this signal appear to seed while
        contributing zero candidates: a silent recall failure indistinguishable
        from "no carrier shares a VIN". Normalizing is still correct inside
        score(), where both sides get the same treatment.

        Uses the raw field rather than a subfield for the same reason: for a
        keyword mapping the indexed term IS the literal value.
        """
        values = self._raw_values(source, ctx)
        if not values:
            return []
        return [
            {"terms": {field_name: values}} for field_name in self.config.fields
        ]

    def exact_evidence_fields(self):
        """These are mapped `keyword`, so the field aggregates on itself."""
        return [(f, f) for f in self.config.fields]

    def _raw_values(self, source, ctx=None) -> list[str]:
        """Deduplicated, sorted, capped raw token values for the seed query.

        Sorted so a given carrier always contributes the same subset when the
        cap bites, rather than whichever values enrichment happened to order
        first on that run — an unstable subset would make the sweep's recall
        vary between identical runs.
        """
        values: set[str] = set()
        for path in self.config.fields:
            raw = read_path(source, path)
            if raw is None:
                continue
            for item in raw if isinstance(raw, list) else [raw]:
                text = str(item).strip()
                if not text:
                    continue
                # Seeding on a placeholder retrieves every carrier that also
                # recorded it -- 158 of them for the literal VIN "GGGG".
                if ctx is not None and ctx.is_ignored(path, text):
                    continue
                values.add(text)
        return sorted(values)[:MAX_SEED_TOKENS]


def _latest_date(raw) -> datetime.date | None:
    """Most recent parseable date from a scalar or list.

    A carrier can accumulate many out-of-service orders over its history; only
    the most recent shutdown is relevant to whether a successor registered
    shortly after, so earlier ones are discarded rather than averaged or
    taken first.
    """
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    dates = [d for d in (parse_flexible_date(item) for item in items) if d is not None]
    return max(dates) if dates else None


SIGNAL_TYPES: dict[str, type[Signal]] = {}


def _register(signal_class: type[Signal]) -> None:
    """Map a signal class's declared type_names to the class itself.

    Config selects signals by type string (see build_signal), so signal
    classes need to be discoverable by name rather than requiring the
    config-loading code to import and know about every signal class directly.
    """
    for name in signal_class.type_names:
        SIGNAL_TYPES[name] = signal_class


_register(NameOverlapSignal)
_register(AddressSignal)
_register(ExactIdentifierSignal)
_register(AgentSignal)
_register(TemporalSignal)
_register(SharedTokenSignal)


def build_signal(config) -> Signal:
    """Construct the Signal a config entry names, by its `type` string.

    Keeps config decoupled from Python import paths: adding a signal means
    registering it via _register, not changing how scorer.py builds its
    signal list from config.
    """
    signal_class = SIGNAL_TYPES.get(config.type)
    if signal_class is None:
        raise ValueError(
            "unknown signal type {!r}; known types are {}".format(
                config.type, ", ".join(sorted(SIGNAL_TYPES))
            )
        )
    return signal_class(config)
