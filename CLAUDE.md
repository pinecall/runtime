# runtime-v2 — how to work here

The Pinecall runtime, rewritten: one Python package (`pinecall`), one wheel, the gateway that
answers the doors and the worker that runs the calls, on LiveKit. It runs on Kubernetes (`infra/`).

Code, comments, commits and pages are in English. A commit subject is a plain sentence saying
what is now true (`git log --oneline` shows the style).

## Commands

```
uv sync                           the venv and every dev tool
make check                        the rules (tests/rules/) and the suites with no database
make test [T=tests/log]           every suite on a local Postgres and Redis (colima; `make db`)
make image                        the runtime's image at this commit
make deploy ENV=<env>             the chart released on the cluster at that image, then the live suite
make suite ENV=<env>              every suite as a Job inside the cluster
make logs ENV=<env>               the gateways' and the workers' logs of the last hour
make local                        the runtime on this laptop: Postgres, Redis, LiveKit in docker
make local-gateway / local-worker the gateway and a sandbox worker against it (infra/local)
make tf-plan ENV=<env>            what Terraform would change, saved; "No changes." is the norm
make tf-apply ENV=<env>           exactly the saved plan, after it was read
vibesmell check                   the hygiene findings; must say "nothing to fix"
```

The runtime runs on Kubernetes (`infra/`, its README): the cloud (cluster, pools, registry,
address, firewall, names, secrets) is Terraform's and the runtime on it is `charts/pinecall`;
nothing cloud-side is made or changed by hand. An operator's own values and Terraform variables
live outside the repo, in the directory `PINECALL_OPS` names (see `infra/README.md`); the repo
carries none of any deployment's.

Both suites run before a change is called green. No test is skipped or deleted to pass.

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

Empty `__init__.py`; no pass-through function; the import graph is a list of edges; nothing written
twice; `TREE.md` is the set of files; tests mirror the source one to one; no suppression (`noqa`,
`type: ignore`) outside `tests/rules/allowed.py`; ruff, pyright strict, deptry;
`docs/protocol/every-door.md` and the gateway's routes agree both ways; no secret-shaped string;
every task has an owner; no module over 700 lines; no literary names (`said`, `held`, `one`…); a
module reads top to bottom; the gateway's private modules within budget; `docs/architecture.md`
carries today's measures; `docs/wire/` describes every field; a migration that contracts (drops,
renames, a `NOT NULL` with no default) names the release that stopped reading what it contracts;
the pool is autocommit, and a block of statements that is not a transaction says why they are
independent; a worker's pod is handed no database, no vault key and no operator's key.

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

- **One gateway, one database, one name, two worlds.** The world of a request is its key's (a
  server's token opens the world it was made in) or, for a person, the `pinecall-env` header's,
  the sandbox when it names none; never the name the request came in by. The console is
  production's at `/` and the sandbox's at `/sandbox/…`. Each world has its own worker fleet.
- **No vendor is named in code.** Every installed livekit plugin is a vendor; defaults, box keys,
  what the box lends and the prices are the providers row in Postgres, edited from the console.
- **The wire is ours** (`pinecall/wire/`), pinned to the TypeScript and Ruby SDKs by
  `tests/wire/golden/`. Money is US dollars everywhere.
- **Lexicon and personas are the agent's**; **judges** are the fixed panel (consent, grounded,
  promises, persona) plus the org's own and the agent's own. The judge model and its per-call
  ceiling are the providers row's (`judge.ceiling_usd`, applied).
- `PINECALL_VAULT_KEY` is required: it seals every key and secret the box holds.
- An applied migration is never edited; the fix is a new one.

## Secrets and what never gets committed

- Keys come from `~/.pinecall/credentials` into an env var and are never printed, logged,
  committed or pasted.
- Never committed: `TREE.md`, `PARITY.md`, plans, notes (they are in `.git/info/exclude`), and
  no operator's deployment (values, Terraform variables, addresses, buckets). This page is
  committed.
- Before a commit: `git ls-files --others --exclude-standard`, read whole.
