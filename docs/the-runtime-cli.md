# `pinecall-runtime`

The operator's terminal: the two processes, the database, the tenants and the box. One module per
group, one parser each; `pinecall-runtime` with nothing after it prints them all, and
`pinecall-runtime <group> --help` prints that group's verbs.

This is the box's side. The **tenant's** terminal is `pinecall` — the agents repo's
`docs/the-cli.md` — and the two never overlap: nothing here writes an agent, and nothing there
issues a key. Who a key belongs to is [multi-tenancy.md](multi-tenancy.md).

```bash
uv run pinecall-runtime <group> <verb>     # in a checkout
pinecall-runtime <group> <verb>            # installed, or inside the box's venv
```

## What each group needs

Three different things, and knowing which is which saves an afternoon:

| group | speaks to |
|---|---|
| `gateway` · `worker` · `chat` · `box` · `doctor` | this machine |
| `migrate` · `sessions` | **Postgres**, straight, over `DATABASE_URL` |
| `orgs` · `keys` · `routes` | **a running gateway**, over `/v1/ops/*` with `PINECALL_OPS_KEY` |

So `keys issue` on a box whose gateway is down is refused by the client, not by the table, and
`sessions list` works whether or not anything is running.

---

## `gateway`

```
pinecall-runtime gateway [--host 0.0.0.0] [--port 8080] [--reload]
```

The control plane: HTTP and WebSocket, one process, the whole API of
[protocol/gateway-api.md](protocol/gateway-api.md). It needs, at the least, one key of each
provider role and either `PINECALL_DEV_KEY` (a laptop) or `DATABASE_URL` with the schema applied (a
box) — and it uses the database whenever it answers, dev key or not. With a dev key it writes
`~/.pinecall/dev` (0600) so the tenant CLI on the same machine finds it with nothing exported.

`--reload` restarts on a source change; it is for writing the runtime, not for running it.

## `worker`

```
pinecall-runtime worker [dev | start | talk | download-files] [flags…]
```

The fleet: the process that holds spoken calls. `dev` for a laptop, `start` for a box, `talk` to
join a room from this terminal, `download-files` to warm the model files a first call would
otherwise wait for. Everything after the verb is passed through to livekit's own CLI untouched.

A worker takes jobs off LiveKit and asks the gateway for everything else, so it needs
`LIVEKIT_URL` and the pair, `PINECALL_GATEWAY_URL`, and a key to knock with. `PINECALL_MAX_JOBS`
caps how many calls one machine holds; unset, it gates on CPU.

## `chat`

```
pinecall-runtime chat --agent <slug> [--url http://localhost:8080] [--caller <id>]
```

A text call from this terminal against an agent some app is holding: one line per turn, the log as
it happens. It is the runtime's own twin of `pinecall chat` and needs no tenant checkout — useful
for asking "does this box answer at all" without a Node toolchain on it.

## `sessions`

```
pinecall-runtime sessions list [--agent <slug>] [--limit 20]
pinecall-runtime sessions show <call-id> [--json]
pinecall-runtime sessions tail [<call-id>]
pinecall-runtime sessions recording <call-id>
```

The log, read back straight from Postgres — no gateway, no key, no org filter: this is the
operator's view of the box, and it sees every tenant's calls. `show` prints one call entry by
entry; `--json` prints the reduced state instead. `tail` follows a call as it happens, and with no
id it follows the newest live one. `recording` says where that call's audio was written.

## `orgs`

```
pinecall-runtime orgs list
pinecall-runtime orgs add <slug> [--name "…"]
pinecall-runtime orgs rm <org>
pinecall-runtime orgs quota <org> [--minutes n] [--messages n] [--agents n]
                                  [--concurrent-calls n] [--memory-facts n] [--knowledge-chunks n]
pinecall-runtime orgs provider-key set <org> <vendor>     # the key on stdin
pinecall-runtime orgs provider-key rm  <org> <vendor>
pinecall-runtime orgs provider-key list <org>
```

The tenants. `<org>` is an id or a slug — every door takes either. `add` makes the row people will
type; `rm` is refused while the org still has keys or routes, so a tenant is never half-deleted.

