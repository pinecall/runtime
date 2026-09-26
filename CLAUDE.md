# pinecall/runtime

The voice-AI runtime for contact centers, on LiveKit: one distribution, two processes (the
gateway, the worker). Reply to the human in Spanish; code, comments, commit messages and this
file in English. What it is: [ARCHITECTURE.md](ARCHITECTURE.md). How it is deployed and the CLI:
[README.md](README.md). Procedures with traps in them are skills under `.claude/skills/`.

## Workflow

```bash
docker compose -f infra/compose/dev.yml up -d   # livekit · sip · redis · postgres · tei
scripts/bootstrap                               # uv sync (runtime · providers · dev), prek hooks
scripts/format                                  # ruff format, then the fixable lint rules
scripts/lint                                    # ruff · pyright · mypy · deptry · squawk over unlanded migrations — the gate
scripts/test                                    # pytest -m "unit or postgres" + infra/tools/tests + the core's suite, coverage to its floor
uv run pytest -m unit                           # ring 0: no keys, no network, SHUFFLED — three green runs, or nothing
uv run pytest packages/pinecall-runtime/tests/cli/doctor/test_verbs.py    # one file
uv run pinecall-runtime gateway | worker dev | migrate up | doctor
scripts/generate-env-example                    # after touching settings/schema.py; a test fails while it drifts
make deploy                                     # this checkout onto your box (deploy.local.mk); ends with the doctor
```

## Structure

- The repository is a uv workspace: the root holds the tools, the suites' configuration, `infra/`,
  `docs/` and `scripts/`; the code is the two distributions under `packages/`, one lock, one venv
- `packages/pinecall-runtime/` — the distribution `pinecall`: `src/pinecall/`, eighteen packages,
  none of them a process; ARCHITECTURE.md §11 is the import table and `tests/test_isolation.py`
  enforces it; the words they speak are `docs/glossary.md`
- `packages/pinecall-core/` — the distribution `pinecall-core`: `types/`, `extensions/`,
  `errors/`, on the standard library alone, that a policy (`cloud/`) installs without the
  runtime. It imports nothing of the runtime, and `pinecall` is a namespace both install into: no
  `__init__.py` in either `src/pinecall/`. Its suite runs apart (`scripts/test`), on its own
  `pyproject.toml`
  - `types/` the shapes, no IO · `log/` the truth, no framework · `providers/` the only vendor names
  - `auth/` keys, members, sign-in · `orgs/` the tenant's tables · `routes/` numbers and trunks at
    the SFU · `tokens/` the room token and the seat · `whatsapp/` the text channel · `fleet/` the
    workers' heartbeats and the loop over the clouds
  - `extensions/` the points a package beside the runtime plugs policy into — what a new org may
    do — spoken in mechanisms, never plans; the runtime answers them itself until told otherwise
  - `session/` one call, `text/` in the gateway and `voice/` in the worker · `evals/` the rings
  - `memory/` the contact's facts · `knowledge/` the knowledge base · `lookups/` the gateway
    running `recall` and `search` — the three the gateway owns and the worker reaches over HTTP
  - `api/` the gateway's doors, one directory per surface (`scope/ accounts/ agents/ calls/ memory/
    knowledge/ evals/ telephony/ org/ ops/ whatsapp/`) · `worker/` the job · `cli/` the verbs · `db/` the driver's one door, the pool and the numbered SQL (`db/migrations/`)
  - `mail/` the letters and the generic SMTP they leave by: the org's own account, else the box's
  - `settings/` the configuration: `schema.py` every variable once, `vendor_keys.py` the vendors'
    own names, `load_settings()` the one reader · `_version.py` the version, the maintainer's number
- each distribution's `tests/` mirrors its `src/pinecall/` one to one; `test_isolation.py`, `test_layout.py`,
  `test_the_public_surface.py`, `settings/test_example.py`, `test_box_packages.py` are the tree's own rules
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
- A door is a controller: under `api/` a coroutine is a door, a dependency, a stream or one of the
  roles `test_doors_are_controllers.py` names; a verb goes to the domain package it changes.
- `types/` imports nothing of ours; `types/` and `log/` import no framework; a vendor SDK
  outside `providers/` fails the suite; `api/` never imports `worker/`, `worker/` never `api/`.
- Every package another reads from opens with its index: its `__init__.py` imports and lists in
  `__all__` what the rest of the tree uses of it, and a test pins it — `api/` and `cli/` alone have
  none (doors and verbs, read by nobody). The public surface of every package is pinned by a test, and the `pinecall`
  namespace has no root module in either distribution.
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

A store is a port and two adapters: `<port>.py` (the Protocol, its records, `<port>_for`),
`<port>_memory.py`, `<port>_postgres.py` — SQL lives there and in `db/`, nowhere else. One definition per thing — `grep` before writing a constant, a parser, a helper. No dead code and
no code "for later": a symbol with no user outside its file and its test goes in the commit that
notices it. No module-level mutable state — per call, per request, or a contextvar. The library
first: name the livekit-agents / livekit-api / pydantic / FastAPI module that already does it, and
the livekit example `file:line` a session knob comes from. One idea per file, named by the idea.
A stale comment is a bug. A name survives a traceback on its own: a verb and its object
(`mint_key`, `seal_log`), a noun phrase for what it answers (`recording_path`), `is_`/`has_`/`may_`
for a predicate, `build_`/`parse_`/`new_` for a factory; a FastAPI dependency is `get_x`, one that
refuses is `require_x`, a projection to the protocol is `wire_x`. No article, pronoun or bare
participle as a name — the sentence goes in the docstring. Small methods; 150 lines is the norm.
Tests read as sentences. 

## Traps — each one cost an afternoon

- **One runtime.** A laptop runs the same Postgres, migrations and issued keys a box does:
  `docker compose … up -d`, `migrate up`, `pinecall-runtime init --org … --email … --person …`,
  then `pinecall login`. `PINECALL_DEV_KEY` and `~/.pinecall/dev` are gone; a gateway with no
  database verifies nothing, says so at startup, and answers every keyed door 503.
- A native Postgres shadows the container on `127.0.0.1`: run the Postgres ring with
  `DATABASE_URL=postgresql://pinecall:pinecall@[::1]:5432/pinecall` on such a machine.
