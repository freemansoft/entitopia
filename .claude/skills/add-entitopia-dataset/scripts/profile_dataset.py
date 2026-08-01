#!/usr/bin/env python3
"""Profile a CSV before configuring an entitopia index for it.

Every mapping decision in this project depends on facts about the data that are
invisible until measured: whether a column mixes numeric and non-numeric values
(which makes dynamic type inference drop documents), whether a code column has
leading zeros (which a numeric type destroys), whether a candidate key is
actually unique, and whether a field has enough distinct values to carry a
matching signal at all.

Those measurements were previously done ad hoc, one throwaway snippet at a time,
and the same mistakes kept surfacing. This runs them all in one pass so the
answers arrive before the config is written rather than after a production load
turns up short.

Streams the file rather than loading it, because the datasets this targets run
to millions of rows. Distinct-value tracking is capped so a high-cardinality
column cannot exhaust memory; a capped column is reported as such rather than
reported wrongly.

Usage:
    profile_dataset.py <path.csv>
    profile_dataset.py <path.csv> --rows 200000        # sample instead of full scan
    profile_dataset.py <path.csv> --key dot_number     # test one candidate key
    profile_dataset.py <path.csv> --key a --key b      # test a composite key
"""

import argparse
import csv
import re
import sys
from collections import Counter

# Above this many distinct values we stop tracking them individually. The exact
# count stops mattering long before this — what we need to know is "high
# cardinality", and any column past this threshold is fingerprint-like rather
# than category-like.
MAX_TRACKED_DISTINCT = 200_000

# A column whose distinct count is at or below this is a category (status codes,
# state abbreviations). Categories make good filters and poor fingerprints.
CATEGORY_MAX_DISTINCT = 200

# A shared value carried by more than this share of rows cannot discriminate
# between entities — two unrelated records will collide on it by chance.
COMMON_VALUE_SHARE = 0.01

# Keep a few concrete offenders per column, so a warning can show what it means
# rather than only asserting a count.
MAX_EXAMPLES = 3

# Past this share of blanks, blank-vs-blank comparisons would dominate any
# signal reading the column.
SPARSE_BLANK_SHARE = 0.2

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ORACLE_DATE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}$")
US_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
INTEGERISH = re.compile(r"^-?\d+$")
FLOATISH = re.compile(r"^-?\d*\.\d+$")
LEADING_ZERO = re.compile(r"^0\d+$")

csv.field_size_limit(10_000_000)


