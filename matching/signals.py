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

from matching.documents import CarrierDoc, ScoringContext
from matching.tokens import (
    blended_overlap,
    containment,
    normalize_phone,
    normalize_text_identifier,
)

logger = logging.getLogger(__name__)


class Signal:
    type_names: tuple[str, ...] = ()

    def __init__(self, config):
        self.config = config
        self.signal_type = config.type
        self.weight = float(config.weight)

    def score(self, pred: CarrierDoc, cand: CarrierDoc, ctx: ScoringContext) -> float | None:
        raise NotImplementedError


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


class ExactIdentifierSignal(Signal):
    """Shared phone, fax, or email. Binary.

    Reads raw _source rather than analyzed tokens, so placeholder rejection
    happens here rather than relying on the analyzer.
    """

    type_names = ("exact-identifier",)

    def score(self, pred, cand, ctx):
        pred_values = set()
        cand_values = set()

        for field_name in getattr(self.config, "phone_fields", []):
            _collect(pred_values, pred.value(field_name), normalize_phone)
            _collect(cand_values, cand.value(field_name), normalize_phone)

        for field_name in getattr(self.config, "text_fields", []):
            _collect(pred_values, pred.value(field_name), normalize_text_identifier)
            _collect(cand_values, cand.value(field_name), normalize_text_identifier)

        if not pred_values or not cand_values:
            return None
        return 1.0 if pred_values & cand_values else 0.0


def _collect(target: set, raw, normalize) -> None:
    """Normalize raw (scalar or list) into target, dropping None results."""
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


def parse_flexible_date(value) -> datetime.date | None:
    """Parse ISO (2022-07-09) or Oracle (01-JUN-74) dates. None on failure."""
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
    if len(parts) == 3 and parts[1] in _MONTHS:
        try:
            day = int(parts[0])
            year = int(parts[2])
        except ValueError:
            return None
        if len(parts[2]) == 2:
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
    """Closeness between the predecessor's shutdown and the successor's registration."""

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


class VinOverlapSignal(Signal):
    """Any shared VIN. Binary — VINs are globally unique, so one is damning."""

    type_names = ("vin-overlap",)

    def score(self, pred, cand, ctx):
        pred_vins = set()
        cand_vins = set()
        for path in self.config.fields:
            _collect(pred_vins, pred.value(path), normalize_text_identifier)
            _collect(cand_vins, cand.value(path), normalize_text_identifier)

        if not pred_vins or not cand_vins:
            return None
        return 1.0 if pred_vins & cand_vins else 0.0


def _latest_date(raw) -> datetime.date | None:
    """Most recent parseable date from a scalar or list."""
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    dates = [d for d in (parse_flexible_date(item) for item in items) if d is not None]
    return max(dates) if dates else None


SIGNAL_TYPES: dict[str, type[Signal]] = {}


def _register(signal_class: type[Signal]) -> None:
    for name in signal_class.type_names:
        SIGNAL_TYPES[name] = signal_class


_register(NameOverlapSignal)
_register(AddressSignal)
_register(ExactIdentifierSignal)
_register(AgentSignal)
_register(TemporalSignal)
_register(VinOverlapSignal)


def build_signal(config) -> Signal:
    signal_class = SIGNAL_TYPES.get(config.type)
    if signal_class is None:
        raise ValueError(
            "unknown signal type {!r}; known types are {}".format(
                config.type, ", ".join(sorted(SIGNAL_TYPES))
            )
        )
    return signal_class(config)
