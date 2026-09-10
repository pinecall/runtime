# pinecall

The Pinecall voice-AI runtime: one Python distribution, two processes, on LiveKit.

- `pinecall-runtime gateway` is the control plane: the app protocol over WebSocket, the call
  log over SSE, the tokens, the routes, WhatsApp's webhook, the operator API.
- `pinecall-runtime worker` is the fleet: one livekit-agents worker, one process per call.

The public talks to an agent by web, WhatsApp and telephone. The agent itself is written with
the `pinecall` framework, in the agents repository; this runtime holds the log, the wire, the
tenants, the sessions and the judges, and never the conversation. How the pieces fit — and
exactly where LiveKit ends and this begins — is [ARCHITECTURE.md](ARCHITECTURE.md).

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

Then, from an example in the agents repository, `pinecall run` registers the agent and
`pinecall chat` talks to it. `pinecall knowledge push ./knowledge/docs --base clinica-norte`
puts the agent's files where its `<!-- retrieved -->` marker reads from, and `pinecall memory
<contact>` prints what a caller's calls taught the agent (`memory forget` erases it). Both are
tables in Postgres, and the vectors are whichever embedder `EMBED_PROVIDER` names — on a dev key,
with no database, a fill is empty and the push says so.

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
container, and a better base than bge-m3 gives. `docs/decisions/retrieval.md` says why, and what
`push it again` means when a box changes embedder. Development happens from the checkout, with `uv`:

```
scripts/format        ruff format, then the fixable lint rules
scripts/lint          ruff, pyright strict, mypy strict
scripts/test          pytest -m "unit or postgres": no keys, no network
```

The wire is `pinecall-protocol`, generated in the protocol repository and resolved here as the
checkout beside this one (`../protocol/python`, see `pyproject.toml`).

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
3. make deploy     from this directory. rsync the checkout · `make -C infra/box install` (the
                   packages, every unit and container file, the fence, the role) · `uv sync
                   --frozen` as the service user · restart, gateway first and the worker once
                   the gateway answers through Caddy · the doctor, last
4. your key        minted on the box on first start, encrypted, printed nowhere — read it once:
                   sudo systemd-creds decrypt --name=PINECALL_OPERATOR_KEY \
                        /etc/credstore.encrypted/PINECALL_OPERATOR_KEY -
```

Every secret on the box is an encrypted systemd credential; there is no `.env` there. The vendors'
keys are yours to bring, and a deploy ends by **knocking at every vendor with the key the box
holds** — a dead one fails the deploy with its name on the screen, never a caller's first call.

```
printf '%s' "$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY     one secret, on stdin
make restart                                                            a credential is read at start
make doctor                                                             what the deploy runs last
make status · make logs UNIT=worker · make ssh
```

**A second box.** One machine is `PINECALL_ROLE=all`. To grow, the machine you have becomes the
**hub** (`PINECALL_ROLE=hub` in its `/etc/pinecall/box.env`: gateway and media plane, no worker)
and each new machine is a **worker** (`PINECALL_ROLE=worker`, `LIVEKIT_URL` and
`PINECALL_GATEWAY_URL` pointing at the hub, `PINECALL_MAX_JOBS` measured on it). The hub
copies a worker its credentials, then the worker is deployed like any box:

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
pip install pinecall              the gateway
pip install pinecall[runtime]     the gateway and the worker (livekit-agents and its plugins)
pinecall-runtime migrate up       then gateway, and worker start, as two long-running processes
```

The two processes read the environment `.env.example` documents — the same names on a box, where
they arrive as systemd credentials — and need a LiveKit server, a Postgres 17 with pgvector and
`pg_textsearch`, and Redis beside them. What runs them, restarts them and fences them is yours.
(Not on PyPI yet: the version and the tag are a person's call.)

## The CLI

`pinecall-runtime <group> <verb>`. Ten groups; every verb answers `--help`.

**The two processes**

| verb | what |
|---|---|
| `gateway [--host] [--port] [--reload]` | the control plane, on 8080 |
| `worker dev` · `worker start` | the fleet, with livekit's own flags passed through |
| `worker talk` | one call served by THIS terminal — a `@tool` breakpoint lands where you typed |
| `worker download-files` | livekit's model files, ahead of the first call |

**The box's operator**

| verb | what |
|---|---|
| `migrate up` · `migrate status` | the schema, numbered SQL, applied in order |
| `doctor` | keys present · keys answer · livekit · postgres · embedder · lk — one line each, and what is down first |
| `box secrets` | every secret a box makes for itself, once; run twice rotates nothing |
| `box secret <NAME>` | one secret you bring, from stdin, replaced in place |
| `orgs list · add · rm · quota · provider-key` | the tenants, their quotas (`--minutes --messages --agents --concurrent-calls --memory-facts --knowledge-chunks`, the whole set at once; a flag left out is no limit and `0` refuses everything), the vendor keys an org brings |
| `keys issue · list · revoke` | an org's API keys: printed once, listed by fingerprint, revoked by UPDATE |
| `routes list · add · rm · seed` | which agent answers a number, from the next call |

**Reading a call**

| verb | what |
|---|---|
| `sessions list` | the calls, newest first |
| `sessions show <call>` | one call, entry by entry, every metric whole |
| `sessions tail <call>` | follow a call as it happens |
| `sessions recording <call>` | where its audio was written |
| `chat --agent <slug> [--url] [--caller]` | a text call from the terminal, one line per turn |

`sessions` reads Postgres, not HTTP: it is the operator's door, and works with the gateway down.
The tenant's own commands — `pinecall run`, `chat`, `test`, `simulate`, `eval`, `ui` — are the
agents repository's, and speak to this gateway with the org's key.

## Where the rest is

| | |
|---|---|
| `ARCHITECTURE.md` | LiveKit's half and ours, the entities, the wire, the two processes, the path of a call, the log, the rings |
| `docs/decisions/` | why each module is the way it is, one page per module |
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
