"""Registered successions, so a sweep can be measured against what is known.

A sweep scores resemblance; it cannot say whether a pair is a real succession.
Some datasets record the answer themselves (a closed bank's named successor, a
legal entity's parent). Tagging each pair `truth`, `explains` or `none` lets a
measurement script count recall against the first and keep the second out of
the findings, without the matcher ever seeing the answer while it scores.

A declared path that never fires makes every pair read as undeclared and recall
read as zero, which looks like a bad matcher rather than bad configuration.
That is why the rules are validated at load and their fields are checked
against the index mapping by utils/config_liveness.py.
"""

import hashlib
from dataclasses import dataclass

from matching.documents import EntityDoc, ScoringContext

ROLES = ("truth", "explains")
KINDS = ("key", "shared")


@dataclass(frozen=True)
class DeclaredRule:
    """One way the data itself says two records are linked."""

    name: str
    role: str
    kind: str
    field: str
    # Only a key rule has a direction (GLEIF records name their parent, so the
    # reference sits on the candidate rather than the predecessor).
    reverse: bool = False


def _values(doc: EntityDoc, field: str, ctx: ScoringContext) -> set[str]:
    """Usable values at a path as strings, with blanks and ignored values dropped.

    Ignored values are dropped here, once, so a placeholder such as a "0"
    successor id cannot link every closed record to whatever is keyed "0".
    """
    raw = doc.value(field)
    if raw is None:
        return set()
    items = raw if isinstance(raw, list) else [raw]
    found = set()
    for item in items:
        text = str(item).strip()
        if not text or ctx.is_ignored(field, text):
            continue
        found.add(text)
    return found


def _fires(rule: DeclaredRule, pred: EntityDoc, cand: EntityDoc, ctx) -> bool:
    """Whether one rule links this predecessor to this candidate."""
    if rule.kind == "shared":
        return bool(_values(pred, rule.field, ctx) & _values(cand, rule.field, ctx))
    if rule.reverse:
        return str(pred.entity_key) in _values(cand, rule.field, ctx)
    return str(cand.entity_key) in _values(pred, rule.field, ctx)


class DeclaredLinks:
    """The configured rules, classifying pairs and naming known-correct targets.

    Built once per sweep and held on the phase, because the pair writer sits
    several frames below the config.
    """

    def __init__(self, rules, complete_truth=False):
        self.rules = list(rules)
        # Whether the truth rules cover every real succession. Recall is only
        # a meaningful number when they do; otherwise unlinked pairs may be
        # real successions the data simply does not record.
        self.complete_truth = complete_truth

    @classmethod
    def from_config(cls, config):
        """Build from the entity-match config, or None when none is declared."""
        block = getattr(config, "declared_links", None)
        if block is None:
            return None
        rules = []
        for raw in block.rules:
            reverse = bool(getattr(raw, "reverse", False))
            if raw.role not in ROLES or raw.kind not in KINDS:
                raise ValueError(
                    "declared_links rule {!r}: role must be one of {} and kind "
                    "one of {}".format(raw.name, ROLES, KINDS)
                )
            if reverse and raw.kind != "key":
                # A flag with no effect is the failure this repo keeps shipping:
                # the rule would silently run undirected.
                raise ValueError(
                    "declared_links rule {!r}: reverse only applies to a key "
                    "rule, not a {} rule".format(raw.name, raw.kind)
                )
            rules.append(DeclaredRule(raw.name, raw.role, raw.kind, raw.field, reverse))
        return cls(rules, bool(getattr(block, "complete_truth", False)))

    def classify(self, pred: EntityDoc, cand: EntityDoc, ctx):
        """(declared, rule name) for a pair; truth outranks explains.

        Truth wins so a pair that is both a recorded successor and the end of a
        chain counts toward recall instead of being filtered out as explained.
        Rule order breaks ties within a role so the tag is reproducible.
        """
        for role in ROLES:
            for rule in self.rules:
                if rule.role == role and _fires(rule, pred, cand, ctx):
                    return role, rule.name
        return "none", None

    def truth_targets(self, pred: EntityDoc, ctx) -> set[str]:
        """Keys the predecessor itself names as its successor.

        Forward key rules only: a reverse or shared rule cannot name a target
        from the predecessor alone, so it cannot say what recall missed.
        """
        targets = set()
        for rule in self.rules:
            if rule.role == "truth" and rule.kind == "key" and not rule.reverse:
                targets |= _values(pred, rule.field, ctx)
        return targets


def split_half(entity_key: str) -> str:
    """Assign a truth link to the tuning half or the reporting half.

    Hash-based so the assignment is the same on every run and every machine,
    and independent of load order. Thresholds tuned on the "tune" half and
    reported on the "report" half keep the quoted recall from being fitted to
    the pairs it is quoted on.
    """
    parity = hashlib.sha256(str(entity_key).encode()).digest()[0] % 2
    return "tune" if parity == 0 else "report"
