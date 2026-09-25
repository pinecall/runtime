# pinecall

The Pinecall voice-AI runtime: one Python distribution, two processes, on LiveKit.

- `pinecall-runtime gateway` is the control plane: the app protocol over WebSocket, the call
  log over SSE, the tokens, the routes, WhatsApp's webhook, the operator API.
- `pinecall-runtime worker` is the fleet: one livekit-agents worker, one process per call.

The public talks to an agent by web, WhatsApp and telephone. The agent itself is written with
the `pinecall` framework, in the agents repository; this runtime holds the log, the wire, the
tenants, the sessions and the judges, and never the conversation. How the pieces fit — and
exactly where LiveKit ends and this begins — is [ARCHITECTURE.md](ARCHITECTURE.md).

## Install

```bash
pip install pinecall          # or: uv add pinecall
```

That is the whole install on a server: the wheel carries the gateway, the worker, the migrations
and the console, with the widget the gateway serves at `/widget/pinecall-widget.js` — both copied
in by `scripts/console` before a build; a gateway built without them answers those paths `404`
with the sentence that says so. An instance is one world: `PINECALL_WORLD`, production unless it
says `sandbox` — which a sandbox instance does on purpose, with `PINECALL_IDENTITY_URL` naming the
production its people sign in at. The console it serves
at `/` shows that world; the sandbox is a second instance of the same runtime, with its own
database, worker and fleet (`PINECALL_FLEET`), where a developer watches what they are running
(`pinecall console` opens it), and `PINECALL_ELSEWHERE_URL` tells each where the other answers. A laptop that wants to read the code, run the example agent or bring up
the dev stack clones instead — [docs/from-zero.md](docs/from-zero.md) is that walkthrough, every
command in it run in order with the output it returned.

## Five minutes, on a laptop

```
docker compose -f infra/compose/dev.yml up -d      livekit · sip · redis · postgres · tei
scripts/bootstrap                                  uv sync, every extra and tool group
uv run pinecall-runtime migrate up                 the schema; a fresh database seeds the
                                                   default org, and `keys issue` mints its key
uv run pinecall-runtime gateway                    the control plane, on 8080
uv run pinecall-runtime worker dev                 the fleet that answers a call
uv run pinecall-runtime doctor                     every service and key, one line each
```

Then, from an example in the agents repository, `pinecall start` registers the agent and
`pinecall chat` talks to it. `pinecall knowledge push ./knowledge/docs --base clinica-norte`
puts the agent's files where its `search` tool reads from, and `pinecall memory
<contact>` prints what a caller's calls taught the agent (`memory forget` erases it). Both are
tables in Postgres, and the vectors are whichever embedder `EMBED_PROVIDER` names — with no
embedder answering, a lookup finds nothing and the push says so.

```
EMBED_PROVIDER=tei                       who embeds: tei · perplexity · openrouter
EMBED_MODEL=                             unset: BAAI/bge-m3 · pplx-embed-context-v1-0.6b ·
                                         perplexity/pplx-embed-v1-0.6b, by provider
EMBED_BASE_URL=                          unset: the provider's own door, and TEI_URL for TEI
PERPLEXITY_API_KEY=                      the two hosted ones. Perplexity's default model is
OPENROUTER_API_KEY=                      CONTEXTUAL: a chunk is embedded seeing its neighbours
```

TEI's CPU image has no arm64 build, so on an Apple Silicon laptop TEI cannot run at all and
`EMBED_PROVIDER=perplexity` with `PERPLEXITY_API_KEY` is how that machine retrieves — no
container, and a better base than bge-m3 gives. A vector is comparable only to vectors of the same
model, so a box that changes embedder refuses, at search, every knowledge base the old one pushed
(`base <name> was pushed with <old>; this gateway embeds with <new>: push it again`) until each
project pushes it again (`pinecall knowledge push`); a contact's facts are recalled by their words
only, since the dense branch of a recall filters on `model`. The *retrieval* decision page in the
maintainer's notebook has the measurements. Development happens from the checkout, with `uv`:

```
scripts/format        ruff format, then the fixable lint rules
scripts/lint          ruff, pyright strict, mypy strict
scripts/test          pytest -m "unit or postgres": no keys, no network
```

The wire is `pinecall-protocol`, generated in the protocol repository and resolved here as the
checkout beside this one (`../protocol/python`, see `pyproject.toml`); the wheel requires
`pinecall-protocol>=0.3,<0.4`.

## Deploy: a box of your own

Two ways to run this on a server, and the first is the one we run ourselves.

### 1. A checkout and `make deploy`

The box is **declared**: `infra/box/` holds cloud-init, the systemd units, the Quadlet containers
(LiveKit, SIP, Redis, Postgres), the Caddyfile, the firewall and a Makefile that is the manifest.
It runs on any Linux with systemd ≥ 254 and podman ≥ 4.9, on any provider — the box holds no
credential for the repository and is never told which cloud it is on.

```
1. the machine     hand your provider infra/box/cloud-init.yaml as user-data, with the three
                   YOURS lines filled: your ssh public key, the domain, the SFU's public URL
