# Orgs, keys and tenants

Who a key belongs to, what each kind of credential opens, why a laptop needs none of it, and how a
real tenant is given one. The doors are [protocol/gateway-api.md](protocol/gateway-api.md) and
[protocol/operator-api.md](protocol/operator-api.md); the verbs are
[the-runtime-cli.md](the-runtime-cli.md). This page is the model underneath both.

## An agent has no key. An org has keys.

This is the sentence the whole model hangs on, and the one that surprises everybody once:

> **A key IS an org.** An agent belongs to the org whose key registered it. There is no agent
> credential, no per-agent secret, nothing to rotate when an agent is renamed.

An app opens `WS /v1/apps` with an org's API key and says `agent.register`. The gateway reads the
org off that key — off the `KeyRecord`, never off anything the app sent — and from then on:

- the agent is **held** for that org, and another org asking for it is told it is not there;
- every call it takes writes a log **owned** by that org (`store.owner`);
- every door that reads that log checks the reader's org against the log's owner and answers
  `403 this key does not read that org's log`, never a 404 — whether a call exists is not another
  tenant's business;
- its knowledge base, the memory of its contacts, its provider keys and its quotas are that org's.

So "clínica-norte does not have a key" is not a gap. It never had one, and it never will.

## The four credentials

| | what it is | who mints it | where it lives | opens |
|---|---|---|---|---|
| **org API key** | `pk_` + 256 bits. The tenant's own | the operator, once per org, as often as needed | the tenant's `~/.pinecall/credentials`, or `PINECALL_API_KEY` in their container | every `/v1/…` door, for that org's rows only |
| **dev key** | `PINECALL_DEV_KEY`, one string in the gateway's own environment | whoever runs the gateway | the gateway's `.env`, and `~/.pinecall/dev` for the CLI beside it | everything, as org `default`, with or without a database |
| **ops key** | `PINECALL_OPS_KEY`, the box's own | the box, once (`box secrets`) | a systemd credential on the box | `/v1/ops/*` and nothing else. It is a gate, not an identity: it belongs to no org |
| **room token** | a LiveKit JWT bound to ONE call | the gateway, from an org key, per visit | a browser tab, for a minute | that call's room and that call's log. See [protocol/tokens.md](protocol/tokens.md) |

A key is stored as its **sha256** and nothing else. `keys issue` prints the plaintext once — there
is no verb, here or anywhere, that reads one back — and `keys list` prints fingerprints, labels and
dates. Revoking keeps the row, so the log entries that name that key stay readable.

## Why the laptop never ran `pinecall login`

Because the gateway on it runs on **a dev key**, and a dev key is deliberately a different shape of
thing:

```
PINECALL_DEV_KEY=…            # in runtime/.env
pinecall-runtime gateway      # writes ~/.pinecall/dev (0600): its URL and its key
```

The tenant CLI beside it reads that file and needs no login — that is the whole point: `git clone`,
`pnpm i`, `pinecall run`, and an agent answers. A dev key **needs** no database and **uses** one
when `DATABASE_URL` answers, exactly as the log does: with the dev stack up, the same laptop has
the knowledge base, a contact's memory, durable routes and the vault, because those are tables and
the tables are there. Without it, everything that is a table is absent or in memory:

| on a dev key | with Postgres | without |
|---|---|---|
| the knowledge base, a contact's memory, a tenant's own provider keys | on | absent: the doors say so with a 503 that names the database |
| the orgs, the routes, the minted tokens, the pipeline overrides, the eval runs | durable | in memory, forgotten when the process exits |
| the log | durable | in memory, and the gateway says so on its first line |
| which keys open the doors | **the dev key alone**, whatever `api_keys` holds | the dev key alone |

An exported `PINECALL_API_KEY` is ignored out loud, by the CLI and by the box alike: a gateway on a
dev key honours that key and no other.

**Should the runtime allow this? Yes — and only here.** The alternative is that the first five
minutes with this repo are a database installation. The rule that keeps it safe is short:

> **A box never sets `PINECALL_DEV_KEY`.** A box has tenants, and a gateway with a dev key honours
> that one key and reads no `api_keys` at all. Setting one on a box would make every call org
> `default` and every real tenant invisible — not a leak, but a silence. The doctor's first line
> is that silence, said out loud.

A gateway with **neither** a dev key nor a database refuses to start at all: it could verify
nothing.

## Giving a tenant a key

The operator verbs speak the gateway's `/v1/ops/*` over HTTP with `PINECALL_OPS_KEY` — they are not
database scripts, so the gateway must be up. On the box, in order:

```bash
pinecall-runtime migrate up                       # the schema, and the `default` org
pinecall-runtime orgs add clinica --name "Clínica Norte"
pinecall-runtime keys issue --org clinica --label "berna's laptop"
#   pk_…  copy it now: the table keeps the fingerprint, and the key is never shown again
pinecall-runtime orgs quota clinica --minutes 2000 --agents 5 --concurrent-calls 10
pinecall-runtime routes add +34910000000 clinica-norte --org clinica --channel phone
```

Then, on the tenant's machine, once:

```bash
pinecall login https://box.pinecall.io          # asks for the key, proves it at /v1/whoami
pinecall whoami                                 # gateway · key from credentials · org clinica
cd clinica-norte && pinecall run                # the agent is now that org's
```

In a container there is no login: `PINECALL_API_KEY` in the environment is the same key, and
`PINECALL_URL` says which gateway. That is the whole of a tenant's authentication.

