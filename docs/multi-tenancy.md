# Orgs, keys and tenants

Who a key belongs to, what each kind of credential opens, why a laptop needs none of it, and how a
real tenant is given one. The doors are [protocol/gateway-api.md](protocol/gateway-api.md) and
[protocol/operator-api.md](protocol/operator-api.md); the verbs are
[the-runtime-cli.md](the-runtime-cli.md). This page is the model underneath both, from the
operator's side; the same model as a tenant's developer walks it — sign-up, laptop, console,
deploy, team — is the agents repo's `docs/worlds-and-teams.md`.

## An agent has no key. An org has keys.

This is the sentence the whole model hangs on, and the one that surprises everybody once:

> **A key IS an org.** An agent belongs to the org whose key registered it. There is no agent
> credential, no per-agent secret, nothing to rotate when an agent is renamed.

An app opens `WS /v1/apps` with an org's API key and says `agent.register`. The gateway reads the
org off that key — off the `KeyRecord`, never off anything the app sent — and from then on:

- the agent is **held** for that org, and another org asking for it is told it is not there;
- every call it takes writes a log **owned** by that org (`store.owner`), and in the corner it was
  opened in — the world and whose, on the same head row (`0024`);
- every door that reads that log checks the reader's org against the log's owner and answers
  `403 this key does not read that org's log`, never a 404 — whether a call exists is not another
  tenant's business;
- its knowledge base, the memory of its contacts, its provider keys and its quotas are that org's.

So "clínica-norte does not have a key" is not a gap. It never had one, and it never will.

## The three credentials

| | what it is | who mints it | where it lives | opens |
|---|---|---|---|---|
| **org API key** | `pk_` + 256 bits. The tenant's own | **the tenant itself**, `POST /v1/keys` or `pinecall keys issue` (the `keys` scope) — or the operator, `keys issue --org` | the tenant's `~/.pinecall/credentials`, or `PINECALL_WORKER_KEY` in their container | every `/v1/…` door, for that org's rows only |
| **ops key** | `PINECALL_OPS_KEY`, the box's own | the box, once (`box secrets`) | a systemd credential on the box | `/v1/ops/*` and nothing else. It is a gate, not an identity: it belongs to no org. A person the box made an **operator** (`orgs operator`, migration 0020) opens the same doors with their own key — the flag is on their member row, read on every request |
| **room token** | a LiveKit JWT bound to ONE call | the gateway, from an org key, per visit | a browser tab, for a minute | that call's room and that call's log. See [protocol/tokens.md](protocol/tokens.md) |

**And the box's worker holds a fourth kind: an org key with the `fleet` scope.** One worker
answers every org's spoken calls, so its key cannot be one tenant's. `pinecall-worker-key.service`
mints it into org `default` with `--scope fleet --scope app --scope calls`, and at every door the
worker knocks — routes, an agent's config, its provider keys, opening and writing a call — that
scope means the corner is **the call's**: the org, the world and the holder the dispatch named
(`?org=&env=&holder=`), never the key's own. The scope is in no role's preset and no login mints
it; a tenant's key that names another corner is refused 403. So "a key IS an org" keeps its one
exception where it has to: the machine that serves them all.

A key is stored as its **sha256** and nothing else. `keys issue` prints the plaintext once — there
is no verb, here or anywhere, that reads one back — and `keys list` prints fingerprints, labels and
dates. Revoking keeps the row, so the log entries that name that key stay readable.

An org issues its own without the operator: `POST /v1/keys {label?, env?, scopes?}` on a key that
opens `keys`, which mints one for a **machine** — `app` and production when nothing is said, and
naming nobody, because people get keys by logging in. A key may not issue a scope it does not
itself open, and a fingerprint that is not the org's is the 404 a stranger's is.

## A laptop is a box with one tenant

