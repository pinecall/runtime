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
| `gateway` · `worker` · `box` · `doctor` · `providers` | this machine |
| `migrate` · `sessions` | **Postgres**, straight, over `DATABASE_URL` |
| `init` · `orgs` · `keys` · `routes` · `fleet` | **a running gateway**, over `/v1/ops/*` with `PINECALL_OPS_KEY` — and `fleet loop`, a cloud's own CLI beside it |

So `keys issue` on a box whose gateway is down is refused by the client, not by the table, and
`sessions list` works whether or not anything is running.

---

## `init`

```
pinecall-runtime init [--org <slug>] --email <address> --person "<name>" [--name "…"] [--role admin]
```

The first org and the first person, on a runtime nobody has used yet — the one command between a
migrated database and a terminal that can sign in. It makes the org (`--org` defaults to
`default`, which the schema seeds), invites its first **admin**, makes that person an **operator**
of this box — somebody has to be able to make the second org, and on a fresh runtime there is
nobody else — and prints the invitation link and the two lines to type next.

```console
$ pinecall-runtime init --email you@example.com --person "Your Name"
org default is already there
m_b3796f3579fc  you@example.com  admin  runs this box
  http://127.0.0.1:8080/invitations/inv_…

  Open the link above to set a password. Then, in the directory of an agent:

    pinecall login http://127.0.0.1:8080
    pinecall run
```

