# Contributing

## Running it

```
uv sync            # Python 3.12; `uv` makes the venv and installs every dev tool
make check         # the architecture rules (tests/rules/) and every suite with no database
make test          # every suite on a local Postgres, on every core; T=tests/log runs one folder
make hooks         # installs the pre-commit hook, which runs `make check`
```

`make test` starts Postgres 17 with pgvector and pg_textsearch from `infra/postgres/` in colima
(macOS) or docker; nothing else runs locally: no LiveKit, no gateway. Live tests (`tests/live/`)
run against a deployed box and are skipped without `PINECALL_URL`.

## The rules are tests

`tests/rules/` holds one test per architecture rule, and the pre-commit hook fails on any of
them: empty `__init__.py` files, no pass-through functions, an import graph declared as edges
(`test_05_import_graph.py`; `docs/architecture.md` is the same graph in prose), no duplicated
code, tests mirroring source files one to one, no linter suppressions, ruff on every rule,
pyright strict, no secret-shaped strings, every task with a named owner, no module over 700
lines, no literary names, and private modules (`_name.py`) that stay inside their package. Read `docs/conventions.md` before writing a file: it says which names,
which data model and which error to use.

## A change

- One concern per pull request, with the test that names the behaviour and the page under
  `docs/` that describes it, in the same commit.
- Code, comments and commit messages in English. A comment says what the code cannot: an
  external fact, a trap, a reason. One line.
- A new file is a line in the import graph (`test_05`) if it opens a new edge, and a new entry
  in `tests/rules/allowed.py` if it needs a pyright ignore at a LiveKit seam, with its reason.
- A bug report says what was sent to which door, what came back, and the box's journal line.

## Security

Vulnerabilities go to the address in `SECURITY.md`, never to an issue.
