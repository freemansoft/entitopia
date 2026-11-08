"""Compare phonetic encoders on one name field, without reindexing anything.

Exists because an encoder choice was made once for DOT-Commercial's carrier
names (double_metaphone, max_code_len 6, commit 47f1b31) and never for
CMS-Providers, whose hospital names kept the 2023 default `metaphone` with no
record of why. Choosing between encoders should not need a reload per
candidate, so this builds each candidate's analyzer chain inline in
`_analyze`: the index's own named filters up to the phonetic step, then the
encoder under test.

The measurement itself is compare_name_matchers.py's: real names edited the
ways an evader or a clerk would, scored against the look-alikes a candidate
search returns, reported as AUC and recall at a 1% false-match rate. Its
limits apply here too, and one more: a gap of 0.01 AUC is within what a
different seed moves, so repeat with two or three seeds before acting on one.

Measured 2026-11-08 on hospitals-2026.11.08-000001, 600 names, seeds
20261108, 7 and 99 (all-edits AUC):

    metaphone (any max_code_len)     0.855  0.857  0.848
    double_metaphone, max_code_len 4 0.866  0.864  0.861
    double_metaphone, max_code_len 6 0.860  0.859  0.853
    beider_morse english+spanish     0.800  0.796  0.790

`max_code_len` changes nothing for plain `metaphone`: the plugin applies it
to double_metaphone only, so setting it on metaphone is silently inert.

Prints aggregates only. Never print or write a sampled name.
"""

import argparse
import importlib.util
import random
import sys
from collections import defaultdict
from pathlib import Path

# Runs as `.venv/bin/python scripts/compare_phonetic_encoders.py`, which puts
# scripts/ on sys.path rather than the repo root; matching.tokens needs it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch

from matching.tokens import blended_overlap

ROOT = Path(__file__).resolve().parent.parent

# Loaded by path because scripts/ is a directory of tools, not a package; the
# edits, look-alike pairs and arithmetic must be the same ones, not copies.
_spec = importlib.util.spec_from_file_location(
    "compare_name_matchers", ROOT / "scripts" / "compare_name_matchers.py"
)
cnm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cnm)

# The candidates. Beider-Morse is pinned to english+spanish as DOT-Commercial
# pins it, because language guessing on short tokens is unstable between runs.
ENCODERS = {
    "metaphone": {"type": "phonetic", "encoder": "metaphone"},
    "double_metaphone, max_code_len 4": {"type": "phonetic", "encoder": "double_metaphone"},
    "double_metaphone, max_code_len 6": {
        "type": "phonetic",
        "encoder": "double_metaphone",
        "max_code_len": 6,
    },
    "beider_morse english+spanish": {
        "type": "phonetic",
        "encoder": "beider_morse",
        "rule_type": "approx",
        "name_type": "generic",
        "languageset": ["english", "spanish"],
    },
}
# Scored too, so each encoder can be read against "no encoder at all".
NO_ENCODER = "no encoder (cleaned tokens)"


class InlineChains:
    """Token sets from the index's own filters plus an encoder given inline, cached."""

    def __init__(self, es, index, base_filters):
        self.es, self.index, self.base = es, index, base_filters
        self._cache = {}

    def tokens(self, chain, text):
        key = (chain, text)
        if key not in self._cache:
            filters = self.base + ([ENCODERS[chain]] if chain in ENCODERS else [])
            response = self.es.indices.analyze(
                index=self.index, tokenizer="standard", filter=filters, text=text
            )
            self._cache[key] = {t["token"] for t in response["tokens"]}
        return self._cache[key]


class _CleanedView:
    """What compare_name_matchers' sampling expects: `.tokens(subfield, text)`."""

    def __init__(self, chains):
        self.chains = chains

    def tokens(self, subfield, text):
        return list(self.chains.tokens(NO_ENCODER, text))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--index", default="hospitals-000001", help="alias or index")
    parser.add_argument("--field", default="Facility Name")
    parser.add_argument(
        "--base-filters",
        default="icu_normalizer,icu_folding,punct_white,hospital_generic_stop",
        help="the field's phonetic analyzer filters before the encoder, by name",
    )
    parser.add_argument("--names", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20261108)
    args = parser.parse_args()

    es = Elasticsearch(args.host, request_timeout=60)
    concrete = next(iter(es.indices.get(index=args.index)))
    chains = InlineChains(es, concrete, args.base_filters.split(","))
    view = _CleanedView(chains)
    rng = random.Random(args.seed)

    names = cnm.sample_names(es, concrete, args.field, view, args.names, args.seed)
    positives = defaultdict(list)
    for name in names:
        for label, edit in cnm.EDITS.items():
            edited = edit(name, rng)
            if edited and edited != name:
                positives[label].append((name, edited))
    negatives = cnm.lookalike_pairs(es, concrete, args.field, view, names)

    print(f"index {concrete}  field {args.field}  seed {args.seed}")
    print(f"names {len(names)}  look-alike pairs {len(negatives)}")

    for chain in [*ENCODERS, NO_ENCODER]:

        def score(a, b, chain=chain):
            s = blended_overlap(chains.tokens(chain, a), chains.tokens(chain, b))
            return 0.0 if s is None else s

        neg = [score(a, b) for a, b in negatives]
        print(f"\n{chain}")
        every = []
        for label, pairs in positives.items():
            pos = [score(a, b) for a, b in pairs]
            every += pos
            recall, _ = cnm.recall_at_false_match_rate(pos, neg)
            print(f"  {label:22s} AUC {cnm.auc(pos, neg):.3f}  recall@1% {recall:6.1%}")
        recall, _ = cnm.recall_at_false_match_rate(every, neg)
        print(f"  {'all edits':22s} AUC {cnm.auc(every, neg):.3f}  recall@1% {recall:6.1%}")


if __name__ == "__main__":
    main()