Run it twice and it carries on to the person rather than stopping at the org: it is the verb
somebody runs twice while reading the README. A second tenant afterwards is [`orgs`](#orgs).

The whole path, with every output under it, is [from-zero.md](from-zero.md).

## `gateway`

```
pinecall-runtime gateway [--host 0.0.0.0] [--port 8080] [--reload]
```

The control plane: HTTP and WebSocket, one process, the whole API of
[protocol/gateway-api.md](protocol/gateway-api.md). It needs, at the least, one key of each
provider role and `DATABASE_URL` with the schema applied — a laptop as much as a box, because a key
is verified against the `api_keys` table and there is nowhere else it could be. With no database
the gateway says so at startup and answers every keyed door 503 with the same sentence.

`--reload` restarts on a source change; it is for writing the runtime, not for running it.

## `worker`

```
pinecall-runtime worker [dev | start | overflow | talk | download-files] [flags…]
```

The fleet: the process that holds spoken calls. `dev` for a laptop, `start` for a box, `talk` to
join a room from this terminal, `download-files` to warm the model files a first call would
otherwise wait for. Everything after the verb is passed through to livekit's own CLI untouched.

A worker takes jobs off LiveKit and asks the gateway for everything else, so it needs
`LIVEKIT_URL` and the pair, `PINECALL_GATEWAY_URL`, and a key to knock with. `PINECALL_MAX_JOBS`
caps how many calls one machine holds; unset, it gates on CPU.

`dev` and `start` also **heartbeat** to the gateway every five seconds — the worker's name
(`PINECALL_WORKER_NAME`, else the short hostname), the calls it holds, its measured seats, its
load — which is what `fleet list` shows and the loop sizes on. A worker told it was **cordoned**
takes no new call, finishes the ones it holds, and exits **3**; the unit's
`RestartPreventExitStatus=3` leaves it down.

`overflow` is the one worker that is never full: it runs on the hub, reports itself full to
LiveKit until the gateway says every real worker is, and then answers the call nobody else can
— one sentence (`PINECALL_OVERFLOW_SAYS`), the caller's number onto the agent's log as
`callback.requested`, and it hangs up. No STT, no model.

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
pinecall-runtime orgs invite <org> <email> --name "…" [--role admin|manager|developer|supervisor|qa]
pinecall-runtime orgs operator <org> <email> [--revoke]  ·  orgs remove-member <org> <email>
pinecall-runtime orgs move <agent> <org>
pinecall-runtime orgs rm <org>
pinecall-runtime orgs quota <org> [--minutes n] [--messages n] [--agents n]
                                  [--concurrent-calls n] [--memory-facts n] [--knowledge-chunks n]
                                  [--numbers n] [--seats n]
pinecall-runtime orgs provider-key set <org> <vendor>     # the key on stdin
pinecall-runtime orgs provider-key rm  <org> <vendor>
pinecall-runtime orgs provider-key list <org>
```

The tenants. `<org>` is an id or a slug — every door takes either. `add` makes the row people will
type; `invite` is how a tenant gets its first person on a gateway that takes no sign-up — it prints
the row and a **link**, once, that opens the console's password card (the operator holds a token
and never a password, and the invitation takes none of the org's seats); `rm` is refused while the
org still has keys or routes, so a tenant is never half-deleted.

A person is their email, with one password across every org: `invite` of an address that already
has one prints no link and seats them `active` (`already a person on this box: seated, they sign in
with the password they have`). `operator` makes a member, by email, an operator of this box — their
key then opens every `/v1/ops` door, as `init` does for the first person; `--revoke` takes it back. `remove-member` takes a person out of an org **for good** — keys revoked, row and links gone, the seat free — and carries the door's refusal for the org's last active admin.

`move` undoes the one thing a slug could not undo: it belongs to the org that first registered it
for as long as its log exists, and a box walks into the wrong one by construction — its own worker
and operator keys are issued into `default`, so the first agent anybody runs there lands in
`default` too. The agent's own log, one head row per call it has taken, and its numbers all go
with it; it is refused while somebody is holding the slug, and a number the destination org
already answers at stays where it is and is named.

`quota` replaces **the whole set**: a limit left out is no limit. The meter is a fold over the log,
so there is no counter to drift, and the gate runs before a call opens, before an agent registers,
before memory keeps a fact and before an invitation makes a row — never in the middle of a call.
`--seats` is what a plan sells a team by: everybody the org has not disabled, invited and active
together, because an invitation sent is a seat taken.

`provider-key set` reads the key from **stdin**, never from a flag, for the reason every verb in
this repo that touches a secret does: argv is visible in `ps` to every user on the box. The row is
encrypted under `PINECALL_VAULT_KEY`; without one this verb is refused with a sentence.

## `keys`

```
pinecall-runtime keys issue [--org <org>] [--label "…"] [--env production|sandbox]
                            [--scope <scope>]… [--subject <member>] [--name "…"]
pinecall-runtime keys list  [--org <org>]
pinecall-runtime keys revoke <fingerprint>
```

```console
$ pinecall-runtime keys issue --org clinica --label "berna's laptop" --env sandbox
pk_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  org clinica · sandbox · berna's laptop
  every scope
  copy it now: the table keeps the fingerprint, and the key is never shown again
```

That sentence is the whole group: the table stores a sha256 and **no verb anywhere reads a key
back**. `list` prints fingerprint, world, label, whose, and whether it is revoked. `revoke` takes a
fingerprint as `list` prints it and stops that key from being honoured; the row and the history
stay, so log entries that name it remain readable. Issue one key per place — a laptop, CI, each
deployment — with a label, because a key you can revoke on its own is a key you will revoke.

`--env` is **the key knowing where**: the agents registered on it, the doors they claim and every
call they take are that world's, and the gateway keeps production and the sandbox apart — the same
slug held once in each, a number in one refused to the other. A box's worker and app run on a
production key, which is the default; a laptop gets a sandbox one. `--scope`, repeatable, is
what the key may do (`app` · `calls` · `talk` · `supervise` · `pipeline` · `knowledge` · `memory` ·
`evals` · `numbers` · `keys` — the org's own API keys — · `providers` — the vendor keys it brought
— · `team` · `usage` · `fleet`); left out is every scope but `fleet`, which is the box's own worker's
and is minted only when typed (`pinecall-worker-key.service` types it). An org issues its own machine keys without the
operator at `POST /v1/keys`; these verbs are the box's way in, on `PINECALL_OPS_KEY`. `--subject` and `--name`
say whose the key is when it is a person's, so a seat minted from it says who sat down.

## `routes`

```
pinecall-runtime routes list [--org <org>] [--env production|sandbox]
pinecall-runtime routes add <number> <agent> [--channel phone|whatsapp] [--org <org>] [--env …]
pinecall-runtime routes rm  <number> [--org <org>]
pinecall-runtime routes seed [--file infra/seed/routes.json]
```

Which number reaches which agent, through which door, in which world. A number belongs to one agent
at a time; adding it again moves it — to another agent, or with `--env` to the other world. `list`
answers one world, production unless asked. `seed` applies a file of them (each route may carry an
`env`), which is how a box is brought up from a checkout rather than from six commands.

## `fleet`

```
pinecall-runtime fleet list
pinecall-runtime fleet cordon <worker>
pinecall-runtime fleet uncordon <worker>
pinecall-runtime fleet loop --cloud <gcp|aws|hetzner|./yours> --seats <n> [--target 0.6] [--min 1] [--max 10] [--every 15] [--once] [--dry-run]
```

`list` is the roster as the hub hears it — one line per worker that has ever knocked, with what
it holds, its seats, its load, its standing (`accepting` · `full` · `draining` · `cordoned` ·
`gone`) and when it was last heard — and the totals: `free = Σ(max − active)`, how many accept,
and **FULL** when nobody does.

```
worker             held  seats  load  standing   heard
pinecall-box       1     cpu    0.31  accepting  3s ago
pinecall-worker-1  2     4      0.50  accepting  4s ago

2 up · 3 calls · 2 seats free · 2 accepting
```

`cordon` is the graceful shrink: the worker is told on its next heartbeat, takes no new call,
finishes the ones it holds, and leaves. `uncordon` takes it back while it is still there.

`loop` is the fleet loop ([scaling.md](scaling.md)): every `--every` seconds it reads the roster
and the cloud, and keeps `busy = active / seats` at `--target` — asks for a machine when over it,
cordons the quietest one when under it by 0.15 or more, deletes a cordoned machine once it holds
nothing, and deletes one that never dialled in. `--seats` is the `PINECALL_MAX_JOBS` baked into
the image, so a machine still booting counts from the moment it is asked for. `--cloud` names a
script under `infra/fleet/` or a path to yours ([../infra/fleet/README.md](../infra/fleet/README.md));
the cloud's own CLI must be signed in wherever the loop runs. `--once --dry-run` prints one tick's
verdict and touches nothing.

## `migrate`

```
pinecall-runtime migrate up [--schema public] [--post]
pinecall-runtime migrate status
pinecall-runtime migrate plan
```

The `.sql` files under `pinecall/migrations`, applied in order, straight over `DATABASE_URL`. It is
what a unit runs before every start, so it prints no secret: the `default` org is seeded here and
its first key is `keys issue`, never this verb. `status` says which have run and `plan` which would
run next. `--schema` applies into a schema of its own, which is how a test run owns its copy.

**`--post` is the other half, and it is never run at startup.** A migration is held to five seconds
there — the unit runs `migrate up` before the gateway opens its socket — so anything slower is
written as a `.post.sql`, applied by a person, after the deploy, with this flag. An index over a
big table is always one of those.

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

✓ api keys              the api_keys table — `pinecall-runtime keys issue --org <slug>` mints one
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

The first line used to read differently on a laptop and on a box, because a laptop could run on
`PINECALL_DEV_KEY` — one key, org `default`, the table not read. That was a second runtime, and it
is gone: there is one table everywhere, and the line names the verb that puts a key in it.

## `providers`

```
pinecall-runtime providers [--does llm|stt|tts]
```

Every vendor this build can reach, what each one does, and whether it has a key here: `ready` ·
`no key` · `no plugin` · `its own`. It is the same table `pinecall providers` prints for a tenant,
read from the box's side — the answer to "can this box speak Spanish with ElevenLabs" before a
call proves it cannot.

```console
$ pinecall-runtime providers
vendor        does         standing   variable              also known as
livekit       llm,stt,tts  ready                            inference lk
anthropic     llm          ready      ANTHROPIC_API_KEY     claude
assemblyai    stt          no key     ASSEMBLYAI_API_KEY    assembly
```

A vendor an org brought of its own is `orgs provider-key`, above; this table is the box's.

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
| `PINECALL_WORKER_KEY` | the key the worker knocks with. On a box the fleet's: `keys issue --org default --scope fleet --scope app --scope calls`, which is what lets one worker answer every org's calls. On a laptop an org's own key, and the worker serves that org |
| `PINECALL_OPS_KEY` | what `/v1/ops/*` is authenticated by. Unset, the operator API is closed |
| `PINECALL_VAULT_KEY` | the Fernet key a tenant's own provider keys are encrypted under |
| `PINECALL_ROLE` | what this box runs: `all` · `hub` · `worker` |
| `PINECALL_GATEWAY_URL` | the gateway a worker's job asks |
| `PINECALL_MAX_JOBS` · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom |
| `PINECALL_WORKER_NAME` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), and the overflow agent's one sentence |
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
pinecall-runtime migrate up                        # the schema, on the compose Postgres
pinecall-runtime doctor                            # every line green before anything else
pinecall-runtime init --org clinica \
  --email berna@clinica.test --person "Berna"      # the first org, and a link to set a password
pinecall-runtime gateway
pinecall-runtime worker dev                        # in another terminal, for spoken calls
pinecall login http://localhost:8080               # in the agent's directory, as a person
```

[from-zero.md](from-zero.md) is this same path with every output under it, through to a call.

**This is the same runtime a box runs, and there is no other.** A laptop used to have one of its
own — `PINECALL_DEV_KEY`, one key that needed no database, org `default`, no login anywhere — and
what it bought in the first five minutes it charged back in every hour after: two sets of keys,
two orgs, two behaviours, and no way to see which you were on. So: the same Postgres, the same
migrations, the same issued keys, and `init` in place of the magic key.

It has every table a box has — the knowledge base, contact memory, the vault (given a
`PINECALL_VAULT_KEY`), durable routes. On an M-series Mac, TEI needs the arm64 tag
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

[multi-tenancy.md](multi-tenancy.md) says what a key IS, and what the tenant does with the one that
came out of `keys issue`.
