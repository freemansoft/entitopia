"""Compare six ways of fuzzy-matching names against the look-alikes a search returns.

Exists to answer "which fuzzy matcher suits a name field?" with a measurement
rather than an opinion, for blog Article 1 and for anyone choosing a matcher
for a new dataset. The sweep uses two phonetic encoders and exact tokens; edit
distance and n-grams were never tried, so a claim that phonetic matching is the
right choice had nothing behind it.

How it measures. Real names are sampled from the index, and each is edited the
ways an evader or a data-entry clerk would edit it (a typo, a respelling, an
abbreviation, a dropped or reordered word, a changed suffix). Each edited name
paired with its original is a positive. Every matcher scores every pair, and
the result is reported as AUC (the chance a positive outscores an unrelated
pair) and as recall at a 1% false-match rate.

The unrelated pairs are the decision that matters. By default they are the
look-alikes a candidate search actually returns for each name: the top `match`
hits, minus the name itself and anything with the same cleaned tokens. Random
pairs of names (`--negatives random`) share almost nothing, so every matcher
separates them almost perfectly; measured on 2026-11-08, all six scored AUC
0.97 or better against random pairs and 0.72 to 0.93 against look-alikes. A
comparison run on random pairs flatters every matcher equally and decides
nothing.

Limits to state with any result: the edits are synthetic, applied to the
first word of each name because that is usually the distinctive one; some
look-alikes may be genuinely related businesses; only names are compared, so
nothing here measures what the sweep's other signals add; and one seeded
sample is one sample.

Prints aggregates only. Never print or write a sampled name: the index holds
real businesses, and this repo does not publish them (CLAUDE.md).
"""

import argparse
import bisect
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

# Runs as `.venv/bin/python scripts/compare_name_matchers.py`, which puts
# scripts/ on sys.path rather than the repo root; matching.tokens needs it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import Elasticsearch

from matching.tokens import blended_overlap

# One false match in a hundred is a working rate for a reviewable queue; the
# recall a matcher keeps at that rate is what a reader can act on, where AUC
# alone hides a matcher that only separates pairs far from the threshold.
FALSE_MATCH_RATE = 0.01

# How many of each name's search hits become unrelated pairs. Five keeps the
# look-alike set close to what a candidate cutoff would actually score without
# letting one common name dominate it.
LOOKALIKES_PER_NAME = 5

# Respellings an evader or a clerk might make without changing how a name
# sounds. Applied first match only, so each positive carries one edit.
RESPELLINGS = [
    (r"PH", "F"),
    (r"CK", "K"),
    (r"C(?=[AOU])", "K"),
    (r"Y", "I"),
    (r"Z", "S"),
    (r"([B-DF-HJ-NP-TV-Z])\1", r"\1"),
    (r"EE", "I"),
    (r"OO", "U"),
    (r"TH", "T"),
]
SUFFIXES = ["LLC", "INC", "CORP", "CO", "LTD"]

# Elasticsearch's `fuzziness: AUTO` boundaries: no edits up to 2 characters,
# one up to 5, two beyond. Copied so the fuzzy-token matcher credits exactly
# what a fuzzy `match` query would.
AUTO_NO_EDITS_MAX_LEN = 2
AUTO_ONE_EDIT_MAX_LEN = 5

# An interior typo or transposition needs a letter either side of the edit,
# and a transposition needs two interior letters, so shorter words are skipped.
MIN_EDITABLE_WORD = 4
# Abbreviation keeps the first four letters, and only of a word long enough
# that four letters is a real shortening (TRANSWORLD -> TRAN, not SMITH -> SMIT).
ABBREVIATE_FROM_LEN = 7
ABBREVIATED_LEN = 4
# Dropping a word must leave at least two; reordering needs two to swap.
MIN_WORDS_TO_DROP = 3
MIN_WORDS_TO_REORDER = 2
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def levenshtein(a: str, b: str) -> int:
    """Edit distance, the measure Elasticsearch's `fuzziness` bounds."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def edit_similarity(a: str, b: str) -> float | None:
    """Whole-name edit distance scaled to [0, 1]; None when either name is empty."""
    if not a or not b:
        return None
    return 1 - levenshtein(a, b) / max(len(a), len(b))


def trigrams(s: str) -> set[str]:
    """Character trigrams with word-boundary padding, as an ngram tokenizer would emit."""
    padded = f"  {s} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def trigram_similarity(a: str, b: str) -> float | None:
    """Jaccard over character trigrams; None when either name is empty."""
    if not a or not b:
        return None
    ga, gb = trigrams(a), trigrams(b)
    return len(ga & gb) / len(ga | gb)


def auto_fuzziness(token: str) -> int:
    """Elasticsearch's `fuzziness: AUTO` edit budget for a token of this length."""
    if len(token) <= AUTO_NO_EDITS_MAX_LEN:
        return 0
    return 1 if len(token) <= AUTO_ONE_EDIT_MAX_LEN else 2


