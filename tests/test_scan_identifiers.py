"""Which git diffs the anonymization-check scanner reads.

Exists because the scanner once read only the branch range when given both
`--range` and `--staged`, silently skipping the staged changes and reporting
"clean" over text it never looked at. That is the quiet-success failure the
scanner exists to prevent, in the scanner itself, so the choice of sources is
pinned here, where it can be tested without git or a cluster.
"""

import importlib.util
from pathlib import Path

_SCANNER = Path(__file__).resolve().parent.parent / ".claude/skills/anonymization-check/scripts/scan_identifiers.py"
_spec = importlib.util.spec_from_file_location("scan_identifiers", _SCANNER)
scan_identifiers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_identifiers)

diff_sources = scan_identifiers.diff_sources


def test_range_and_staged_together_read_both_diffs():
    # The bug: this returned only the range, so a revision staged on top of a
    # pushed branch was never scanned.
    assert diff_sources("origin/main...HEAD", staged=True, other_sources=False) == [
        ["origin/main...HEAD"],
        ["--cached"],
    ]


def test_range_alone_reads_only_the_range():
    assert diff_sources("origin/main...HEAD", staged=False, other_sources=False) == [["origin/main...HEAD"]]


def test_no_source_at_all_defaults_to_the_staged_diff():
    assert diff_sources(None, staged=False, other_sources=False) == [["--cached"]]


def test_text_or_paths_alone_read_no_diff():
    assert diff_sources(None, staged=False, other_sources=True) == []


def test_staged_with_text_reads_the_staged_diff():
    assert diff_sources(None, staged=True, other_sources=True) == [["--cached"]]