2. which box       runtime/deploy.local.mk, git-ignored:
                     BOX     = deploy@203.0.113.7
                     DOMAIN  = box.example.com
                     SSH_KEY = ~/.ssh/id_ed25519       # optional
3. make deploy     from this directory. `scripts/console` (the agents repo's console bundled and
                   copied in as package data, and the widget beside it; needs pnpm,
                   ../agents and ../widget) · rsync the checkout · `make -C infra/box install` (the
                   packages, every unit and container file, the fence) · `uv sync --frozen` as the
                   service user · `make -C infra/box converge` (every instance whole, or the
                   deploy stops; a Caddy site each; the role's units) · restart, instance by
                   instance, gateway first and the worker once the gateway answers through
                   Caddy — live calls go on through both · the doctor, per instance, last
4. your key        minted on the box on first start, encrypted, printed nowhere — read it once:
                   sudo systemd-creds decrypt --name=PINECALL_OPERATOR_KEY \
                        /etc/credstore.encrypted/PINECALL_OPERATOR_KEY -
                   no such file? `sudo systemctl start pinecall-operator-key` mints one and
                   does nothing at all while the credstore already has one
```

**A box runs instances.** The first deploy makes it one, `production`. A second — the sandbox
where agents are written, a staging — is its own gateway, worker, database, fleet and keys on the
same media plane, declared with one verb and a line of `/etc/pinecall/box.env`, with nothing about
it written in a unit file ([infra/box/README.md](infra/box/README.md), "An instance"):

```
make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com \
              IDENTITY=https://box.example.com ELSEWHERE=https://box.example.com
                   then PINECALL_INSTANCES="production sandbox" in box.env, and make deploy
```

Every secret on the box is an encrypted systemd credential; there is no `.env` there. The vendors'
keys are yours to bring, and a deploy ends by **knocking at every vendor with the key the box
holds** — a dead one fails the deploy with its name on the screen, never a caller's first call.

```
printf '%s' "$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY     one secret, on stdin
make restart                                                            a credential is read at start
make doctor [INSTANCE=sandbox] [MAIL_TO=you@example.com]                what the deploy runs last; a test letter
make providers [DOES=tts]                                               every vendor, and what each wants
make status · make logs UNIT=worker@production · make ssh
```

**The first org, and who operates the box.** A box answers nobody until an org exists, and there
is no page to make one in: a fresh box has no person to sign in as. It is made on the box, by the
CLI, which is the only thing that can speak to a runtime nobody has used yet:

```
pinecall-runtime init --org clinica-norte --email ana@clinica.example --person "Ana Ruiz"
```

That makes the org, makes Ana its first admin, makes her an **operator of this box**, and prints
the link she signs in with, once. From then on everything is the console at `https://<your
domain>`: Ana's own screens are her org's, and the **Box** group in her sidebar — Organizations,
Fleet, Routes, Box usage, Box settings — is the operator's, which `/v1/ops/*` opens for her
because the box made her one. More orgs are made there, or with `pinecall-runtime orgs add`; more
operators with `pinecall-runtime orgs operator <org> <email>`. There is no separate operator page
and no ops key typed into a browser.

**A second box.** One machine is `PINECALL_ROLE=all`. To grow, the machine you have becomes the
**hub** (`PINECALL_ROLE=hub` in its `/etc/pinecall/box.env`: gateway and media plane, no worker)
and each new machine is a **worker** (`PINECALL_ROLE=worker` and `LIVEKIT_URL` pointing at the hub
in its box.env; the hub's instance file with `PINECALL_GATEWAY_URL` pointing at the hub and
`PINECALL_MAX_JOBS` measured on it). The hub copies a worker its credentials, then the worker is
deployed like any box:

```
make worker-secrets WORKER=deploy@203.0.113.9
make deploy BOX=deploy@203.0.113.9 DOMAIN=box.example.com
```

The whole of it — the fence, the roles, the slots, the four traps a real box has, wiring a
number — is [infra/box/README.md](infra/box/README.md).

### 2. The package, and a machine you build yourself

`pinecall` is a plain Python distribution with one entrypoint, for whoever already has a way to
run processes (Kubernetes, Nomad, Ansible) and wants ours out of the picture:

```
pip install pinecall                       the gateway
pip install pinecall[runtime]              the gateway and the worker, on the five tuned vendors
pip install pinecall[runtime,providers]    and the other forty livekit ships a plugin for
pinecall-runtime migrate up                then gateway, and worker start, two long-running processes
```

`providers` is thin HTTP clients and is what a box installs; `providers-big` is the four that
bring a whole SDK with them — AWS, Azure Speech, Google Cloud, Speechmatics — and is a decision,
never a default. `pinecall-runtime providers` prints the whole table with what each vendor still
wants; a vendor whose plugin is missing is refused by name, with the one command that installs it.

The two processes read the environment `.env.example` documents — the same names on a box, where
they arrive as systemd credentials — and need a LiveKit server, a Postgres 17 with pgvector and
`pg_textsearch`, and Redis beside them. What runs them, restarts them and fences them is yours.
(Not on PyPI yet: the version and the tag are a person's call.)

## The CLI

`pinecall-runtime <group> <verb>`. Twelve groups; every verb answers `--help`.

**The two processes**

| verb | what |
|---|---|
| `gateway [--host] [--port] [--reload]` | the control plane, on the loopback host and port of its own `PINECALL_GATEWAY_URL` (8080 unless the instance says) |
| `worker dev` · `worker start` | the fleet, with livekit's own flags passed through; each heartbeats to the gateway |
| `worker overflow` | the hub's one worker that is never full: a sentence and a call back when every other is |
| `worker talk` | one call served by THIS terminal — a `@tool` breakpoint lands where you typed |
| `worker download-files` | livekit's model files, ahead of the first call |

**The box's operator**

| verb | what |
|---|---|
| `init --email --person [--org]` | the first org and its first admin, made an operator of this box, on a runtime nobody has used yet |
| `migrate up [--post]` · `migrate status` · `migrate plan` | the schema, numbered SQL, applied in order. `up` says which database first, takes an advisory lock, and holds every migration to 5 s; a `.post.sql` is named and never run at startup, so `--post` is how an index on a big table gets built. `status` asks the database, `plan` touches nothing |
| `doctor [--mail-to <address>]` | its first line names the .env read, the instance's world and its fleet; then keys present · keys answer · livekit · postgres · embedder · mail · lk — one line each, and what is down first; the mail line says which mailbox — stored by the operator at `PUT /v1/ops/mail`, or the environment's; `--mail-to` posts one test letter through that same mailbox and says what the server said |
| `box secrets [--instance <name>]` | every secret a box makes for itself, once — or one instance's own three; run twice rotates nothing |
| `box secret <NAME> [--instance <name>]` | one secret you bring, from stdin, replaced in place — in the box's store, or one instance's |
| `box instance <name> --world --domain [--port --fleet --identity --elsewhere --max-jobs --idle-processes --force]` | one more instance of the runtime on this box: its env file, every variable written, the next free hundred for its ports; never over a file unasked |
| `box database` | what `pinecall-db@<name>` runs: the instance's database, its role and its walls, made once |
| `fleet list · cordon · uncordon · loop` | the workers as the hub hears them, the graceful shrink, and the loop that keeps `busy` at the target over any cloud |
| `providers [--does llm\|stt\|tts]` | every vendor this build runs, as a table — the last line counts them: what each does, whether this box has its plugin and its key, the variable a key goes under, and every other word the vendor answers to. Reads the catalog and this process's settings; asks nothing of anybody, so it answers on a box that is down. Never a key |
| `orgs list · add · invite · operator · remove-member · move · rm · quota · dialling · provider-key · sso` | the tenants: a person invited (no link for somebody who already has a password on this box: they are seated at once), a person made an operator of the box, a person removed from an org for good (keys revoked, the seat free; refused for its last active admin), an agent moved to the org it belongs to, their quotas (`--minutes --messages --agents --concurrent-calls --memory-facts --knowledge-chunks --numbers --seats`, the whole set at once; a flag left out is no limit and `0` refuses everything; `--budget-eur` beside them is shown, never refused), what it may dial out (`--dial-anywhere --per-minute --per-day --max-duration-s`, the whole set; a guard left out goes back to the code's default), the vendor keys an org brings, which identity provider an org signs in with (`orgs sso <org>`, and `--off` the break-glass that lets a password open it again while that provider is down) |
| `keys issue · list · revoke` | an org's API keys, the operator's way in: printed once, listed by fingerprint, revoked by UPDATE. `issue --env production\|sandbox --scope … --subject … --name …`: which world the key opens (`pc_live_…`, `pc_test_…`), what it may do there (left out, every scope but `fleet`), whose it is — with `--subject` a person's (`pc_…`), whose world each request names. A tenant makes its servers' tokens itself, in the console's Tokens screen |
| `routes list · add · rm · seed` | which agent answers a number, from the next call; `--env` says in which world |

**Reading a call**

| verb | what |
|---|---|
| `sessions list` | the calls, newest first |
| `sessions show <call>` | one call, entry by entry, every metric whole |
| `sessions tail <call>` | follow a call as it happens |
| `sessions recording <call>` | where its audio was written; a written (chat) call kept none, and it says so |

`sessions` reads Postgres, not HTTP: it is the operator's door, and works with the gateway down.
A text call from a terminal is the tenant's `pinecall chat`: the tenant's own commands — `pinecall
run`, `chat`, `test`, `simulate`, `eval`, `ui` — are the agents repository's, and speak to this
gateway with the org's key.

## Where the rest is

| | |
|---|---|
| `ARCHITECTURE.md` | LiveKit's half and ours, the entities, the wire, the two processes, the path of a call, the log, the rings |
| `docs/scaling.md` | one server to a fleet: the three planes, seats not CPU, the heartbeats, cordon, the loop, overflow at the door |
| `docs/the-fleet.html` | the same fleet on one page for a reader: every way to run it, every verb and door, what was measured, what is left and why |
| `docs/a-box-in-production.md` | **a box in production**: one machine with a domain, from an operating system and nothing else — written by deleting a real box's software and putting it back |
| `docs/from-zero.md` | **start here**: a runtime of your own and an agent answering, every command run and every output what came back |
| `docs/the-runtime-cli.md` | every `pinecall-runtime` verb: what it takes and what it speaks to |
| `docs/the-environment.md` | every variable the two processes read, and the two walkthroughs: a laptop from nothing, a box from nothing |
| `docs/multi-tenancy.md` | orgs, keys and tenants: what a key IS, why a laptop runs the same runtime a box does, and how a tenant is given one |
| `docs/protocol/gateway-api.md` | every door a tenant's own code may knock at, with an app in thirty lines |
| `docs/decisions/` | why each module is the way it is, one page per module — the maintainer's notebook, git-ignored: a clone has the names and not the pages |
| `docs/protocol/` | the operator API and the token door, as public contracts |
| `infra/box/README.md` | the box: standing one up, roles, slots, secrets, the fence, wiring a number |
| `infra/README.md` | the dev stack |
| `.env.example` | every variable both processes read, rendered from the settings class |

## License

[Apache-2.0](LICENSE). Use it, change it, run it in production, sell what you build with it —
commercially or not, on your own box or somebody else's. The licence carries an explicit patent
grant, which is why it is the one this stack uses (LiveKit's is the same). There is no NOTICE
file, so nothing has to be reproduced downstream beyond the licence itself, and there is no CLA:
a patch is yours and stays under the same terms.

`pinecall` on PyPI, and the box under `infra/box/` — the same tree either way. Nothing in it
is a hosted-only path: everything needed to self-host is in this repository.
