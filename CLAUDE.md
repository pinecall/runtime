# runtime-v2 — how to work here

The Pinecall runtime, rewritten: one Python package (`pinecall`), one wheel, the gateway that
answers the doors and the worker that runs the calls, on LiveKit. It runs the new box today and
replaces `../runtime` (v1) at the cutover. The full history of decisions is in
`../internal-docs/runtime-v2/SESSION-LOG.md`; read it only when a question is not answered here.

## Talking to Bernardo

- Reply in Spanish, short: what was done, what was verified (numbers), what he must decide.
- Code, comments, commits and pages in English. Author `Bernardo Castro <me@bernardocastro.dev>`,
  no `Co-Authored-By`, no "Generated with".
- Commit, push and deploy when he asks. Never decide a product question alone (limits, defaults,
  security, what a tenant sees): ask in one line, with the options and a recommendation.
- A question explains what is behind it and, when a tenant is involved, shows what they type or see.

## Commands

```
uv sync                           the venv and every dev tool
make check                        the rules (tests/rules/) and the suites with no database
make test [T=tests/log]           every suite on a local Postgres (colima; `make db` starts it)
make deploy BOX=example-box   console built in, wheel on the box, migrations, live tests
make logs BOX=example-box     the journal of the three units, whole
vibesmell check                   the hygiene findings; must say "nothing to fix"
```

Both suites run before a reply says green. No test is skipped or deleted to pass.

## The map

`docs/architecture.md` is the map (every folder, what it holds, what it may import), and
`TREE.md` lists every file: a new file is a line there first. In short, bottom up:
`domain` (shapes, errors) · `wire` (every frame, event, command, REST body) · `postgres` ·
`process` (settings, box rows) · `log` (the call's log, the reducer, the facts) · `fleet` ·
`providers` (any livekit plugin, the providers row, prices) · `tenancy` (orgs, people, keys,
vault, lexicon, personas, judges) · `retrieval` (knowledge, memory) · `session` (one livekit
session for voice and text) · `channels` (telephony, WhatsApp, rooms) · `evals` · `worker` ·
`gateway` (`api/` one module per family of doors) · `cli`.

## The rules the machine enforces (`tests/rules/`, run by the pre-commit hook)

Empty `__init__.py`; no pass-through function; the import graph is a list of edges; nothing
written twice; `TREE.md` is the set of files; tests mirror the source one to one; no suppression
(`noqa`, `type: ignore`) outside `tests/rules/allowed.py`; ruff, pyright strict, deptry; every
door of v1 answers (or is in `GONE` with its reason); no secret-shaped string; every task has an
owner; no module over 700 lines; no literary names (`said`, `held`, `one`…); a module reads top
to bottom; the gateway's private modules within budget; `docs/architecture.md` carries today's
measures; `docs/wire/` describes every field.

## How a file is written (`docs/conventions.md` is the long version)

- Three hops from a door to its effect: door → one function of ours → the library.
- A class only when there is state that outlives a call; otherwise functions. No inheritance of
  our own, no decorators, no registries, no `utils`/`helpers`/`manager`.
- Five parameters at most; no boolean that picks a behaviour; return early.
- Names say what a thing is: `OpenCallRequest`, `list_personas`, `entry`, `is_sealed`.
- One-line docstrings; a comment says only what the code cannot (a fact, a trap, a reason).
- Tests are real over mocked: Postgres is real, only livekit and vendors are faked
  (`tests/fakes/`). A change lands with its test and the page that describes it, in one commit.

## Decisions that stand

- **One gateway, one database, two worlds.** The world is the name a request comes in by:
  `PINECALL_DOMAIN` is production's, `PINECALL_SANDBOX_DOMAIN` the sandbox's; a `pinecall-env`
  header may only agree. Each world has its own worker fleet.
- **No vendor is named in code.** Every installed livekit plugin is a vendor; defaults, box keys,
  what the box lends and the prices are the providers row in Postgres, edited from the console.
- **The wire is ours** (`pinecall/wire/`), pinned to the TypeScript and Ruby SDKs by
  `tests/wire/golden/`. Money is US dollars everywhere.
- **Lexicon and personas are the agent's**; **judges** are the fixed panel (consent, grounded,
  promises, persona) plus the org's own and the agent's own. The judge model and its per-call
  ceiling are the providers row's (`judge.ceiling_usd`, applied).
- `PINECALL_VAULT_KEY` is required: it seals every key and secret the box holds.
- An applied migration is never edited; the fix is a new one.

## The box

`ssh example-box` (GCP, 203.0.113.20) is **production**: `box.pinecall.io` is its production
name and `sandbox.pinecall.io` its sandbox name, one gateway, since the cutover of 2026-09-29
(`../internal-docs/runtime-v2/CUTOVER-PLAN.md`; the old box, `pinecall-v2-box`, 203.0.113.21, runs
v1 stopped and the tenant apps under `/opt/pinecall/apps`). Everything is tested against the box,
never a local gateway; local is for the suites. `pinecall-notify` runs
beside the runtime (it pushes calls of both worlds to phones and browsers).

## Secrets and what never gets committed

- Keys come from `~/.pinecall/credentials` into an env var and are never printed, logged,
  committed or pasted. A key minted on the sandbox is said in the reply.
- Never committed: `TREE.md`, `PARITY.md`, plans, notes (they are in `.git/info/exclude`).
  Internal documents live in `../internal-docs/`, never in the repo. This page is committed.
- Before a commit: `git ls-files --others --exclude-standard`, read whole.

## Open, waiting on Bernardo

- The first real phone call on the new box, and `pinecall` 0.9.9 published (the apps run it from a tarball).
- Android pushes: the new VM needs the `pinecall-fleet` service account (a VM stop).
- `notify.pinecall.io` DNS to the new box; WhatsApp needs a Meta token.
- Publishing the `pinecall` CLI and `@pinecall/room` to npm (their GitHub repos do not exist yet).
