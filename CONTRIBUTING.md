# Contributing

Everything here is written by a person, one file at a time, and read the same way: a one-line
docstring opening every file, no file over 400 lines, names that read as sentences, the why in
`docs/decisions/<module>.md`. The one generated file is `.env.example`, which
`scripts/generate-env-example` renders from `_settings.py`; a test fails when the two drift.

Before a commit, `scripts/format`, then `scripts/lint` and `scripts/test` must both exit 0.
`scripts/bootstrap` installs the hooks in `.pre-commit-config.yaml` (with `prek`, or
`pre-commit`), which run the format, the fixable lint rules, the lock check and the `.env.example`
check on what a commit touches. `scripts/lint` ends with `deptry`: a package the tree imports is
one `pyproject.toml` declares, and one it declares is one the tree imports. `scripts/test`
measures branch coverage and holds it to the floor in `pyproject.toml`, which moves up with a
release and never down. Ring 0 (`pytest -m unit`) opens no socket and reads no key: what a test
fakes it fakes once, in `tests/clocks.py`, `tests/orgs/boxes.py`, `tests/api/no_vault.py`, and
what must hold for every input — a chunk under the cap, a fusion blind to the order of its
branches, a snapshot that resumes to the state it was cut from — is stated once as a
`hypothesis` property beside the examples. Commits carry a subject line and a short body;
versions and tags are the maintainer's call.

Documentation is part of a change, not a follow-up: a commit that moves a module edits
`ARCHITECTURE.md` with it, one that changes a command edits `README.md`, one that changes a
public contract edits the page under `docs/protocol/`, and anything a user would notice gains a
line in `CHANGELOG.md` under `Unreleased`. When a page and the code disagree, the page is the bug.
