"""Find identifiers in outgoing text that resolve to an entity the matcher flagged.

CLAUDE.md forbids naming a flagged entity in anything committed or published —
code, comments, docs, config, commit messages, PR text — because a pair score
here is an analysis, not a finding, and the repository is public. The rule has
already been broken once and repaired after the fact (the originals stay in git
history), and until this script nothing mechanical stood between a draft and a
push.

It deliberately decides nothing. Regexes pull out whatever looks like a company
name, street, phone, email, VIN or entity key; the local cluster then says
whether each value belongs to a real record and whether that record appears in
any sweep's pair index. Whether a hit is an accusation or a documented exclusion
(a filing agent named because it is being filtered out) depends on the sentence
around it, which only a reader can judge. So the output is evidence, and the
anonymization-check skill says how to rule on it.

    .venv/bin/python .claude/skills/anonymization-check/scripts/scan_identifiers.py            # staged diff
    .venv/bin/python .claude/skills/anonymization-check/scripts/scan_identifiers.py --range origin/main...HEAD
    .venv/bin/python .claude/skills/anonymization-check/scripts/scan_identifiers.py --text /path/to/pr-body.md

Exit status is the contract a caller can branch on: 0 nothing needs a ruling,
1 at least one value resolved to a flagged entity (or a gitignored data file is
being committed), 2 the cluster could not answer for some project. Status 2
exists because "found nothing" and "could not look" print the same empty table
otherwise — the quiet-success failure this repository keeps meeting.
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from elasticsearch import ApiError, Elasticsearch, NotFoundError, TransportError

# Located from this file rather than `git rev-parse` in the caller's directory, so
# the scan works when invoked from a scratch directory holding a draft, and the
# git commands below always read this repository's index.
REPO_ROOT = Path(__file__).resolve().parents[4]

EXIT_CLEAN = 0
EXIT_REVIEW = 1
EXIT_UNCHECKED = 2

# A value carried by this many source records is a shared value — a filing
# agent, a corporate parent, a placeholder — rather than one entity's
# identifier. CLAUDE.md permits naming those when the text presents them as a
# filtered-out source of false positives. The number is a floor, not a
# calibration: the shared values this repo actually documents sit far above it
# (87 BOC-3 agents cover 519,139 filings; one email address sits on 206
# carriers), while a genuinely identifying name or phone resolves to a handful.
SHARED_THRESHOLD = 25

# Sides a pair document may name its two entities. A lifecycle sweep writes
# predecessor/successor; an all-entities sweep writes left/right. Querying only
# one naming would report every pair from the other kind of sweep as unflagged.
PAIR_SIDES = ("predecessor", "successor", "left", "right")

EXCERPT_CHARS = 140
LOCATIONS_SHOWN = 3

# Synthetic placeholders such as <CARRIER-A> are how CLAUDE.md says to
# anonymize, so anything inside angle brackets is the fix, not the problem.
PLACEHOLDER = re.compile(r"<[^<>\s]+>")

_ORG_SUFFIX = (
    r"LLC|L\.L\.C\.?|INC\.?|INCORPORATED|CORP\.?|CORPORATION|CO\.|COMPANY|LTD\.?|"
    r"LLP|PLLC|TRUCKING|TRANSPORT|TRANSPORTATION|LOGISTICS|FREIGHT|EXPRESS|"
    r"CARRIERS?|HAULING|LINES|ENTERPRISES|HOSPITAL|CLINIC|MEDICAL CENTER|HEALTH SYSTEM"
)
_TITLE_SUFFIX = (
    r"Hospital|Medical Center|Clinic|Health System|Healthcare|Trucking|Transport|"
    r"Logistics|Freight|Hauling|LLC|Inc\.?|Corp\.?|Company"
)
_STREET_SUFFIX = (
    r"ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|HWY|HIGHWAY|"
    r"PKWY|PARKWAY|WAY|CT|COURT|PL|PLACE|CIR|CIRCLE|TER|TERRACE|TRL|TRAIL|PIKE"
)

# Kind of value -> the patterns that extract it. Over-extraction is cheap: a
# value the cluster has never seen is dropped as NOT_FOUND, so these favour
# recall and let the lookup supply the precision.
PATTERNS = {
    "name": [
        # FMCSA legal names put a comma before the suffix (", INC.") and
        # filing agents lead with "#1" or "A+", so both are allowed in a word.
        re.compile(r"(?<![\w#+])(?:[A-Z0-9#][A-Z0-9&'.+#\-]*,?\s+){1,5}(?:" + _ORG_SUFFIX + r")(?![\w])"),
        re.compile(r"\b(?:[A-Z][a-z][\w'&.\-]*,?\s+){1,5}(?:" + _TITLE_SUFFIX + r")(?![\w])"),
    ],
    "street": [
        re.compile(
            r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9][\w'.\-]*\s+){1,4}(?:" + _STREET_SUFFIX + r")\b"
            r"(?:\s+\d{1,5}\b)?(?:\s+[NSEW]\b)?",
            re.IGNORECASE,
        ),
        re.compile(r"\bP\.?\s?O\.?\s+BOX\s+\d+\b", re.IGNORECASE),
    ],
    "phone": [re.compile(r"(?<![\w-])\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}(?![\w-])")],
    "email": [re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b")],
    "vin": [re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b")],
    # An entity key introduced by its own label. Matches short keys too,
    # because the label removes the ambiguity a bare short number has.
    "key": [
        re.compile(
            r"(?i)\b(?:US\s?DOT|DOT(?:[\s_#\-]*(?:number|no\.?|num))?|dot_number|entity_key|"
            r"facility[\s_]id|CCN)[\s\"':=#]{0,4}([0-9A-Z]{3,8})\b"
        )
    ],
    # A bare 5-8 digit number. DOT numbers and CMS facility IDs are both this
    # shape, and so is any uncomma'd count, which is why these are reported
    # separately from labelled keys: a hit here needs the reader to rule out a
    # coincidence before it means anything. Neighbouring punctuation excludes
    # index suffixes (-000001), versions, decimals, dates and comma'd counts.
    "number": [re.compile(r"(?<![\w.,/:#@$\-])\d{5,8}(?![\w,/%:\-]|\.\d)")],
}

# Which configured fields a kind of value is looked up in, keyed by the role a
# matching signal gives the field. Looking a phone up in name fields cannot
# match anything real but can match noise, so each kind stays in its lane.
ROLE_OF_SIGNAL = {
    "name-phonetic": "name",
    "name-token": "name",
    "address": "street",
    "shared-token": "vin",
}


@dataclass(frozen=True)
class Candidate:
    """One extracted value. Frozen so duplicates across files collapse to one lookup."""

    kind: str
    value: str


@dataclass
class Project:
    """What one project's entity-match step needs looked up, read from its own config.

    Built from configuration rather than hardcoded index and field names, so a
    third project is covered the day its entity-match.json lands instead of on
    the day someone remembers to extend this script.
    """

    name: str
    source_alias: str
    entity_key: str
    pair_pattern: str
    fields_by_role: dict[str, list[str]]
    ignore: set[str]


@dataclass
class Finding:
    """A candidate's evidence across every project, plus where the text used it."""

    candidate: Candidate
    locations: list[tuple[str, str]] = field(default_factory=list)
    verdict: str = "NOT_FOUND"
    source_hits: dict[str, str] = field(default_factory=dict)
    flagged_pairs: dict[str, int] = field(default_factory=dict)


