# runtime-v2 — how to work here

The Pinecall runtime, rewritten: one Python package (`pinecall`), one wheel, the gateway that
answers the doors and the worker that runs the calls, on LiveKit. It runs on Kubernetes,
production's cluster since 2026-10-04. The full history of decisions is in
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
make test [T=tests/log]           every suite on a local Postgres and Redis (colima; `make db`)
make image                        the runtime's image at this commit, built by Cloud Build
make deploy ENV=staging           the chart released on the cluster at that image, then the live suite
make suite ENV=staging            every suite as a Job inside the cluster
make logs ENV=staging             the gateways' and the workers' logs of the last hour
make local                        the runtime on this laptop: Postgres, Redis, LiveKit in docker
make local-gateway / local-worker the gateway and a sandbox worker against it (infra/local)
make tf-plan ENV=staging          what Terraform would change, saved; "No changes." is the norm
make tf-apply ENV=staging         exactly the saved plan, after it was read
vibesmell check                   the hygiene findings; must say "nothing to fix"
```

The runtime runs on Kubernetes (`infra/`, its README): the cloud (cluster, pools, registry,
address, firewall, names, secrets) is Terraform's and the runtime on it is `charts/pinecall`;
nothing cloud-side is made or changed by hand. v1's machines (the box, the cell, the fleet loop,
the lab, v1's Terraform) left the repository on 2026-10-03 and live in `../infra-v1/`.

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

Empty `__init__.py`; no pass-through function; the import graph is a list of edges; nothing written
twice; `TREE.md` is the set of files; tests mirror the source one to one; no suppression (`noqa`,
`type: ignore`) outside `tests/rules/allowed.py`; ruff, pyright strict, deptry;
`docs/protocol/every-door.md` and the gateway's routes agree both ways; no secret-shaped string;
every task has an owner; no module over 700 lines; no literary names (`said`, `held`, `one`…); a
module reads top to bottom; the gateway's private modules within budget; `docs/architecture.md`
carries today's measures; `docs/wire/` describes every field; a migration that contracts (drops,
renames, a `NOT NULL` with no default) names the release that stopped reading what it contracts;
the pool is autocommit, and a block of statements that is not a transaction says why they are
independent.

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

## Production

**Production is the cluster** `pinecall-production` (GKE, `environments/production`, kubectl context
`gke_example-project_us-central1-c_pinecall-production`) since the cutover of 2026-10-04
(`../internal-docs/runtime-v2/CUTOVER-K8S-RUNBOOK.md`): `cloud.pinecall.io` and `sandbox.pinecall.io`
on its Gateway (203.0.113.1, Certificate Manager's certificate), SIP at `sip.pinecall.io` and
`sip.sandbox.pinecall.io` on the core node's static address (203.0.113.10, kubeip). Released with
`make deploy ENV=production TAG=<commit>`; tested against the domain, never a local gateway; local
is for the suites. Postgres is CloudNativePG's, its WAL and nightly base backups in
`pinecall-production-postgres-000000000000`; recordings in `pinecall-box-recordings-000000000000`
(S3, its key in Secret Manager); the vault key and the ops key are the box's, carried over.

The old box, `ssh example-box` (203.0.113.20), is kept for a week as the way back: its
runtime units masked and stopped, its database frozen at the cutover, Prometheus off.
notify and billing left it for the cluster the same day (`infra/README.md`, "Pinecall's own
services"); `ssh example-replica` is its replica. Its files and
Terraform are `../infra-v1/`.

## Secrets and what never gets committed

- Keys come from `~/.pinecall/credentials` into an env var and are never printed, logged,
  committed or pasted. A key minted on the sandbox is said in the reply.
- Never committed: `TREE.md`, `PARITY.md`, plans, notes (they are in `.git/info/exclude`).
  Internal documents live in `../internal-docs/`, never in the repo. This page is committed.
- Before a commit: `git ls-files --others --exclude-standard`, read whole.

## Open, waiting on Bernardo

- The first real phone call on the new box.
- WhatsApp needs a Meta token.
- Publishing the `pinecall` CLI and `@pinecall/room` to npm (their GitHub repos do not exist yet).
