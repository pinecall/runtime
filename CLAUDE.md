# pinecall/runtime

The voice-AI runtime for contact centers, on LiveKit: one distribution, two processes (the
gateway, the worker). Reply to the human in Spanish; code, comments, commit messages and this
file in English. What it is: [ARCHITECTURE.md](ARCHITECTURE.md). How it is deployed and the CLI:
[README.md](README.md). Procedures with traps in them are skills under `.claude/skills/`.

## Workflow

```bash
docker compose -f infra/compose/dev.yml up -d   # livekit · sip · redis · postgres · tei
scripts/bootstrap                               # uv sync, every extra and tool group
scripts/format                                  # ruff format, then the fixable lint rules
scripts/lint                                    # ruff · pyright strict (src, infra/tools) · mypy strict — the gate
scripts/test                                    # pytest -m "unit or postgres", plus infra/tools/tests
uv run pytest -m unit                           # ring 0: no keys, no network, SHUFFLED — three green runs, or nothing
uv run pytest tests/cli/doctor/test_verbs.py    # one file
uv run pinecall-runtime gateway | worker dev | migrate up | doctor
scripts/generate-env-example                    # after touching _settings.py; a test fails while it drifts
make deploy                                     # this checkout onto your box (deploy.local.mk); ends with the doctor
```

## Structure

- `src/pinecall/` — seventeen packages, none of them a process; ARCHITECTURE.md §11 is the import
  table and `tests/test_isolation.py` enforces it
  - `types/` the shapes, no IO · `log/` the truth, no framework · `providers/` the only vendor names
  - `session/` one call, `text/` in the gateway and `voice/` in the worker · `evals/` the rings
  - `memory/` the contact's facts · `knowledge/` the knowledge base · `lookups/` the gateway
    running `recall` and `search` — the three the gateway owns and the worker reaches over HTTP
  - `api/` the gateway's doors · `worker/` the job · `cli/` the verbs · `migrations/` numbered SQL
  - `_settings.py` every variable, once · `_version.py` `0.0.0` until a person says otherwise
- `tests/` mirrors `src/pinecall/` one to one; `test_isolation.py`, `test_layout.py`,
  `test_the_public_surface.py`, `test_env_example.py`, `test_box_packages.py` are the tree's own rules
- `infra/box/` the declared box (cloud-init, units, Quadlets, the fence, the manifest Makefile);
  `infra/compose/` the dev stack; the root `Makefile` is the deploy
- `docs/protocol/` public contracts · `docs/decisions/` the maintainer's notebook, **git-ignored**:
  a clone has no such directory, and a comment naming a page there points at a note

## Docs are part of the change

**A change lands with the page that describes it, in the same commit.** Not "later", not a TODO:
a page that describes what the tree no longer does is worse than no page, because somebody trusts
it. What to edit, by what you touched:

| you changed | edit |
|---|---|
| a package, a module, an entity or its fields, a line of the import table, the path a call takes | `ARCHITECTURE.md` — the section, and any table that lists the module |
| a CLI verb, a flag, a deploy step, a variable | `README.md` (the CLI and the deploy), `.env.example` via `scripts/generate-env-example` |
| a public contract — the operator API, the token door, a projection | `docs/protocol/`, which is the contract itself and not a description of one |
| a procedure with a trap in it — a NEVER, an order of steps, a refusal | the skill under `.claude/skills/` |
| anything a user of the package or the box would notice | `CHANGELOG.md`, one line under `Unreleased` |

Before committing a rename or a removal:
`grep -rn '<the old name>' ARCHITECTURE.md README.md CLAUDE.md docs .claude/skills` — a symbol
that moved is a stale sentence somewhere. When a doc and the code disagree, the code is what
happened and the doc is the bug.

## Rules the tests enforce

- No `.py` at the root. No tracked file over 400 lines. Every module opens with a one-line
  docstring. No two modules in one directory one letter apart.
- `types/` imports nothing of ours; `types/` and `log/` import no framework; a vendor SDK
  outside `providers/` fails the suite; `api/` never imports `worker/`, `worker/` never `api/`.
- The public surface of the root and of every package with an `__all__` is pinned by a test.
- The prompt is a list of named blocks in two regions, in this order: static blocks (cached) ·
  append-only history · the dynamic region, which is the view and nothing else. Never reorder.
  What a lookup found reaches the model as a `tool_result`, never as part of the prompt:
  `docs/security/prompt-injection.md` is the contract, and it is public.
- A lookup is run by the gateway, never by the app: the worker asks over HTTP
  (`POST /v1/calls/{call}/lookup`, `/remember`), the text session asks `lookups/` in-process, and
  `worker/` imports none of `memory/`, `knowledge/`, `lookups/`.
- Unit tests run on dead-sentinel keys (`tests/conftest.py`): everything constructs, a real call
  dies in seconds. The same golden log reduces to the same state here and in TypeScript.

## What a review comes back to

One definition per thing — `grep` before writing a constant, a parser, a helper. No dead code and
no code "for later": a symbol with no user outside its file and its test goes in the commit that
notices it. No module-level mutable state — per call, per request, or a contextvar. The library
first: name the livekit-agents / livekit-api / pydantic / FastAPI module that already does it, and
the livekit example `file:line` a session knob comes from. One idea per file, named by the idea.
A stale comment is a bug. Names are sentences; small methods; 150 lines is the norm. Tests read
as sentences. 

## Traps — each one cost an afternoon

- A gateway on a dev key honours that key and no other, and `PINECALL_API_KEY` in the shell is
  then ignored, out loud. A bare `403` from any door: `env | grep PINECALL`, then `unset`.
- A native Postgres shadows the container on `127.0.0.1`: run the Postgres ring with
  `DATABASE_URL=postgresql://pinecall:pinecall@[::1]:5432/pinecall` on such a machine.
- **Nothing fixed by hand on a server counts.** A package goes in `PACKAGES`, a secret through
  `make secret`, a class of failure into the doctor; then the box re-converges via `make deploy`.
- **TEI's CPU image has no arm64 build**, so on this Mac the dev stack's `tei` cannot start at
  all and every lookup is skipped. `EMBED_PROVIDER=perplexity` + `PERPLEXITY_API_KEY` embeds
  contextually over HTTP with no container. `doctor`'s `embedder` line says which one is running.
- A key is never printed — not in a commit, a test, a log line, a reply. Compare by sha256.
- Versions and tags are the human's: never pick a number, never tag. `_version.py` stays `0.0.0`.
- The shell may name a vendor's key differently (`ELEVENLABS_API_KEY`) than the runtime does
  (`ELEVEN_API_KEY`); `.env.example` is the list.

## Commits

`Bernardo Castro <me@bernardocastro.dev>`, a subject line and a body that says why, no
`Co-Authored-By`, no generated-with trailers. `scripts/format`, then `scripts/lint` and
`scripts/test` exit 0 before a commit. `CHANGELOG.md` gains a line under `Unreleased` for
anything a user of the package or the box would notice.