class ColumnProfile:
    """Accumulates one column's statistics in a single streaming pass."""

    def __init__(self, name):
        self.name = name
        self.total = 0
        self.blank = 0
        self.values = Counter()
        self.distinct_capped = False
        self.n_int = 0
        self.n_float = 0
        self.n_other = 0
        self.n_leading_zero = 0
        self.n_iso_date = 0
        self.n_oracle_date = 0
        self.n_us_date = 0
        self.max_len = 0
        self.example_non_numeric = []

    def add(self, raw):
        self.total += 1
        value = (raw or "").strip()
        if not value:
            self.blank += 1
            return

        self.max_len = max(self.max_len, len(value))

        if not self.distinct_capped:
            self.values[value] += 1
            if len(self.values) > MAX_TRACKED_DISTINCT:
                self.distinct_capped = True
                self.values.clear()

        if INTEGERISH.match(value):
            self.n_int += 1
            if LEADING_ZERO.match(value):
                self.n_leading_zero += 1
        elif FLOATISH.match(value):
            self.n_float += 1
        else:
            self.n_other += 1
            if len(self.example_non_numeric) < MAX_EXAMPLES:
                self.example_non_numeric.append(value)

        if ISO_DATE.match(value):
            self.n_iso_date += 1
        elif ORACLE_DATE.match(value):
            self.n_oracle_date += 1
        elif US_DATE.match(value):
            self.n_us_date += 1

    @property
    def populated(self):
        return self.total - self.blank

    def warnings(self):
        """Conditions that will silently corrupt a load if left unmapped.

        Each of these corresponds to an incident this project has already had;
        the messages name the consequence rather than the rule so a reader can
        tell whether it applies to them.
        """
        out = []
        numeric = self.n_int + self.n_float

        if numeric and self.n_other:
            out.append(
                "MIXED TYPES: {:,} numeric and {:,} non-numeric values (e.g. {}). "
                "Dynamic mapping infers a numeric type from whichever it sees first, "
                "then every non-conforming row fails to index and the document is "
                "dropped. Pin as keyword.".format(
                    numeric, self.n_other, ", ".join(repr(v) for v in self.example_non_numeric)
                )
            )
        elif self.n_leading_zero:
            out.append(
                "LEADING ZEROS: {:,} values like '0...' would become integers and lose "
                "them (00602 -> 602). Pin as keyword.".format(self.n_leading_zero)
            )
        elif numeric and not self.n_other and self.populated:
            out.append(
                "ALL-NUMERIC: infers as a numeric type. If this is an identifier or "
                "code rather than a quantity, pin as keyword — a join between a "
                "numeric and a keyword field matches nothing."
            )

        if self.n_oracle_date or self.n_us_date:
            fmt = "dd-MMM-yy" if self.n_oracle_date else "M/D/Y"
            out.append(
                "NON-ISO DATES: {:,} values look like {}. Elasticsearch will not "
                "auto-detect this, so the field lands as text. Convert in an ingest "
                "script with an explicit century pivot — mapping it directly risks "
                "resolving a 1974 date as 2074.".format(
                    self.n_oracle_date or self.n_us_date, fmt
                )
            )

        if self.populated and self.blank / self.total > SPARSE_BLANK_SHARE:
            out.append(
                "SPARSE: {:.1%} blank. A signal reading this must treat blank as "
                "'not evaluable', never as a match — otherwise two records with "
                "nothing here will appear to agree.".format(self.blank / self.total)
            )

        if not self.distinct_capped and self.populated:
            distinct = len(self.values)
            if 0 < distinct <= CATEGORY_MAX_DISTINCT:
                top_value, top_count = self.values.most_common(1)[0]
                share = top_count / self.populated
                if share > COMMON_VALUE_SHARE:
                    collision = sum((c / self.populated) ** 2 for c in self.values.values())
                    out.append(
                        "LOW CARDINALITY: {:,} distinct values; most common is {!r} at "
                        "{:.1%}. Two unrelated records share a value ~{:.1%} of the time "
                        "by chance, so this is a filter, not a fingerprint. Weight it "
                        "low or weight it by rarity.".format(
                            distinct, top_value, share, collision
                        )
                    )
        return out


def profile(path, max_rows=None):
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            sys.exit("{}: no header row".format(path))
        columns = {name: ColumnProfile(name) for name in reader.fieldnames}
        rows = 0
        for row in reader:
            for name, col in columns.items():
                col.add(row.get(name))
            rows += 1
            if max_rows and rows >= max_rows:
                break
    return reader.fieldnames, columns, rows


def check_key(path, key_columns, max_rows=None):
    """Report whether a candidate key is unique, and how it fails if not.

    Distinguishes 'collides on rows that genuinely differ' from 'collapses rows
    that are byte-identical'. The second is usually the desired outcome — a
    composite key merging exact duplicates is doing its job — so reporting them
    the same way would send someone hunting for a longer key they do not need.
    """
    seen = {}
    duplicate_keys = 0
    identical_rows = 0
    rows = 0
    example = None

    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in key_columns if c not in (reader.fieldnames or [])]
        if missing:
            return {"error": "columns not in file: {}".format(", ".join(missing))}
        for row in reader:
            composite = "|".join((row.get(c) or "").strip() for c in key_columns)
            fingerprint = tuple(sorted(row.items()))
            if composite in seen:
                duplicate_keys += 1
                if seen[composite] == fingerprint:
                    identical_rows += 1
                elif example is None:
                    example = composite
            else:
                seen[composite] = fingerprint
            rows += 1
            if max_rows and rows >= max_rows:
                break

    return {
        "rows": rows,
        "distinct": len(seen),
        "duplicate_keys": duplicate_keys,
        "identical_rows": identical_rows,
        "real_collisions": duplicate_keys - identical_rows,
        "example_collision": example,
    }