def normalize(value: str) -> str:
    """Casefold and collapse whitespace so ignore-list comparison is not defeated by layout."""
    return " ".join(value.split()).casefold()


def digits(value: str) -> str:
    """Phone numbers compare by digits; (000) 000-0000 and 0000000000 are one junk value."""
    return re.sub(r"\D", "", value)


def load_projects(root: Path) -> list[Project]:
    """Every entity-match step in the repo, as the lookups it implies.

    Plain json rather than file_utils.load_from_file: ignore_values is keyed by
    dotted field paths and iterated as a mapping, which a SimpleNamespace would
    only make awkward.
    """
    projects = []
    for match_path in sorted(root.glob("*/configuration/*/entity-match.json")):
        index_path = match_path.with_name("index-config.json")
        match = json.loads(match_path.read_text(encoding="utf-8"))
        if "source_index" not in match or not index_path.exists():
            continue
        template = json.loads(index_path.read_text(encoding="utf-8"))["index"]
        projects.append(
            Project(
                name="{}/{}".format(match_path.parts[-4], match_path.parts[-2]),
                source_alias=match["source_index"],
                entity_key=match["entity"]["key"],
                pair_pattern=template.split("{", 1)[0] + "*",
                fields_by_role=fields_by_role(match),
                ignore=ignore_values(match),
            )
        )
    return projects


