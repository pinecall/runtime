# Contributing

Everything here is written by a person, one file at a time, and read the same way: a one-line
docstring opening every file, no file over 400 lines, names that read as sentences, the why in
`docs/decisions/<module>.md`. The one generated file is `.env.example`, which
`scripts/generate-env-example` renders from `_settings.py`; a test fails when the two drift.

Before a commit, `scripts/format`, then `scripts/lint` and `scripts/test` must both exit 0.
Commits carry a subject line and a short body; versions and tags are the maintainer's call.