def fuzzy_token_overlap(a: list[str], b: list[str]) -> float | None:
    """The sweep's blended overlap, with tokens equal when within AUTO edits.

    What a `match` query with `fuzziness: AUTO` would credit, scored the way
    the sweep scores token sets, so the comparison with exact tokens changes
    one thing only. Each token in b is used once, so repeating a word cannot
    inflate the overlap.
    """
    if not a or not b:
        return None
    used, matched = set(), 0
    for x in a:
        for j, y in enumerate(b):
            if j not in used and levenshtein(x, y) <= auto_fuzziness(x):
                used.add(j)
                matched += 1
                break
    jaccard = matched / (len(a) + len(b) - matched)
    containment = matched / min(len(a), len(b))
    return 0.5 * jaccard + 0.5 * containment


def _edit_first_word(name: str, rng: random.Random, edit) -> str | None:
    words = name.split()
    if len(words[0]) < MIN_EDITABLE_WORD:
        return None
    words[0] = edit(words[0], rng)
    return " ".join(words)


def typo(name: str, rng: random.Random) -> str | None:
    def edit(w, r):
        k = r.randrange(1, len(w) - 1)
        return w[:k] + r.choice(LETTERS.replace(w[k], "")) + w[k + 1 :]

    return _edit_first_word(name, rng, edit)


def transposition(name: str, rng: random.Random) -> str | None:
    def edit(w, r):
        k = r.randrange(1, len(w) - 2)
        return w[:k] + w[k + 1] + w[k] + w[k + 2 :]

    return _edit_first_word(name, rng, edit)


def respelling(name: str, rng: random.Random) -> str | None:
    for pattern, replacement in RESPELLINGS:
        edited = re.sub(pattern, replacement, name, count=1)
        if edited != name:
            return edited
    return None


def abbreviation(name: str, rng: random.Random) -> str | None:
    words = name.split()
    if len(words[0]) < ABBREVIATE_FROM_LEN:
        return None
    words[0] = words[0][:ABBREVIATED_LEN]
    return " ".join(words)


def word_dropped(name: str, rng: random.Random) -> str | None:
    words = name.split()
    if len(words) < MIN_WORDS_TO_DROP:
        return None
    del words[rng.randrange(1, len(words))]
    return " ".join(words)


def words_reordered(name: str, rng: random.Random) -> str | None:
    words = name.split()
    if len(words) < MIN_WORDS_TO_REORDER:
        return None
    words[0], words[1] = words[1], words[0]
    return " ".join(words)


def suffix_changed(name: str, rng: random.Random) -> str | None:
    words = name.split()
    if words[-1] in SUFFIXES:
        words[-1] = rng.choice([s for s in SUFFIXES if s != words[-1]])
    else:
        words.append("LLC")
    return " ".join(words)


EDITS = {
    "typo (1 letter)": typo,
    "transposition": transposition,
    "phonetic respelling": respelling,
    "abbreviation": abbreviation,
    "word dropped": word_dropped,
    "words reordered": words_reordered,
    "suffix changed": suffix_changed,
}


def auc(positives: list[float], negatives: list[float]) -> float:
    """Chance a positive outscores a negative, ties counting half."""
    ordered = sorted(negatives)
    total = 0.0
    for p in positives:
        below = bisect.bisect_left(ordered, p)
        tied = bisect.bisect_right(ordered, p) - below
        total += below + 0.5 * tied
    return total / (len(positives) * len(negatives))


def recall_at_false_match_rate(positives, negatives, rate=FALSE_MATCH_RATE):
    """Share of positives scoring strictly above the negatives' (1 - rate) quantile.

    Strictly above, so a matcher whose look-alikes tie at 1.0 is credited with
    nothing: a threshold cannot separate pairs that score the same.
    """
    cut = sorted(negatives)[int((1 - rate) * len(negatives))]
    return sum(p > cut for p in positives) / len(positives), cut


class Analyzer:
    """Token sets from the index's own analyzers, cached per (subfield, text).

    Asking the live index rather than re-implementing the analyzers in Python
    is the point: a measurement of what the config says would not be a
    measurement of what the index does.
    """

    def __init__(self, es, index, field):
        self.es, self.index, self.field = es, index, field
        self._cache = {}

    def tokens(self, subfield, text):
        key = (subfield, text)
        if key not in self._cache:
            response = self.es.indices.analyze(
                index=self.index, field=f"{self.field}.{subfield}", text=text
            )
            self._cache[key] = [t["token"] for t in response["tokens"]]
        return self._cache[key]

    def cleaned(self, text):
        """The name as the sweep's `.clean` subfield sees it, joined for string measures."""
        return " ".join(self.tokens("clean", text))