`quota` replaces **the whole set**: a limit left out is no limit. The meter is a fold over the log,
so there is no counter to drift, and the gate runs before a call opens, before an agent registers
and before memory keeps a fact — never in the middle of a call.

`provider-key set` reads the key from **stdin**, never from a flag, for the reason every verb in
this repo that touches a secret does: argv is visible in `ps` to every user on the box. The row is
encrypted under `PINECALL_VAULT_KEY`; without one this verb is refused with a sentence.

## `keys`

```
pinecall-runtime keys issue [--org <org>] [--label "…"]
pinecall-runtime keys list  [--org <org>]
pinecall-runtime keys revoke <fingerprint>
```

```console
$ pinecall-runtime keys issue --org clinica --label "berna's laptop"
pk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
copy it now: the table keeps the fingerprint, and the key is never shown again
```

That sentence is the whole group: the table stores a sha256 and **no verb anywhere reads a key
back**. `list` prints fingerprint, label, created, and whether it is revoked. `revoke` takes a
fingerprint as `list` prints it and stops that key from being honoured; the row and the history
stay, so log entries that name it remain readable. Issue one key per place — a laptop, CI, each
deployment — with a label, because a key you can revoke on its own is a key you will revoke.

## `routes`

```
pinecall-runtime routes list [--org <org>]
pinecall-runtime routes add <number> <agent> [--channel phone|whatsapp] [--org <org>]
pinecall-runtime routes rm  <number> [--org <org>]
pinecall-runtime routes seed [--file infra/seed/routes.json]
```

Which number reaches which agent, and through which door. A number belongs to one agent at a time;
adding it again moves it. `seed` applies a file of them, which is how a box is brought up from a
checkout rather than from six commands.

## `migrate`

```
pinecall-runtime migrate up [--schema public]
pinecall-runtime migrate status
```

The `.sql` files under `pinecall/migrations`, applied in order, straight over `DATABASE_URL`. It is
what a unit runs before every start, so it prints no secret: the `default` org is seeded here and
its first key is `keys issue`, never this verb. `--schema` applies into a schema of its own, which
is how a test run owns its copy.

## `doctor`

```
pinecall-runtime doctor
```

Every dependency asked a real question — is the key present, does it answer, is the port open — and
one line each. It is the first thing to run on a box that behaves strangely, and the last thing to
run after a deploy.

```console
$ pinecall-runtime doctor
env: /Users/berna/pinecall-v2/runtime/.env

! api keys              PINECALL_DEV_KEY — one key, org default, the api_keys table not read; the
                        tables are Postgres's when it answers below. A box unsets it and issues org keys
✓ provider keys         llm ANTHROPIC_API_KEY, OPENAI_API_KEY · stt SONIOX_API_KEY, … · tts ELEVEN_API_KEY
✓ provider keys answer  ANTHROPIC_API_KEY · OPENAI_API_KEY · SONIOX_API_KEY · …
✓ livekit               http://127.0.0.1:7880/ — HTTP 200
✓ postgres              postgresql://pinecall@[::1]:5432/pinecall — vector, pg_textsearch
! embedder              tei · BAAI/bge-m3 — http://127.0.0.1:8081/info — ConnectError: …;
                        a lookup without it is skipped and said in the call's log: this stops no call
! lk                    not installed — brew install livekit-cli

all up
```

`✓` is answered, `!` is advice — something degraded that stops no call — and `✗` is broken. What it
asks after depends on `PINECALL_ROLE`: `all`, `hub` (no worker) or `worker`.

The first line is the one that reads differently on a laptop and on a box. A dev key is advice
here and **the first thing down** on a box, because it is the only key such a gateway honours:
every call would be org `default` and every tenant invisible — a silence no other check would
notice.

## `box`

```
pinecall-runtime box secrets [--into /etc/credstore.encrypted]
pinecall-runtime box secret <NAME> [--into …]        # the value on stdin
```

`secrets` generates, once, everything a box makes for itself and nobody issues to it: the LiveKit
pair, the Postgres password and `DATABASE_URL`, `PINECALL_OPS_KEY`, `PINECALL_VAULT_KEY`, and the
`media.env` the three containers read. Run twice it **rotates nothing** — what is there is kept and
only what is missing is made.