There was a fourth credential here: `PINECALL_DEV_KEY`, one string in the gateway's own
environment that needed no database and, when it was set, was the **only** key the gateway
honoured — every call org `default`, the `api_keys` table not read, and a `~/.pinecall/dev` file
the CLI beside it picked up so nobody had to log in. It bought five minutes at the start and cost
a second runtime: a laptop and a box had different keys, different orgs, different worlds and
different bugs, and the one you were on was decided by an environment variable you could not see.
It is gone.

**One runtime for everything.** A laptop runs the same Postgres, the same migrations and the same
issued keys a box does:

```
docker compose -f infra/compose/dev.yml up -d
pinecall-runtime migrate up
pinecall-runtime init --org clinica --email berna@clinica.test --person "Berna"
pinecall-runtime gateway
pinecall login http://localhost:8080
```

`init` is the one command that replaces the magic key: it makes the first org, invites its first
admin — who is also made an **operator** of this box, because somebody has to be able to make the
second org — and prints the link that opens the password screen. After it, a laptop is signed in
the way a customer's machine is, with a key of that person's own.

A gateway with **no database refuses to verify anything**, says so on its first line at startup,
and answers every keyed door `503` with the same sentence. It does not come up looking healthy.

## Giving a tenant its first person

A gateway that takes no sign-up — every box of its own, and a cloud that shut them — gets its
tenants from the operator: the org is made, and its first admin is **invited**. The operator verbs
speak the gateway's `/v1/ops/*` over HTTP with `PINECALL_OPS_KEY` — they are not database scripts,
so the gateway must be up. On the box, in order:

```bash
pinecall-runtime migrate up                       # the schema, and the `default` org
pinecall-runtime orgs add pinecall --name "Pinecall"
pinecall-runtime orgs invite pinecall bernardo@pinecall.io --name "Bernardo"   # --role admin
#   m_…  bernardo@pinecall.io  admin  invited
#     https://box.pinecall.io/invitations/inv_…
#     send them this; it opens the console's password screen once, within a week
pinecall-runtime orgs quota pinecall --seats 10 --agents 25
```

The link opens the console's own card: the person chooses a password, the token is spent, and
they hold their first key — an admin's, every door of the org in production. The sandbox is not
on this console: a person's sandbox key is their machine's (`pinecall login`), and what it holds is
watched there, on `pinecall serve`. The operator held a **token** and never a password: an
invitation is inert until the person it names accepts it, so the box can seat somebody and never
be them. An address that already has a password on this box gets no link: `orgs invite` prints the
row `active` and `already a person on this box: seated, they sign in with the password they have`.
From there the admin invites the rest from the Team screen, and issues the key the org's
server runs on from the Keys screen (or `pinecall keys issue`) — the operator is out of the loop.
The whole of it from the developer's side is the agents repo's `docs/worlds-and-teams.md`.

## Giving a machine a key

A process is not a person and has no password: a worker, a CI job, a box the tenant deploys to.
The tenant issues those itself (`POST /v1/keys`); the operator can too, for a tenant who asked:

```bash
pinecall-runtime keys issue --org pinecall --label "prod server"   # --scope app, production
#   pk_…  copy it now: the table keeps the fingerprint, and the key is never shown again
pinecall-runtime routes add +34910000000 tienda-sur --org pinecall --channel phone
```

In a container there is no login: `PINECALL_WORKER_KEY` in the environment is that key, and
`PINECALL_URL` says which gateway. On a laptop, `pinecall login` keeps a key **nobody typed** —
it prints a link, the person signs in on that page, and the page mints the terminal a key of its
own (see below). That is the whole of a tenant's authentication.

## Two worlds on one gateway

A tenant writes an agent on a laptop and runs the same agent on the box, and the two must never
see each other: a laptop's `pinecall run` must not take the clinic's number, and the clinic's
sessions must not fill with a developer's test calls. So **the key knows where.** It is issued into
`production` or `sandbox`, and the gateway namespaces its registry and its routes by that
word: the same slug is held in each world by different sockets; `GET /v1/agents`, `GET
/v1/routes` and every door that names an agent answer the world the key opens; a dialled number
is one agent's in one world, and a sandbox key claiming a production number is refused with
the world named.