def fields_by_role(match: dict) -> dict[str, list[str]]:
    """The fields each kind of value is searched in, taken from the signals themselves.

    Summary fields such as city and state are left out on purpose: they are on
    every pair document but identify nobody, and querying them would only
    attach real-looking hit counts to place names.
    """
    roles: dict[str, set[str]] = {"name": set(), "street": set(), "phone": set(), "email": set(), "vin": set()}
    for signal in match.get("signals", []):
        role = ROLE_OF_SIGNAL.get(signal.get("type", ""))
        if role:
            roles[role].update(signal.get("fields") or [])
        roles["phone"].update(signal.get("phone_fields") or [])
        roles["email"].update(signal.get("text_fields") or [])
        if signal.get("name_field"):
            roles["name"].add(signal["name_field"])
    return {role: sorted(names) for role, names in roles.items()}


def ignore_values(match: dict) -> set[str]:
    """Every value the matcher itself refuses to treat as identifying.

    These are exactly the values CLAUDE.md says to record freely — junk VINs,
    zeroed phones — because an ignore list cannot work unless someone names
    its entries.
    """
    found = set()
    for values in match.get("ignore_values", {}).values():
        for value in values:
            found.add(normalize(value))
            if digits(value):
                found.add(digits(value))
    return found


def extract(text: str) -> Iterator[Candidate]:
    """Every distinct candidate on one line of text.

    A number already captured as a labelled key is not repeated as a bare
    number: the label is the stronger evidence, and listing it twice reads as
    two separate leaks.
    """
    found: dict[Candidate, None] = {}
    for kind, patterns in PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                value = (match.group(1) if pattern.groups else match.group(0)).strip(" .,;:")
                if value:
                    found[Candidate(kind, value)] = None
    labelled = {c.value for c in found if c.kind == "key"}
    yield from (c for c in found if not (c.kind == "number" and c.value in labelled))


def added_lines(diff: str) -> Iterator[tuple[str, str]]:
    """(path:line, text) for each line a unified diff adds.

    Only additions are scanned. A removed line is leaving the tree, and
    reporting it would flag the very commit that fixes a leak.
    """
    path, line_no = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            hunk = re.search(r"\+(\d+)", line)
            line_no = int(hunk.group(1)) if hunk else 0
        elif line.startswith("+") and path:
            yield "{}:{}".format(path, line_no), line[1:]
            line_no += 1