`secret` keeps one secret you bring under its own name, read from stdin: a provider key, WhatsApp's
token. Both write systemd encrypted credentials, which is why the runtime reads
`CREDENTIALS_DIRECTORY` as a source of settings: on a box a secret is a file the unit decrypts, not
a line in an environment file.

---

## The environment

`.env` in the directory the process started in, then in each parent up to the repository root; on a
box, systemd credentials instead. Our own knobs carry `PINECALL_`; a vendor key keeps the vendor's
own name, so the SDK that reads `ANTHROPIC_API_KEY` by itself and this runtime agree.

| | |
|---|---|
| `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane both processes talk to. The secret also signs call tokens |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join, when it differs |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service |
| `TEI_URL` · `EMBED_PROVIDER` · `EMBED_MODEL` · `EMBED_BASE_URL` | who embeds, and where |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `SONIOX_API_KEY` · `DEEPGRAM_API_KEY` · `ELEVEN_API_KEY` | a call needs one key of each role: llm, stt, tts |
| `PINECALL_DEV_KEY` | one key, org `default`, the only one honoured; needs no database, uses one when it answers. Development only |
| `PINECALL_API_KEY` | an org's key, for a worker or an app that runs here |
| `PINECALL_OPS_KEY` | what `/v1/ops/*` is authenticated by. Unset, the operator API is closed |
| `PINECALL_VAULT_KEY` | the Fernet key a tenant's own provider keys are encrypted under |
| `PINECALL_ROLE` | what this box runs: `all` · `hub` · `worker` |
| `PINECALL_GATEWAY_URL` | the gateway a worker's job asks |
| `PINECALL_MAX_JOBS` · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom |
| `RECORD` · `PINECALL_RECORDINGS` | whether a call's audio is kept, and where it lands |
| `WHATSAPP_ACCESS_TOKEN` · `WHATSAPP_APP_SECRET` · `WHATSAPP_VERIFY_TOKEN` | Meta's webhook |
| `PINECALL_JUDGE_CEILING_EUR` | what judging one call may spend on a model. Zero: no judge asks |
| `PINECALL_VOICE_LOOKUP_BUDGET_MS` · `PINECALL_TEXT_LOOKUP_BUDGET_MS` · `PINECALL_REMEMBER_BUDGET_S` | how long a turn waits for recall and search, and a hang-up for memory |
| `PINECALL_LOG_LEVEL` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |

---

## A laptop, from nothing

```bash
docker compose -f infra/compose/dev.yml up -d      # livekit · sip · redis · postgres · tei
cd runtime && uv sync --extra runtime --group dev
cp .env.example .env                               # and fill in the provider keys
pinecall-runtime doctor                            # every line green before anything else
pinecall-runtime gateway                           # writes ~/.pinecall/dev; the CLI finds it
pinecall-runtime worker dev                        # in another terminal, for spoken calls
```

That gateway runs on a dev key: one key, org `default`, and no `pinecall login` anywhere. Give it
a database of its own if the same Postgres also holds a real org — `create database pinecall_dev`,
the two extensions, `migrate up` against it — because a dev key IS org `default`, and an agent
another org registered in the shared database is one the dev key is told it cannot read. With the
compose Postgres answering it has every table a box has — the knowledge base, contact memory, the
vault (given a `PINECALL_VAULT_KEY`), durable routes — and without it, it still runs, keeps its
log in memory and says so on its first line. On an M-series Mac, TEI needs the arm64 tag
`infra/README.md` names in `TEI_IMAGE`; without an embedder a push answers 503 and a lookup is
skipped and said in the call's log.

## A box, from nothing

```bash
pinecall-runtime box secrets                       # the pair, the password, the ops and vault keys
pinecall-runtime migrate up                        # the schema, and the `default` org
# gateway and worker start as systemd units, reading those credentials
pinecall-runtime doctor                            # from the box, with PINECALL_ROLE set
pinecall-runtime orgs add clinica --name "Clínica Norte"
pinecall-runtime keys issue --org clinica --label "berna's laptop"
pinecall-runtime routes add +34910000000 clinica-norte --org clinica
```

`PINECALL_DEV_KEY` is never set on a box: it opens no database, and a box IS its database.
[multi-tenancy.md](multi-tenancy.md) says what follows from that, and what the tenant does with the
key that came out of `keys issue`.
