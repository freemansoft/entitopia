"""Data shapes that signals consume.

CarrierDoc pairs a carrier's _source with the analyzed tokens Elasticsearch
produced for it. Tokens come from _mtermvectors rather than being recomputed in
Python, so scoring always sees exactly what the index sees.
"""

import math
from dataclasses import dataclass, field


@dataclass
class CarrierDoc:
    dot_number: str
    source: dict
    # Keyed "field.subfield", e.g. "legal_name.phonetic_bm"
    tokens: dict[str, set[str]] = field(default_factory=dict)

    def token_set(self, field_name: str, subfield: str) -> set[str]:
        return self.tokens.get("{}.{}".format(field_name, subfield), set())

    def value(self, path: str):
        """Read a dotted path out of _source.

        Enriched fields arrive as lists (max_matches > 1), so walking a path
        through a list collects the value from every element. Collected values
        are flattened at each step because enrichment nests two levels deep:
        a carrier's inspections[] each carry their own units[], so
        "inspections.units.insp_unit_vehicle_id_number" would otherwise produce
        a list of lists and find no dicts at the final step.

        Returns None when any part of the path is missing.
        """
        current = self.source
        for part in path.split("."):
            if isinstance(current, list):
                collected = []
                for item in _flatten(current):
                    if isinstance(item, dict) and part in item:
                        collected.append(item[part])
                if not collected:
                    return None
                current = _flatten(collected)
            elif isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            else:
                return None
        return current


def _flatten(values):
    """One level of list flattening, leaving non-list elements alone."""
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


@dataclass
class ScoringContext:
    """Corpus-level statistics gathered once per sweep."""

    agent_counts: dict[str, int] = field(default_factory=dict)
    total_agent_carriers: int = 0

    def agent_rarity(self, agent_name: str) -> float:
        """1.0 for an agent nobody uses, near 0.0 for a dominant filer.

        BOC-3 process agents are a commercial filing industry: only 89 distinct
        agents cover 1.43M filings, and the largest covers 9.4%. Without this
        weighting a shared agent fires on roughly 7% of random pairs.

        Uses normalized inverse document frequency, log(N/count)/log(N), NOT
        1 - count/N. With only 89 agents the largest share is 9.4%, so
        1 - share would compress every agent into [0.906, 1.0] and the signal
        would carry no discriminating power at all. Normalized IDF spreads the
        same population across [0.167, 1.0].
        """
        if self.total_agent_carriers <= 0:
            return 0.0
        count = self.agent_counts.get(agent_name, 0)
        if count <= 0:
            return 1.0
        return math.log(self.total_agent_carriers / count) / math.log(
            self.total_agent_carriers
        )
