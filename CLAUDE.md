# pinecall/runtime — working agreement

The voice-AI runtime for contact centers, on LiveKit. Reply to the human in Spanish; code,
comments, commit messages and this file in English.

**Thesis.** An agent is an object, written with the framework in the agents repository. This
runtime owns the log, the wire, the tenants, the sessions and the judges — never the
conversation. The log is the truth.

## The tree

`README.md` is the map: fourteen directories under `src/pinecall/`, none of them a process.
`tests/` mirrors `src/pinecall/` one to one. `docs/decisions/<module>.md` holds the why.

## Invariants the tests enforce

- `tests/test_isolation.py` is the import table. `types/` imports nothing of ours; `types/` and
  `log/` import no framework; only `providers/` names a vendor; `api/` never imports `worker/`
  and `worker/` never imports `api/` — they meet over HTTP.
- No `.py` at the repo root. No tracked file over 400 lines. Every module opens with a one-line
  docstring. No two modules in one directory one letter apart.
- The public surface of the root and of every package with an `__all__` is pinned by a test.
- `.env.example` is generated from `_settings.py`; a test fails when the two drift.
- Unit tests run on dead-sentinel keys: everything constructs, a real call dies in seconds.
  `pytest -m unit` is shuffled; three of three green is a gate and not luck.
- The golden log in `pinecall_protocol.fixtures` reduces to the golden state here and in TS.

## Hygiene — what every review greps for

One definition per thing. No dead code, no code "for later". No module-level mutable state. The
library first: before writing what livekit-agents, livekit-api, pydantic or FastAPI already do,
name the module that does it. One idea per file, named by the idea. A stale comment is a bug.

## Code style

Files open with a one-line docstring, then imports, then public methods, then private ones. A
short comment above any method whose name does not say everything — why, never what. Names are
sentences. Small methods, small files: 400 lines is the ceiling, 150 the norm. Tests read as
sentences. If it would not have shipped in Rails 2.3, do not write it.

## Commits, versions

`Bernardo Castro <me@bernardocastro.dev>`, no `Co-Authored-By`, no generated-with trailers.
Versions and tags are the human's call — never pick a number, never tag.

## Commands

```
docker compose -f infra/compose/dev.yml up -d      the dev stack
scripts/bootstrap · scripts/format · scripts/lint · scripts/test
uv run pinecall-runtime gateway · worker dev · migrate up · doctor [--bench]
uv run pytest -m unit                              ring 0, no keys
```

A native Postgres shadows the container on 127.0.0.1: the Postgres ring runs with
`DATABASE_URL=postgresql://pinecall:pinecall@[::1]:5432/pinecall` on such a machine
(`infra/README.md`). `unset PINECALL_API_KEY` before anything opens a socket on a dev key.