- **Nothing fixed by hand on a server counts.** A package goes in `PACKAGES`, a secret through
  `make secret`, a class of failure into the doctor; then the box re-converges via `make deploy`.
- **TEI's CPU image has no arm64 build**, so on this Mac the dev stack's `tei` cannot start at
  all and every lookup is skipped. `EMBED_PROVIDER=perplexity` + `PERPLEXITY_API_KEY` embeds
  contextually over HTTP with no container. `doctor`'s `embedder` line says which one is running.
- **An applied migration is NEVER edited.** `schema_migrations.sha256` refuses a checkout whose
  file changed, by name and by both hashes, because every database that ran it has the OLD one.
  The fix for an old migration is a NEW migration; `migrations/applied.sha256` keeps every
  landed file's hash in the tree and the unit suite holds each file to it, so an edit fails CI and not a
  box's startup (2026-09-26: a rename touched nine comments and production did not start). Adding
  one means bumping `db/migrations/migrations.lock` AND appending its line to `applied.sha256` in the
  same commit — the lock is what makes two branches adding `0022`
  conflict in git, and it is the linter's baseline. `scripts/lint-migrations` (squawk) gates only
  what sits above that line. Three rules it will not catch and a review must: never rename a
  column (rename in code, keep the column), drop one in TWO migrations (the code stops using it
  first), and a constraint on a populated table goes in `NOT VALID` then `VALIDATE`.
- **A migration is held to five seconds at startup.** The unit runs `migrate up` before the
  gateway opens, so anything slower is a `.post.sql` — named, never run at startup, applied by a
  person with `migrate up --post`. An index on a big table is always one of those, and one built
  `CONCURRENTLY` opens with `-- pinecall:no-transaction` and holds that ONE statement: the runner
  wraps every other file in a transaction, which is the thing `CONCURRENTLY` cannot run inside.
- **A test that walks `app.routes` can go vacuous on a FastAPI upgrade.** 0.141 stopped
  flattening an included router into it and puts a wrapper there (`original_router`), so
  `test_scopes_at_the_doors` found no `APIRoute` at all and pinned every door's scope over an
  EMPTY list, silently. Any walk of the app unwraps that, and asserts it reached something.
- A key is never printed — not in a commit, a test, a log line, a reply. Compare by sha256.
- `_version.py` is the maintainer's number: a release bumps its last digit, and nothing else
  ever writes it.
- The shell may name a vendor's key differently (`ELEVENLABS_API_KEY`) than the runtime does
  (`ELEVEN_API_KEY`); `.env.example` is the list.

## Commits

A subject line and a body that says why. `scripts/format`, then `scripts/lint` and
`scripts/test` exit 0 before a commit. `CHANGELOG.md` gains a line under `Unreleased` for
anything a user of the package or the box would notice.