**And the key knows whose.** The sandbox is namespaced a second time, by the member the key was
minted for, because a tenant is a team: Berna and Carla both run `tienda-sur` on their own
laptops, each reaches their own, and neither takes the other's. A sandbox key that names
nobody — CI's, a machine's — holds the org's own, which is what a developer holding none falls
back to. Production is namespaced by nobody, because there is one holder there by construction:
a person's key does not open `app` in production at all (see below), so what holds a deployed
slug is a key issued for a machine. The exception is a **dialled** door: a number exists once in
a world, so the sandbox number is the org's and a call at it rings in one terminal — web and
chat are each developer's own, the telephone is shared. WHICH terminal is asked in two steps
(`api/agents/doors.py`). First, **whose phone dialled**: a developer says which number they call
from (`PUT /v1/line/from`) and every call they make lands in their own corner — three of them can
test at once, and that is the answer for almost every ring. Then, for a number nobody claimed, the
agent's **line**: the first corner to hold it takes it, a second developer claims it, and it is
handed on when that terminal closes. Before either, the newest `pinecall run` silently took the
others' calls. Production needs none of it: one corner, and its line is nobody's.

**Except for the developer's own phone.** An org buys one number, and the line its customers
dial is the one a developer most needs to test on. So a phone call to a **production** number that
no dispatch aimed anywhere is asked about before it is built: the worker knocks at `GET
/v1/agents/{slug}/rings-for?caller=` (`worker/router.py`, `may_be_a_developers`), and when the
phone dialling is one a developer registered with `pinecall line from` while they hold that agent
in the sandbox, in that org, the call is built in **their sandbox corner** — their declaration,
their app socket, the keys asked for that corner, and a sandbox log whose context metadata says
`diverted_from: production`. Every other caller of the real number reaches production, and so does
this one whenever the question cannot be asked or is answered wrongly: a gateway that does not
answer leaves the call where it rang. A widget visit, an outbound call, an eval run and a sandbox
number are already where they were sent, and are never asked about.

**And the calls are listed by corner, not by org.** A call's head row keeps the world and the
holder it was opened in (`0024`), and `GET /v1/sessions` and `GET /v1/agents/{slug}/sessions` list
the reader's corner alone: a developer's sandbox test calls are theirs, a colleague's are the
colleague's, and the telephone's are production's. The Sessions screen used to show all three
together. Every row written before `0024` reads as production's, the org's own.

**An admin can open a developer's copy.** The corners are private by construction, and a
corner nobody can look into is one nobody can help with. So a key that sees every corner (`team`
— an admin's, the operator's) may send `pinecall-corner: <member id>` on any HTTP door, and that
request is answered in that member's sandbox corner (`auth/corner.py`, `in_the_corner_asked`): their
agents, their line, their calls, as the console draws them when an admin opens one. Only in the
sandbox — production has no corners to open — and only into an active member of the key's own
org; anything else is `403` in one sentence. Nothing is stored: the corner is that request's.

**And so does the data — twice.** A contact's facts and a knowledge base carry the world of the
key that pushed or the call that taught them (`0018`): a test call on a laptop never writes into
the memory a production call reads under the same number, and a `knowledge push` from that laptop
replaces the laptop's base and never the telephone's. Promoting knowledge is the same push made
with the box's key. And they carry WHOSE corner, exactly as the registry does (`0021`): before it,
the sandbox was one pile shared by the team, so one developer's push replaced what the other two
were testing against and one test call's extracted fact arrived in another's. The org's own corner
is the empty string and not NULL, because it is part of a key and a NULL in one matches nothing —
production is always the org's, and so is anything a sandbox key naming nobody wrote.