def changed_paths(diff_args: list[str]) -> list[str]:
    """Paths the diff touches, so committed data files can be caught by location alone."""
    result = subprocess.run(
        ["git", "diff", "--name-only", *diff_args], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    return [p for p in result.stdout.splitlines() if p]


def data_paths(paths: Iterable[str]) -> list[str]:
    """Paths under a project's gitignored data directory.

    `*/data/` holds review samples that carry the legal names and addresses of
    flagged carriers; sample_pairs_for_review.py writes there precisely because
    it is ignored. A file there being committed means `git add -f` overrode the
    one guard that keeps those rows out, so it is reported whatever it contains.
    """
    return [p for p in paths if "data" in Path(p).parts[1:-1]]


def diff_sources(range_: str | None, staged: bool, other_sources: bool) -> list[list[str]]:
    """The `git diff` arguments for each diff a scan reads.

    A range and the staged diff are separate sources, and both are read when
    both are asked for. Collapsing them to one, as this once did, scanned only
    the range when given both, silently skipping a revision staged on top of
    a pushed branch and reporting "clean" over text it never read. With no
    source named at all, the staged diff is the default, because the commonest
    question is "is what I'm about to commit safe?".
    """
    sources = []
    if range_:
        sources.append([range_])
    if staged or not (range_ or other_sources):
        sources.append(["--cached"])
    return sources


def gather(args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[str]]:
    """The (location, line) pairs to scan and any committed data paths, per the chosen mode."""
    lines: list[tuple[str, str]] = []
    paths: list[str] = []
    for diff_args in diff_sources(args.range, args.staged, bool(args.text or args.paths)):
        diff = subprocess.run(
            ["git", "diff", "-U0", "--no-color", "--no-ext-diff", *diff_args],
            capture_output=True, text=True, check=True, cwd=REPO_ROOT,
        ).stdout
        lines.extend(added_lines(diff))
        paths.extend(changed_paths(diff_args))
    if args.range:
        # Commit messages are published with the branch, and CLAUDE.md names
        # them explicitly; a diff alone never shows them.
        log = subprocess.run(
            ["git", "log", "--format=%x00%h%n%B", args.range], capture_output=True, text=True, check=True, cwd=REPO_ROOT
        ).stdout
        for entry in log.split("\x00")[1:]:
            sha, _, body = entry.partition("\n")
            lines.extend(("commit {}:{}".format(sha, n), text) for n, text in enumerate(body.splitlines(), 1))
    # Drafts quote real names, which are often non-ASCII. Read them as UTF-8
    # explicitly rather than in the platform encoding: under a Latin-1 locale
    # the platform default turned an accented name into mojibake that can no
    # longer match the cluster, so a real entity came back NOT_FOUND.
    for source in args.text or []:
        content = sys.stdin.buffer.read().decode("utf-8") if source == "-" else Path(source).read_text(encoding="utf-8")
        label = "<stdin>" if source == "-" else source
        lines.extend(("{}:{}".format(label, n), text) for n, text in enumerate(content.splitlines(), 1))
    for source in args.paths or []:
        content = Path(source).read_text(encoding="utf-8", errors="replace")
        lines.extend(("{}:{}".format(source, n), text) for n, text in enumerate(content.splitlines(), 1))
    return lines, paths


def is_allowed(candidate: Candidate, projects: list[Project]) -> bool:
    """Placeholders and ignore-listed junk are the sanctioned way to write examples."""
    if PLACEHOLDER.fullmatch(candidate.value):
        return True
    keys = {normalize(candidate.value), digits(candidate.value)} - {""}
    return any(keys & project.ignore for project in projects)


def field_clause(properties: dict, path: str, value: str) -> dict | None:
    """The query that matches `value` in one configured field the way the matcher reads it.

    `.tokens` is preferred where it exists (streets) because `.clean` keeps a
    whole street as one token, so the partial address prose usually quotes
    could never phrase-match it; `.tokens` also normalises HWY/HIGHWAY and E/EAST
    the way the address signal does. Otherwise `.clean`, the form the name
    signals compare. The phonetic subfields are avoided entirely: searching
    every field was tried first, and phonetic matching let an invented example
    name hit more than a thousand carriers. Fields under a nested mapping are
    skipped, since a plain query cannot see inside nested documents.
    """
    node: dict = {"properties": properties}
    for part in path.split("."):
        node = node.get("properties", {}).get(part)
        if node is None:
            return None
        if node.get("type") == "nested":
            return None
    subfields = node.get("fields", {})
    for analysed in ("tokens", "clean"):
        if analysed in subfields:
            return {"match_phrase": {"{}.{}".format(path, analysed): value}}
    if node.get("type") == "keyword":
        return {"term": {path: value}}
    if "keyword" in subfields:
        return {"term": {path + ".keyword": value}}
    return {"match_phrase": {path: value}}


def source_lookup(es: Elasticsearch, project: Project, properties: dict, candidate: Candidate) -> tuple[str, list[str]]:
    """How many source records carry the value (capped at the shared threshold), and their keys."""
    if candidate.kind in ("key", "number"):
        clauses = [{"term": {project.entity_key: candidate.value}}]
    else:
        clauses = [
            clause
            for path in project.fields_by_role.get(candidate.kind, [])
            if (clause := field_clause(properties, path, candidate.value))
        ]
    if not clauses:
        return "", []
    response = es.search(
        index=project.source_alias,
        size=SHARED_THRESHOLD,
        query={"bool": {"should": clauses, "minimum_should_match": 1}},
        source=[project.entity_key],
        track_total_hits=SHARED_THRESHOLD,
    )
    total = response["hits"]["total"]
    count = "{}{}".format(total["value"], "+" if total["relation"] == "gte" else "")
    keys = [str(hit["_source"][project.entity_key]) for hit in response["hits"]["hits"] if project.entity_key in hit["_source"]]
    return count, keys


def pair_count(es: Elasticsearch, project: Project, keys: list[str]) -> int:
    """Pairs, across every sweep this project has kept, that name any of these entities.

    Every retained sweep counts, not just the one the alias points at: an
    entity matched by last week's experiment was still matched, and the text
    being checked may describe that run.
    """
    if not keys:
        return 0
    clauses = [{"match_phrase": {side + ".entity_key": key}} for key in keys for side in PAIR_SIDES]
    response = es.count(
        index=project.pair_pattern,
        query={"bool": {"should": clauses, "minimum_should_match": 1}},
        allow_no_indices=True,
        ignore_unavailable=True,
    )
    return response["count"]


def verdict_for(finding: Finding) -> str:
    """Rank the evidence: shared outranks flagged, because a shared value identifies no one entity."""
    counts = [hits for hits in finding.source_hits.values() if hits]
    if any(hits.endswith("+") for hits in counts):
        return "SHARED"
    if any(finding.flagged_pairs.values()):
        return "FLAGGED"
    if any(hits != "0" for hits in counts):
        return "REAL"
    return "NOT_FOUND"


def evaluate(es: Elasticsearch, projects: list[Project], findings: list[Finding]) -> list[str]:
    """Fill each finding's verdict; return the projects that could not be checked."""
    unchecked = []
    for project in projects:
        try:
            properties = next(iter(es.indices.get_mapping(index=project.source_alias).values()))["mappings"]["properties"]
        except NotFoundError:
            unchecked.append("{} (source index {} does not exist)".format(project.name, project.source_alias))
            continue
        for finding in findings:
            if finding.verdict == "ALLOWED":
                continue
            hits, keys = source_lookup(es, project, properties, finding.candidate)
            finding.source_hits[project.name] = hits
            if keys and not hits.endswith("+"):
                finding.flagged_pairs[project.name] = pair_count(es, project, keys)
    for finding in findings:
        if finding.verdict != "ALLOWED":
            finding.verdict = verdict_for(finding)
    return unchecked


def report(findings: list[Finding], data_files: list[str], unchecked: list[str]) -> None:
    """Print what needs a ruling first; summarise what does not."""
    if data_files:
        print("COMMITTED DATA FILES (gitignored */data/ content — remove from the commit):")
        for path in data_files:
            print("  " + path)
        print()
    order = ["FLAGGED", "REAL", "SHARED"]
    for verdict in order:
        group = [f for f in findings if f.verdict == verdict]
        if not group:
            continue
        print("{} ({})".format(verdict, len(group)))
        for finding in group:
            evidence = ", ".join(
                "{}: {} source records, {} pairs".format(name, hits, finding.flagged_pairs.get(name, "-"))
                for name, hits in finding.source_hits.items()
                if hits and hits != "0"
            )
            print("  [{}] {}  ({})".format(finding.candidate.kind, finding.candidate.value, evidence))
            for where, line in finding.locations[:LOCATIONS_SHOWN]:
                print("      {}  {}".format(where, line[:EXCERPT_CHARS]))
            if len(finding.locations) > LOCATIONS_SHOWN:
                print("      ... {} more".format(len(finding.locations) - LOCATIONS_SHOWN))
        print()
    quiet = {v: sum(1 for f in findings if f.verdict == v) for v in ("ALLOWED", "NOT_FOUND")}
    print("scanned {} candidates; {} allowed (placeholder or ignore list), {} not found in any source index".format(
        len(findings), quiet["ALLOWED"], quiet["NOT_FOUND"]))
    for line in unchecked:
        print("UNCHECKED: " + line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staged", action="store_true", help="scan the staged diff (the default when no other source is given)")
    parser.add_argument("--range", help="scan the diff and commit messages of a git range, e.g. origin/main...HEAD")
    parser.add_argument("--text", action="append", help="scan a file of draft text (commit message, PR body); '-' reads stdin")
    parser.add_argument("--paths", nargs="+", help="scan whole files rather than a diff")
    parser.add_argument("--host", default="http://localhost:9200")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args()

    projects = load_projects(REPO_ROOT)
    lines, paths = gather(args)

    by_candidate: dict[Candidate, Finding] = {}
    for where, text in lines:
        for candidate in extract(text):
            by_candidate.setdefault(candidate, Finding(candidate)).locations.append((where, text.strip()))
    findings = list(by_candidate.values())
    for finding in findings:
        if is_allowed(finding.candidate, projects):
            finding.verdict = "ALLOWED"

    es = Elasticsearch(args.host)
    try:
        unchecked = evaluate(es, projects, findings)
    except (ApiError, TransportError) as error:
        print("UNCHECKED: cluster at {} did not answer ({}). Nothing was verified — this is not a clean result.".format(
            args.host, type(error).__name__))
        return EXIT_UNCHECKED

    data_files = data_paths(paths)
    if args.json:
        print(json.dumps({
            "findings": [
                {"kind": f.candidate.kind, "value": f.candidate.value, "verdict": f.verdict,
                 "source_hits": f.source_hits, "flagged_pairs": f.flagged_pairs,
                 "locations": [w for w, _ in f.locations]}
                for f in findings if f.verdict not in ("ALLOWED", "NOT_FOUND")
            ],
            "data_files": data_files,
            "unchecked": unchecked,
        }, indent=2))
    else:
        report(findings, data_files, unchecked)

    if unchecked:
        return EXIT_UNCHECKED
    if data_files or any(f.verdict == "FLAGGED" for f in findings):
        return EXIT_REVIEW
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