## Two worlds on one gateway

A tenant writes an agent on a laptop and runs the same agent on the box, and the two must never
see each other: a laptop's `pinecall run` must not take the clinic's number, and the clinic's
sessions must not fill with a developer's test calls. So **the key knows where.** It is issued into
`production` or `development`, and the gateway namespaces its registry and its routes by that
word: the same slug is held once in each world, by different sockets; `GET /v1/agents`, `GET
/v1/routes` and every door that names an agent answer the world the key opens; a dialled number
is one agent's in one world, and a development key claiming a production number is refused with
the world named. `agent.registered` and `call.started` carry `env`, so a console and a session
list can say which world they are reading. A dev key opens development — a laptop is where things
are written — and every key issued before the field existed is production's.

```bash
pinecall-runtime keys issue --org clinica --label "berna's laptop" --env development
pinecall-runtime routes add +34910000000 clinica-norte --org clinica   # production, the default
```

The key also knows **what** — `scopes`, the doors as they are grouped — and **who** — `subject`
and `name`, the member it was minted for. A key issued with nothing said holds every scope, which
is what an org's own machine key means; a person's key holds what their role presets.

## People

A person of an org is a **member**, not a shared key: invited with a one-use link (`POST
/v1/members`, on the org's key), active once they chose a password (`POST /v1/invitations/{token}`),
and holding keys of their own from then on — one per device, minted at `POST /v1/login` with the
scopes of their role (`qa` · `supervisor` · `manager` · `admin` · `developer`) and their member id
as `subject`. **In production a person's key never holds `app`**: a deployed agent is held by a key
issued for a machine (`POST /v1/keys`, or `keys issue --scope app`), not by whoever is logged in. Disabling them keeps the row, revokes every key of theirs and refuses their login. A
browser never carries a key in a URL: a key holder mints a one-use code (`POST /v1/login/codes`)
and the browser spends it for a key of its own. The doors that refuse on a scope are the next card.

**One key per place, not one per tenant.** Issue a key for the laptop, one for CI, one for each
deployment, each with a `--label` — a key you can revoke on its own is a key you will revoke.

## Taking it back

```bash
pinecall-runtime keys list --org clinica         # fingerprints, labels, created, revoked
pinecall-runtime keys revoke <fingerprint>       # stops being honoured from the next request
pinecall-runtime orgs rm clinica                 # refused while it still has keys or routes
```

Revoking is immediate and total for that key: the socket of an app already registered stays up
until it reconnects, and every new request with it is refused. Nothing that key wrote is deleted —
the rows and the log stay, which is the point of keeping the revoked row.

## What one tenant cannot see of another

Each of these is one rule in one place, and they are the whole of the isolation:

| | |
|---|---|
| a log | `refuse_another_org` — the reader's org against the log's owner, on every door that reads one |
| an agent | the registry answers only the org's own; another org's slug is "no app is holding it" |
| a call token | bound to ONE call, and `refuse_another_call` refuses it on any other |
| what a browser may see of an entry | the **public** projection, decided from what the reader IS, never from a parameter it sends ([protocol/projections.md](protocol/projections.md)) |
| a provider key | one row per (org, vendor), encrypted; read back by exactly one door, the worker's, on the org's own key |
| a quota | `Admission` folds the org's own usage out of the log before every call and every register |

The operator's key crosses those lines by design — it is the box's own — and it is the only thing
that does. It opens `/v1/ops/*` and no tenant door: an ops key cannot read a call.

## Provider keys, per tenant

By default every call runs on the **box's** vendor keys, out of its environment. An org may bring
its own, and then every call of that org runs on its account from the next one:

```bash
# the operator, for a tenant who sent theirs
printf %s "$KEY" | pinecall-runtime orgs provider-key set clinica elevenlabs
pinecall-runtime orgs provider-key list clinica

# or the tenant themselves, with their own org key
pinecall keys add elevenlabs        # reads the key from stdin, never from a flag
```

The rows are encrypted with `PINECALL_VAULT_KEY`, which lives in the box's environment and never in
the database. A runtime without one cannot keep somebody else's secret and says so with a 503 —
[the gateway API §6](protocol/provider-keys.md).

## Quotas

```bash
pinecall-runtime orgs quota clinica --minutes 2000 --messages 5000 --agents 5 \
                                    --concurrent-calls 10 --memory-facts 50000 \
                                    --knowledge-chunks 20000 --numbers 1
```

The whole set is replaced at once, and a limit left out is **no limit**. The meter is a fold over
the log — there is no counter table to drift — and the gate runs before a call opens, before an
agent registers, and before memory writes a fact. A tenant over one is refused with a sentence and
`credits.exhausted` in their own log; nothing is cut mid-call.

## Where each thing is written

| | |
|---|---|
| `orgs` | id, slug, name. `default` is seeded by the migrations |
| `api_keys` | sha256 fingerprint, org, label, created_at, revoked_at. Never the key |
| `quotas` | one row per org, the whole set replaced |
| `routes` | number → (org, agent, channel). One number belongs to one agent at a time |
| `provider_keys` | one row per (org, vendor), Fernet ciphertext under `PINECALL_VAULT_KEY` |
| `call_log`, `call_log_head` | every entry, with the org that owns the call |
| `knowledge_bases`, `knowledge_chunks`, `contact_memories` | per org |
| `tokens` | which room tokens were minted and which were spent |

A tenant is a row in `orgs` and at least one row in `api_keys`. Everything else follows from the
key their app knocks with.