Knowledge **falls back** and memory does not, and the difference is what each one is. A base is
something somebody wrote down for the agent to read, so a developer who has pushed none still
reads the org's, the way `Registry.of()` falls back to the org's corner: nobody joins a team to an
empty knowledge base. A push and a drop never fall back — they are about one copy, and a laptop's
drop must not take the telephone's base. A contact's facts are what a CALL learned, and there is
no org-wide sandbox call to inherit from: they are the corner's, or nothing. The counts behind `memory_facts` and `knowledge_chunks` read both worlds, because a
row a laptop wrote is a row on the same disk. `agent.registered` and `call.started` carry `env`, so a console and a session
list can say which world they are reading. A login keeps a sandbox key — a laptop is where things
are written — and every key issued before the field existed is production's.

```bash
pinecall-runtime keys issue --org clinica --label "berna's laptop" --env sandbox
pinecall-runtime routes add +34910000000 clinica-norte --org clinica   # production, the default
```

The key also knows **what** — `scopes`, the doors as they are grouped — and **who** — `subject`
and `name`, the member it was minted for. A key issued with nothing said holds every scope, which
is what an org's own machine key means; a person's key holds what their role presets.

## People

A person of an org is a **member**, not a shared key: invited with a one-use link (`POST
/v1/members` on the org's own key — or `POST /v1/ops/orgs/{org}/members`, which is how the box
gives an org its first admin where sign-ups are shut), active once they chose a password on the
card that link opens (`POST /v1/invitations/{token}`), and holding keys of their own from then on — one per device, minted at `POST /v1/login` with the
scopes of their role (`qa` · `supervisor` · `manager` · `admin` · `developer`) and their member id
as `subject`. **In production a person's key never holds `app`**: a deployed agent is held by a key
issued for a machine (`POST /v1/keys`, or `keys issue --scope app`), not by whoever is logged in. Disabling them keeps the row, revokes every key of theirs and refuses their login. A
browser never carries a key in a URL: a key holder mints a one-use code (`POST /v1/login/codes`)
and the browser spends it for a key of its own.

**A person is their email, and may belong to several orgs.** The email is kept trimmed and
lower-cased, and it has one password on the box: each org is a row of theirs carrying the same
hash, and accepting an invitation sets it on every row that has one. So inviting somebody who
already has a password here seats them `active` at once — no link, nothing to accept — and the
operator's `orgs invite` says so instead of printing one. A login that names no org lands in the
oldest org that has not disabled them; the console's org switch lists the rest
(`GET /v1/login/orgs`) and mints the same person's key in the one picked (`POST /v1/login/org`),
in the same world. A machine's key names nobody and opens its one org.

**And a terminal never carries a password.** `pinecall login` holds no key, and the person at it
has none to paste — a key is minted FOR a person and kept BY the browser that minted it, never
shown. So the two meet at a word: the terminal opens a pairing (`POST /v1/login/pairings`, no
key), prints `<gateway>/cli?c=<code>`, and polls; the browser holding the person's key reads what
it is approving and approves it; the terminal collects a key of its own, once. Ten minutes, one
collection, and nothing but a dead word ever sits in a shell history. It is also why SSO later
touches none of this: the terminal's half knows nothing about how the person proved who they are.

Which door each scope opens, and every refusal in the words it is said in:
[protocol/people.md](protocol/people.md) and [protocol/gateway-api.md](protocol/gateway-api.md).

**One key per place, not one per tenant.** Issue a key for the laptop, one for CI, one for each
deployment, each with a `--label` — a key you can revoke on its own is a key you will revoke.

## Taking it back

The tenant does this itself, on a key that opens `keys` — the Keys screen of its console, or:

```bash
pinecall keys list                               # fingerprints, labels, worlds, whose, revoked
pinecall keys revoke <fingerprint>               # stops being honoured from the next request
```

The operator can too, for a tenant who asked, and is the only one who can remove the org:

