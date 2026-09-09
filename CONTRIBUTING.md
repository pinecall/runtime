# Contributing

Everything here is written by a person, one file at a time, and read the same way: a one-line
docstring opening every file, no file over 400 lines, names that read as sentences, the why in
`docs/decisions/<module>.md`. The one generated file is `.env.example`, which
`scripts/generate-env-example` renders from `_settings.py`; a test fails when the two drift.

Before a commit, `scripts/format`, then `scripts/lint` and `scripts/test` must both exit 0.
Commits carry a subject line and a short body; versions and tags are the maintainer's call.

Documentation is part of a change, not a follow-up: a commit that moves a module edits
`ARCHITECTURE.md` with it, one that changes a command edits `README.md`, one that changes a
public contract edits the page under `docs/protocol/`, and anything a user would notice gains a
line in `CHANGELOG.md` under `Unreleased`. When a page and the code disagree, the page is the bug.