def matchers(analyzer):
    def token_overlap(subfield):
        return lambda a, b: blended_overlap(
            set(analyzer.tokens(subfield, a)), set(analyzer.tokens(subfield, b))
        )

    return {
        "double_metaphone (.phonetic)": token_overlap("phonetic"),
        "beider_morse (.phonetic_bm)": token_overlap("phonetic_bm"),
        "exact tokens (.clean)": token_overlap("clean"),
        "fuzzy tokens (AUTO edits)": lambda a, b: fuzzy_token_overlap(
            analyzer.tokens("clean", a), analyzer.tokens("clean", b)
        ),
        "edit distance (whole name)": lambda a, b: edit_similarity(
            analyzer.cleaned(a), analyzer.cleaned(b)
        ),
        "char trigrams (whole name)": lambda a, b: trigram_similarity(
            analyzer.cleaned(a), analyzer.cleaned(b)
        ),
    }


def normalize(name):
    return re.sub(r"\s+", " ", str(name).upper()).strip()


def sample_names(es, index, field, analyzer, count, seed):
    """A seeded random sample of plain names that leave at least one cleaned token.

    Names with characters outside letters, digits and common punctuation are
    skipped, because the edits above are defined over A-Z.
    """
    response = es.search(
        index=index,
        size=count * 2,
        query={
            "function_score": {
                "query": {"exists": {"field": field}},
                "random_score": {"seed": seed, "field": "_seq_no"},
            }
        },
        source=[field],
    )
    names = []
    for hit in response["hits"]["hits"]:
        name = normalize(hit["_source"][field])
        if re.fullmatch(r"[A-Z0-9 &.,'-]+", name) and analyzer.tokens("clean", name):
            names.append(name)
    return list(dict.fromkeys(names))[:count]


def lookalike_pairs(es, index, field, analyzer, names):
    pairs = []
    for name in names:
        response = es.search(
            index=index, size=10, query={"match": {field: name}}, source=[field]
        )
        own = set(analyzer.tokens("clean", name))
        found = 0
        for hit in response["hits"]["hits"]:
            other = normalize(hit["_source"].get(field, ""))
            if other and other != name and set(analyzer.tokens("clean", other)) != own:
                pairs.append((name, other))
                found += 1
                if found == LOOKALIKES_PER_NAME:
                    break
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--index", default="carriers-000001", help="alias or index")
    parser.add_argument("--field", default="legal_name")
    parser.add_argument("--names", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--negatives", choices=["lookalike", "random"], default="lookalike")
    parser.add_argument("--random-pairs", type=int, default=3000)
    args = parser.parse_args()

    es = Elasticsearch(args.host, request_timeout=60)
    concrete = next(iter(es.indices.get(index=args.index)))
    analyzer = Analyzer(es, concrete, args.field)
    rng = random.Random(args.seed)

    names = sample_names(es, concrete, args.field, analyzer, args.names, args.seed)
    positives = defaultdict(list)
    for name in names:
        for label, edit in EDITS.items():
            edited = edit(name, rng)
            if edited and edited != name:
                positives[label].append((name, edited))
    if args.negatives == "random":
        negatives = [tuple(rng.sample(names, 2)) for _ in range(args.random_pairs)]
    else:
        negatives = lookalike_pairs(es, concrete, args.field, analyzer, names)

    print(f"index {concrete}  field {args.field}  seed {args.seed}  negatives {args.negatives}")
    print(f"names {len(names)}  unrelated pairs {len(negatives)}")
    print("edited pairs: " + ", ".join(f"{k} {len(v)}" for k, v in positives.items()))

    for label, score in matchers(analyzer).items():
        # An unevaluable pair scores 0.0 here, as it cannot match at any threshold.
        neg = [s if (s := score(a, b)) is not None else 0.0 for a, b in negatives]
        _, cut = recall_at_false_match_rate([0.0], neg)
        print(f"\n{label}  (unrelated: mean {sum(neg) / len(neg):.3f}, 99th pct {cut:.3f})")
        every = []
        for edit_label, pairs in positives.items():
            pos = [s if (s := score(a, b)) is not None else 0.0 for a, b in pairs]
            every += pos
            recall, _ = recall_at_false_match_rate(pos, neg)
            print(f"  {edit_label:22s} AUC {auc(pos, neg):.3f}  recall@1% {recall:6.1%}")
        recall, _ = recall_at_false_match_rate(every, neg)
        print(f"  {'all edits':22s} AUC {auc(every, neg):.3f}  recall@1% {recall:6.1%}")


if __name__ == "__main__":
    main()