```bash
pinecall-runtime keys list --org clinica
pinecall-runtime keys revoke <fingerprint>
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
that does. It opens `/v1/ops/*` and no tenant door: an ops key cannot read a call. A person the
box made an operator crosses them as themselves: their own key opens `/v1/ops/*` too, and the
console's org switch lets them into any org (`POST /v1/login/org`) on a key whose `subject` is
`operator:<email>` — no member row there, no seat, and every dial or verb of theirs attributable
by address in that tenant's own log.

## Provider keys, per tenant

By default every call runs on the **box's** vendor keys, out of its environment. An org may bring
its own, and then every call of that org runs on its account from the next one:

```bash
# the operator, for a tenant who sent theirs
printf %s "$KEY" | pinecall-runtime orgs provider-key set clinica elevenlabs
pinecall-runtime orgs provider-key list clinica

# or the tenant themselves, with their own org key (the `providers` scope)
pinecall providers add elevenlabs   # reads the key from stdin, never from a flag
```

The rows are encrypted with `PINECALL_VAULT_KEY`, which lives in the box's environment and never in
the database. A runtime without one cannot keep somebody else's secret and says so with a 503 —
[the gateway API §6](protocol/provider-keys.md).

## Quotas

```bash
pinecall-runtime orgs quota clinica --minutes 2000 --messages 5000 --agents 5 \
                                    --concurrent-calls 10 --memory-facts 50000 \
                                    --knowledge-chunks 20000 --numbers 1 --seats 10
```

The whole set is replaced at once, and a limit left out is **no limit**. The meter is a fold over
the log — there is no counter table to drift — and the gate runs before a call opens, before an
agent registers, before memory writes a fact, and before an invitation makes a row: `seats` is
what a plan sells a team by, counted as everybody the org has not disabled. A tenant over one is refused with a sentence and
`credits.exhausted` in their own log; nothing is cut mid-call.

## Where each thing is written

| | |
|---|---|
| `orgs` | id, slug, name. `default` is seeded by the migrations |
| `quotas` | one row per org, the whole set of eight replaced at once |
| `api_keys` | sha256 fingerprint, org, label, `env`, `scopes`, `subject`, `name`, created_at, revoked_at. **Never the key**, and a revoked row is kept |
| `members` | one person of one org: email (trimmed, lower-cased, unique per org), name, `role`, `agents`, `status`, the argon2id hash — the same hash on every row of that email. A disabled row stays |
| `invitations` | the sha256 of a one-use token, whose it is, when it expires, when it was spent |
| `routes` | (org, number) → (agent, channel), plus `env` and `managed`. One number is one door |
| `carriers` | one per org: `twilio` or `sip`, the account it names, the credentials as Fernet ciphertext |
| `provider_keys` | one row per (org, vendor), Fernet ciphertext under `PINECALL_VAULT_KEY` |
| `call_log`, `call_log_head` | every entry, with the org that owns the call and, on a call's head row, the corner it was opened in: `env` and `holder`, the org's own being `''` (`0024`; older rows read as production's) |
| `contact_memories` | a contact's facts, per org **and world** — a test call's never reach production's |
| `knowledge_bases`, `knowledge_chunks` | a base per (org, `env`, name); a laptop's push never replaces the box's |
| `eval_runs`, `pipeline_overrides`, `tokens` | the suites run, the operator's knobs, the room tokens minted and spent |

A tenant is a row in `orgs` and at least one way in: a **person** (a row in `members`, invited and
then holding keys of their own) or a **machine** (a row in `api_keys`). Everything else follows
from the key whoever knocks is carrying.

## An admin opening a colleague's copy

`pinecall-corner: <member id>`
on any HTTP door that takes a key — the scoped doors and every read of a log — answers that request
in that member's sandbox corner instead of the key's own (`auth/corner.py`): their agents, their
line, their calls. It is what an admin's own console — `pinecall serve`, on their machine, which
forwards the header as the page sent it — sends when they open a developer's copy; the gateway's
console shows production, where there is one corner and nothing to open. Only a key
that sees every corner may send it (`team`), only in the sandbox — production has one corner — and
only naming an active member of the key's own org; otherwise `403 only a key that sees every corner
opens a colleague's, and only in the sandbox`, or `403 no active member of this org answers to that
corner`. A header naming the key's own person is the key's own corner. The sockets do not read it:
`WS /v1/apps` and `WS /v1/chat` always work in the key's own corner.
