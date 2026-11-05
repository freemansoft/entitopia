---
name: anonymization-check
description: Check outgoing entitopia text for real companies, hospitals, DOT numbers, facility IDs, addresses, phones or emails that the matcher flagged, before anything is committed, pushed or published. Use it before every git commit, push or pull request in this repo, and whenever you draft a commit message, a PR description, README results, measured sweep numbers, examples drawn from live data, or comments quoting pair output. Also use it when the user asks whether something is safe to publish, asks you to anonymize, or pastes a carrier or hospital record. A sweep score is an analysis, not a finding, and the repo is public, so naming a flagged entity publishes an unverified fraud accusation. Run it even when the change looks like it is only code; examples hide in docstrings.
---

# Anonymization check

CLAUDE.md forbids naming an entity the matcher flagged anywhere that gets
published. That covers code, comments, docstrings, config, docs, commit
messages and PR text. The thresholds are uncalibrated, and a high score often
reflects a shared filing agent or a placeholder rather than wrongdoing. This
repository is public. It has already needed one after-the-fact repair, and the
original lines are still in git history. Once a leak is pushed, it can't
really be undone.

This skill pairs a scanner that gathers evidence with your judgement about the
sentence around each hit. The scanner can't tell an accusation from a
documented exclusion. You can.

## 1. Make sure the cluster can answer

The scanner resolves every value against the local Elasticsearch at
`http://localhost:9200`. Check that it's up:

```bash
curl -s http://localhost:9200/_cluster/health
```

If it's down, start it with `docker compose -f docker/compose.yml up -d` (this
machine runs Colima, so `colima start` may be needed first). Starting the
container touches no data. Leave out `--build` unless the image is missing. If
the cluster can't be brought up, stop and tell the user. Never report "clean"
from an unchecked run.

## 2. Run the scanner on everything that will be published

```bash
SCAN=.claude/skills/anonymization-check/scripts/scan_identifiers.py
```

- **Before a commit:** stage first, then write the draft commit message to a
  scratch file and scan both together:
  `.venv/bin/python $SCAN --staged --text /path/to/commit-msg.txt`
- **Before a push or PR:** scan the branch's diff and its commit messages,
  plus the draft PR body:
  `.venv/bin/python $SCAN --range origin/main...HEAD --text /path/to/pr-body.md`
- **Auditing existing files:** `.venv/bin/python $SCAN --paths README.md docs/*.md`

Write draft text to the session scratchpad, not the repo, so the draft itself
never becomes a committed file.

Exit status: `0` nothing needs a ruling, `1` at least one value resolved to a
flagged entity or a gitignored data file is being committed, `2` the cluster
couldn't answer. Treat `2` as "not checked", never as "clean".

## 3. Rule on each finding

The scanner prints only what needs a decision. Each finding shows the value's
kind, how many source records carry it, how many sweep pairs name those
records, and the lines that use it. The pair count covers every retained sweep
index (`chameleon-candidates-*`, `hospital-duplicates-*`), not just the one the
alias points at. An entity matched by an older experiment was still matched, so
there's no need to re-check the other sweeps by hand.

| Verdict                  | What it means                                                                                                                     | What to do                                                                                                                                                                                                                                                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **COMMITTED DATA FILES** | A path under a project's gitignored `*/data/` is in the change. Review samples there carry flagged carriers' names and addresses. | Unstage it (`git restore --staged <path>`). Never commit it, whatever it contains.                                                                                                                                                                                                                                                  |
| **FLAGGED**              | The value belongs to a handful of real records, and at least one appears in a sweep's pairs.                                      | Replace it with a synthetic placeholder that keeps the pattern the example illustrates. For a `[number]` with no label, first rule out a coincidence: a count or an unrelated ID that happens to equal a key needs no change (writing counts with thousands separators, as this repo does, keeps the scanner from extracting them). |
| **REAL**                 | A real record carries the value, but no sweep matched it.                                                                         | Usually a data-quality example, such as a DOT number quoted to show zero-padding, and acceptable. If the surrounding text implies wrongdoing, or the value isn't load-bearing, use a synthetic one anyway.                                                                                                                          |
| **SHARED**               | 25 or more records carry the value: a filing agent, a corporate parent, a placeholder.                                            | Allowed when the text presents it as a shared value being filtered out. CLAUDE.md expects those to be named, so the ignore list can be maintained. Anonymize it if the text presents it as one suspect.                                                                                                                             |

ALLOWED (placeholders and ignore-list junk such as `(000) 000-0000`) and
NOT_FOUND are only counted, because neither identifies anyone.

**Placeholders.** Follow the repo's existing style, `<CARRIER-A>` and
`<CARRIER-B>`, and extend it in kind: `<DOT-1>`, `<STREET-A>`, `<PHONE-A>`,
`<EMAIL-A>`, `<HOSPITAL-A>`. Preserve what made the example worth writing,
such as "identical legal name" or "same street, one day after shutdown". If an
example stops being useful once the name is gone, the name was doing
argumentative work, and the sentence needs rewriting, not a placeholder.

**Measured numbers stay.** Aggregate counts, percentages, index names, dates
and fingerprints identify no one. Keep them exactly as measured.

## 4. Read the diff yourself for what the scanner cannot see

The patterns favour recall, but they still miss things. Before you call the
change clean, skim the added lines for:

- Company names without a corporate suffix, such as a bare two-word trade name.
- Names of people: officers, process agents, contacts.
- A city and street combination quoted without a house number.
- Values inside JSON, CSV or test fixtures, and text in images.
- Anything next to "chameleon", "duplicate", "fraud", a score or a pair.

Treat any proper noun near pair or score language as a finding, and rule on it
the same way.

## 5. Re-scan, then report

After editing, re-run the same scan until nothing is left FLAGGED without a
ruling. Then tell the user what was found and what you changed. Refer to each
finding by file, line and kind, such as "a FLAGGED street at README.md:212",
rather than repeating the value. The user may paste your summary into a PR.

If the leak is already pushed, a fixing commit only removes it from the tip.
It stays in history. Say so plainly, and leave any history rewrite or
force-push to the user's decision.