def _print_columns(fieldnames, columns):
    """The shape facts a mapping decision needs, one row per column."""
    print("\nCOLUMNS\n")
    header = "{:<34} {:>10} {:>8} {:>9}  {}".format("column", "distinct", "blank%", "maxlen", "looks like")
    print(header)
    print("-" * len(header))
    for name in fieldnames:
        col = columns[name]
        distinct = "capped" if col.distinct_capped else "{:,}".format(len(col.values))
        blank_pct = (col.blank / col.total * 100) if col.total else 0.0
        kinds = []
        if col.n_iso_date:
            kinds.append("iso-date")
        if col.n_oracle_date:
            kinds.append("oracle-date")
        if col.n_us_date:
            kinds.append("us-date")
        if col.n_int and not col.n_other and not col.n_float:
            kinds.append("integer")
        if col.n_float:
            kinds.append("float")
        if col.n_other:
            kinds.append("text")
        print(
            "{:<34} {:>10} {:>7.1f}% {:>9}  {}".format(
                name[:34], distinct, blank_pct, col.max_len, ",".join(kinds) or "-"
            )
        )


def _print_warnings(fieldnames, columns):
    """The section that matters most — every entry is a silent-corruption risk."""
    print("\nWARNINGS — each of these silently corrupts a load if ignored\n")
    any_warning = False
    for name in fieldnames:
        issues = columns[name].warnings()
        if issues:
            any_warning = True
            print("  {}".format(name))
            for issue in issues:
                print("      - {}".format(issue))
    if not any_warning:
        print("  none")


def _print_signals(fieldnames, columns):
    """Split columns by whether they can identify an entity or only filter a population.

    The distinction drives weighting: a fingerprint field can carry a match on
    its own, a category field can only corroborate one.
    """
    print("\nCANDIDATE SIGNALS\n")
    fingerprints, categories = [], []
    for name in fieldnames:
        col = columns[name]
        if not col.populated:
            continue
        if col.distinct_capped or len(col.values) > CATEGORY_MAX_DISTINCT:
            fingerprints.append(name)
        else:
            categories.append(name)
    print("  high cardinality (usable as identity/fingerprint signals):")
    print("      {}".format(", ".join(fingerprints) or "none"))
    print("  low cardinality (usable as filters/selectors, not fingerprints):")
    print("      {}".format(", ".join(categories) or "none"))


def _print_key_check(csv_path, key, max_rows):
    """Report whether a candidate id_field is safe, and how it fails if not."""
    print("\nCANDIDATE KEY: {}\n".format(" + ".join(key)))
    result = check_key(csv_path, key, max_rows)
    if "error" in result:
        print("  ERROR: {}".format(result["error"]))
        return
    if result["duplicate_keys"] == 0:
        print("  UNIQUE across {:,} rows — safe as id_field".format(result["rows"]))
        return
    print("  NOT unique: {:,} distinct keys over {:,} rows".format(result["distinct"], result["rows"]))
    print(
        "    {:,} duplicates are byte-identical rows (a composite key correctly "
        "collapses these — usually the desired outcome)".format(result["identical_rows"])
    )
    print("    {:,} are real collisions between rows that differ".format(result["real_collisions"]))
    if result["example_collision"]:
        print("       example colliding key: {!r}".format(result["example_collision"]))
    if result["real_collisions"]:
        print("    -> add a column that distinguishes them, or accept the merge deliberately")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv_path")
    parser.add_argument("--rows", type=int, default=None, help="stop after N rows (default: full scan)")
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        dest="key",
        help="candidate id_field column; repeat for a composite key",
    )
    args = parser.parse_args()

    fieldnames, columns, rows = profile(args.csv_path, args.rows)

    print("=" * 78)
    print("{}  —  {:,} rows scanned, {} columns".format(args.csv_path, rows, len(fieldnames)))
    if args.rows:
        print("SAMPLED to {:,} rows — uniqueness results are provisional".format(args.rows))
    print("=" * 78)

    _print_columns(fieldnames, columns)
    _print_warnings(fieldnames, columns)
    _print_signals(fieldnames, columns)
    if args.key:
        _print_key_check(args.csv_path, args.key, args.rows)
    print()


if __name__ == "__main__":
    main()
