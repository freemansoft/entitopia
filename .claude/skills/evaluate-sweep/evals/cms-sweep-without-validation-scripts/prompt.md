---
tags: [entitopia]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

/evaluate-sweep CMS-Providers hospital-duplicates: candidate hospital-duplicates-2026.08.20-000001 against the baseline declared in metrics.json. I wrote the expectations before re-running, they're in expect-cms-baseline-check.json. Is the sweep good? Put the verdict in report.md.

Input files: expect-cms-baseline-check.json
