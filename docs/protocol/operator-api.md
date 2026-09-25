# The operator API — `/v1/ops/*`

The doors an operator opens on a runtime: the ones that change what the box does, as opposed to the
ones a worker, an app or a browser uses. It is a **public contract**: everything needed to self-host
is in this repo; everything needed to charge for it lives outside, in `pinecall/cloud`, and only
ever reaches a runtime through this API. Nothing here prices anything, and nothing here is private.
The model underneath — what an org is, what a key IS, how a tenant is given one — is
[../multi-tenancy.md](../multi-tenancy.md), and the verbs are [../the-runtime-cli.md](../the-runtime-cli.md). What the operator configures about the **box itself** — its mail, its brand, a box-wide "Continue with Google" — is [the-box.md](the-box.md); every org's floor on one stream is [the-boxs-floor.md](the-boxs-floor.md).

## Authentication

Two things open these doors, sent as `Authorization: Bearer <key>`, and neither is an org's admin:

- **The box's own key**, `PINECALL_OPS_KEY`, out of the environment. It belongs to no org and
  carries no name.
- **The key of a person the box made an operator** — `pinecall-runtime orgs operator <org> <email>`,
  or the first person `init` makes (`PUT /v1/ops/orgs/{org}/members/{id}/operator`, migration 0020).
  The flag is on their member row, never the key, and is read on every request: any key of theirs,
  in any org of theirs, opens `/v1/ops/*` as well as their org's own doors, while an active row of
  their address carries the flag. `--revoke`, disabling or removing the member stops it on the next
  request. A server's token names nobody and never opens these doors, whatever else it opens.

`GET /v1/ops/whoami` answers `{operator: true, version, domain, name, org}` and is what the
console's **Box** screens — served by the same gateway, to a person the box made an operator —
prove their credential at before they draw, as the console proves a person's at `/v1/whoami`. `name` and `org` are the person's when a
person's key knocked and null for the box's own; `domain` is null on a box that was told none, and
the page then says the host it was loaded from. Every door here names its org explicitly, because
neither credential is an org's. An app's or a worker's API key does not open these doors, nor the
ops key theirs. With `PINECALL_OPS_KEY` unset, only a person made an operator opens `/v1/ops/*` —
and on a runtime that was never given one nobody was, since `init` knocks with that key — so the
doors are closed. A wrong or missing credential is `401` with `WWW-Authenticate: Bearer` and one
sentence: `this door is the box's: its operator key, or a person the box made an operator`.

## Routes

A number is a route to an agent. A row here outranks whatever a running app declares for the same
door — the *routes* decision page in the maintainer's notebook has the order and the reason. Changes take effect on the next
call: the gateway reads the table on every request and the worker asks before every job. Nothing is
restarted, and nothing is deployed.

### `GET /v1/ops/routes?org=<id or slug>`

Every door the org answers now, in the worker's order. A door is a row in this table and there is
no other kind: a class declares none, and the widget is not a door — every agent is on the web.

```json
[
  { "org": "default", "agent": "tienda-sur", "channel": "phone",
    "number": "+59829000000", "label": null, "env": "production", "managed": false }
]
```

`?env=` names the world, `production` when left out; the worker's `GET /v1/routes` answers its
key's, in the same shape.

### `POST /v1/ops/routes`

Add a number, or move one. The body is one route:

```json
{ "org": "default", "number": "+59829000000", "agent": "tienda-sur", "channel": "phone",
  "env": "production" }
```

`org` is an id or a slug; the stored row names the id. `env` is the world the number answers in,
`production` when left out; a row is still one per `(org, number)`, so a number moves between worlds
as it moves between agents. `channel` is `phone`, `whatsapp` or `web`; `number` is E.164 (`+` and up
to fifteen digits, the first never zero) and is required — a widget answers at no number, and a
`web` body is refused for that reason. A number that already has a row is **moved**, never doubled.
```json
{ "route": { "org": "default", "agent": "tienda-sur", "channel": "phone",
             "number": "+59829000000", "label": null, "env": "production", "managed": false },
  "overrides": "clinica-norte" }
```

`overrides` names the agent that declared the same door and lost it, `null` if nobody did. `400`
with the reason in `detail` for a number or a channel the domain refuses.
### `DELETE /v1/ops/routes/{number}?org=<id or slug>`

Forget the number. `204` when a row went, `404` when none did — a typo in `routes rm` must never
read as done. What a running app declares for that number answers again from the next call.

Four doors touch a tenant's people, and they are the only four. `GET /v1/ops/orgs/{named}/members` is the org's people as the operator reads them — `{members: [...], seated}`. `POST /v1/ops/orgs/{named}/members {email, name, role, agents?, production?}` **invites** one — the org's first admin where sign-ups are shut, or one more — and answers `201` with the row and a one-use `token`, exactly as the tenant's own invite does; it takes none of the org's seats, because a plan caps what an org seats by itself. An email that already has a password on this box — a person of another org — gets no token: the row is `active` from the start, with that password, and `token` and `expires_at` are null ([people.md](people.md)). `PUT /v1/ops/orgs/{named}/members/{id}/operator {operator}` makes that member an operator of the box, or stops: their own key opens `/v1/ops/*` as well as their org's doors, and nothing about their org changes — a person is their email, so it is their key in ANY org of theirs, and the console's org switch then lists every org of the box and lets them into one as `operator:<email>`, on a production key with an admin's scopes and no member row ([people.md](people.md)); taking the flag back stops those keys on their next request; `404` for an id no member of the org answers to. `DELETE /v1/ops/orgs/{named}/members/{id}` removes one **for good** — `204`, every key of theirs revoked first, the row and its open links gone, the seat free — under the tenant door's own rules less "yourself": `409` for the org's last active admin, `404` for a stranger. There is no door here that changes a member's role or standing: an invitation is inert until the person it names accepts it with a password of their own, so the box can seat somebody and never be them, while a role changed from here would be the box editing a tenant's team. Changing and disabling are the tenant's own `/v1/members` ([people.md](people.md)).

## Orgs

The tenants. An org is a row: a minted **id** every other row names it by, the **slug** people type,
and a name. Every door here takes the org by id or by slug, and the `default` org is the first row
of every runtime. The *orgs* decision page in the maintainer's notebook says why.

### `GET /v1/ops/orgs`

Every org, oldest first.

```json
[ { "id": "default", "slug": "default", "name": "default" },
  { "id": "org_3f2a9c1b8d0e", "slug": "clinica-norte", "name": "Clínica Norte" } ]
```

### `POST /v1/ops/orgs`

A new tenant. `slug` is lowercase letters, digits and dashes; `name` defaults to it.

```json
{ "slug": "clinica-norte", "name": "Clínica Norte" }
```

The answer is the row, with the id this runtime minted. `409` when the slug is somebody's, `400`
with the reason for a slug that is not one.

### `GET /v1/ops/orgs/{org}`

One org, with its quotas and what it holds against the ones that are stocks; `null` is no limit.

```json
{ "id": "org_3f2a9c1b8d0e", "slug": "clinica-norte", "name": "Clínica Norte",
  "quotas": { "minutes": 1000, "messages": null, "agents": 5, "concurrent_calls": 10,
              "memory_facts": 5000, "knowledge_chunks": 2000, "numbers": 1, "seats": 10,
              "budget_eur": 300 },
  "dialling": { "dial_anywhere": false, "per_minute": 6, "per_day": 200,
                "max_duration_s": 600 },
  "holding": { "memory_facts": 412, "knowledge_chunks": 1860, "numbers": 1, "seats": 4 } }
```

`dialling` is what the org may dial out (below), the code's defaults where none was set. `holding`
is counted now by one indexed query (`0` with no database) — here, not on `/v1/ops/usage`, which
folds the log: a row there is an event at a cursor, and a stock has no cursor.

### `DELETE /v1/ops/orgs/{org}`

Forget the org and its quotas: `204` when the row went; `409` while a live key or route names it
(revoke the keys and remove the numbers first, in this API); `404` for an org nobody typed.

### `PUT /v1/ops/orgs/{org}/agents`

An agent that registered into the wrong org, moved into this one: its log, its calls and the numbers
that answer for it. The body names it, `{ "agent": "tienda-sur" }`; the answer says what moved:

```json
{ "agent": "tienda-sur", "org": "clinica-norte", "logs": 14,
  "numbers": ["+59829000000"], "stayed": [] }
```

`logs` is how many logs moved, the agent's own and one per call; `stayed` names a number left
where it was because this org already answers at it. `409` while any socket holds the agent — stop
it, move it, start it again — and `404` for a slug that has never written a log here. The CLI is `pinecall-runtime orgs move <agent> <org>`.

## Quotas

What an org may consume, and what it may keep. The runtime holds the **mechanism**; whoever charges
sets the numbers. A self-hosted box never sets any, and an org nobody limited has no limits.

### `PUT /v1/ops/orgs/{org}/quotas`

The whole set, replaced: a limit left out is no limit. Zero is a real limit and refuses everything.

```json
{ "minutes": 1000, "agents": 5, "concurrent_calls": 10, "llm_tokens": 2000000,
  "memory_facts": 5000, "knowledge_chunks": 2000, "numbers": 1, "seats": 10, "budget_eur": 300,
  "lends": ["deepgram", "cartesia", "anthropic/claude-haiku-4-5"] }
```

`lends`: the box's vendor keys the org may run on where it brought none — `null` all, `[]` none,
else vendors and `vendor/model` prefixes; an unknown vendor is `400` ([limits.md](../limits.md)).

`budget_eur` rides the same body and is not a quota: whole euros a calendar month, both worlds,
shown beside what was spent (`GET /v1/insights`, [console-api.md](console-api.md)); nothing is refused over it. Five of them are **flows** — what the org has consumed, or holds open right now. `minutes` is
minutes of call, summed from every `call.summary` in the org's logs; `messages` is turns, both
sides, and `llm_tokens` tokens in and out, the same way; `agents` how many agents it may hold;
`concurrent_calls` is how many of its calls may be open on this gateway at once. The answer is the
set as kept. A limit bites the **next** call and the next register: the gateway refuses with a
`credits.exhausted` entry in the agent's own log and a `429` whose `detail` is the same sentence —

```
org clinica-norte has used 1000 of its 1000 minutes: credits.exhausted
```

— on `POST /v1/calls`, on `POST /v1/tokens` (before the browser joins), on the chat socket (as the
close reason, and before every turn: [limits.md](../limits.md#quotas)) and on `agent.register` (the `error` frame after the entry).

Four are **stocks** — how much of a table the org may keep standing: `memory_facts`, the facts memory holds about its contacts, all together (a superseded one is history and is not counted); `knowledge_chunks`, the chunks its bases hold, all together; `numbers`, the ones the box bought for it on its own carrier account; and `seats`, the people it holds — invited and active together, because an invitation sent is a seat taken, and a `disabled` member keeps their row and holds none. Same mechanism, and it is what a plan switches memory and retrieval off with: `null` is no limit, a number is a cap, and **`0` is how a plan that does not include the feature is expressed** — a `0` org keeps neither, and its `recall` and `search` tools find nothing, embed nothing and write no entry at all: a plan without a feature is not a failure and must not read as one.

- **`PUT /v1/knowledge/{base}`** counts what the push would become — the org's other bases plus the
  chunks these files cut into, the base being replaced counted as freed — and answers `429` before a
  row is written, since a push is all or nothing: `org clinica-norte has used 2400 of its 2000
  knowledge_chunks: credits.exhausted`. It is the one refusal here that writes **no**
  `credits.exhausted` entry — a push names no agent and opens no call, so the org has no log for it,
  and the tenant is reading the 429. No quota refuses the list or the drop.
- **`POST /v1/members`** counts the people the org already seats and answers `429` in the same shape
  — `org clinica-norte has used 3 of its 3 seats: credits.exhausted` — writing no entry, for the
  same reason a push writes none. A seat is charged only where a ROW will be made: an email the org
  already holds is a member who accepted (refused `409`) or one still invited, whose seat was taken
  by the first invitation, so re-sending a link is never the thing a full org cannot do.
- **`remember` at hang-up** reads the cap before asking a model, so an org that may keep no more
  facts pays for no extraction: nothing written, `memory.ops` carries an op that kept nothing, and
  `credits.exhausted` goes into the agent's own log as every quota refusal does; like minutes, the
  cap bites the NEXT hang-up.
- **A contact's memory is read and erased at every quota, `0` included**: erasing is a right, not a
  feature. And a cap is about what is KEPT, so an org at its cap still recalls all of it.

**`numbers`** caps what the box may BUY for the org on its own carrier account (`POST
/v1/numbers/buy`, [numbers](numbers.md)), counted on its bought routes (`managed`); one the tenant
imports from its own carrier counts against nothing. `0` buys none, refused with the same `429`
sentence before the carrier is asked, with no `credits.exhausted` entry: a purchase opens no call.

## What an org may dial

### `PUT /v1/ops/orgs/{org}/dialling`

The org's outbound guards, replaced whole — but a guard left out goes back to the code's **default**
and never to "no limit": no org may dial without a fence, the one way this differs from the quotas
above. It is the operator's, not the tenant's, deliberately and unlike `PUT /v1/org/judging`, which
an org turns for itself: an org that could lift its own dialling fence has none.

```json
{ "dial_anywhere": false, "per_minute": 6, "per_day": 200, "max_duration_s": 600 }
```

What each guard refuses, and its status, is [console-api.md](console-api.md) §4, beside the door that places a call. Two are worth naming here. `dial_anywhere` is the one switch that turns a call-back box into one that can dial strangers — off, a destination must already have called or written to one of the org's agents, and "call back" means back. Which countries an org may reach is its carrier account's own setting (Twilio's geo permissions), and never a guard here. `max_duration_s` rides in the dispatch and is enforced by the media plane, so a worker that crashed leaves no call running on somebody's bill. It is the org's ceiling on a dialled call; each agent has its own, on every voice call, set by the org (settings-api.md).
A count below zero is `400` with the reason. The answer is the policy as kept, and it bites the next dial. The CLI over this door is `pinecall-runtime orgs dialling <org> [--dial-anywhere/--no-dial-anywhere] [--per-minute N] [--per-day N] [--max-duration-s N]`.

## Keys

An API key is what a worker and a tenant's app knock at the runtime's own doors with — `GET
/v1/routes`, the app socket, the log. It **is** the org: every door reads the org off the key, none
from a parameter. It is **not** the ops key, the box's, which opens only `/v1/ops/*`; an API key is
the tenant's and opens none of them — unless it is the key of a person the box made an operator
([Authentication](#authentication)). The *keys* decision page in the maintainer's notebook argues
the split. `env` is the world it opens: a server's token's own; a person's key is stored `sandbox` and each request names its world with `pinecall-env`, production only while their member row opens it (0039). That world's are
the agents registered on it, the doors they claim and every call they take are that world's, the
registry and the routes are namespaced by it — and the registry again, in the sandbox, by the member
the key names, so two developers of one tenant hold their own — and a number claimed in one world is
refused to a request in the other, naming the world that holds it. `scopes` is what it may do there, as
the doors are grouped (`app` · `calls` · `talk` · `supervise` · `pipeline` · `knowledge` · `memory`
· `evals` · `numbers` · `keys` · `providers` · `team` · `usage`, and `fleet`, the box's own worker's
— its doors resolve by the call's corner, and it is minted only when named); `subject` and `name`
say whose it is, if a person's. An org makes its servers' tokens at `POST /v1/keys` without any of
this. A key issued before the fields existed is production's, with every scope.

The table stores `sha256(key)`, never the key, and nothing in the runtime reads a key back: the
answer to `POST /v1/ops/orgs/{org}/keys` is the only place one is carried in the clear, once.

### `POST /v1/ops/orgs/{org}/keys`

Issue a key for the org. The body says what it is for, where it opens, what it may do, whose it is;
every field is optional — `env` defaults to `production`, `scopes` left out is every scope but
`fleet`, the rest to `null` — and a world or a scope nobody declared is `400` with the reason in `detail`:

```json
{ "label": "berna's laptop", "env": "sandbox",
  "scopes": ["calls", "talk"], "subject": "m_1", "name": "Berna" }
```

The answer carries the key, once — `pc_` when it names a person, else `pc_live_`/`pc_test_` by its world — with the record it was written under. Copy it: no verb shows it
again and there is no recovery path — a lost key is revoked and another is issued:

```json
{ "key": "pc_yT3…", "key_id": "k_9f2c4a1b8d0e6f37", "org": "org_3f2a9c1b8d0e",
  "label": "berna's laptop", "env": "sandbox", "scopes": ["calls", "talk"],
  "subject": "m_1", "name": "Berna" }
```

### `GET /v1/ops/orgs/{org}/keys`

Every key of the org, oldest first, revoked ones included and named so. Fingerprints, never keys:

```json
[
  { "fingerprint": "3f2a…", "org": "org_3f2a9c1b8d0e", "label": "the worker on this box",
    "created_at": "2026-09-07T14:02:11+00:00", "revoked_at": null,
    "env": "production", "scopes": ["app", "calls", "…", "usage"], "subject": null, "name": null }
]
```

### `POST /v1/ops/keys/{fingerprint}/revoke`

Stop honouring one key from the next request. A **POST and not a DELETE**, because nothing is
deleted: the row stays and grows a `revoked_at`, so the log entries that name the key stay
readable. It names no org: a fingerprint already names one row.

```json
{ "fingerprint": "3f2a…", "revoked": true }
```

`404` when no live key answers to that fingerprint — an unknown one, or one already revoked. A typo
in `keys revoke` must never read as done.

## Provider keys

An org may bring its own key for a vendor. A call of that org then runs that vendor with that key;
with no row, it runs on the box's own `ANTHROPIC_API_KEY`, `SONIOX_API_KEY` and the rest, as every
call did before this existed. That is the whole of managed versus BYOK, and nothing here prices
anything. The *provider-keys* decision page in the maintainer's notebook says why. The vendor is any
this build reaches with a key — `pinecall-runtime providers` lists them, and an alias (`11labs`) is
stored under the vendor's own name — anything else is `400` with that list in `detail`.

The row holds a Fernet token under the box's own `PINECALL_VAULT_KEY`, never the key. A runtime
that was given no vault key answers every door here with

```
503 no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key
```

and every call of every org on it runs on the box's own vendor keys, which is a complete install.

### `PUT /v1/ops/orgs/{org}/provider-keys/{vendor}`

The org's own key for one vendor, from its next call on. Replaces whatever it had for that vendor.

```json
{ "key": "sk_yT3…" }
```

`204`, with no body. `404` for an org nobody typed, `400` for a vendor this build does not run.

### `DELETE /v1/ops/orgs/{org}/provider-keys/{vendor}`

Forget it: the org's next call goes back to the box's own key for that vendor. `204` when a row
went; `404` when the org had none for it — a typo in `provider-key rm` must never read as done.

### `GET /v1/ops/orgs/{org}/provider-keys`

Which vendors the org brought a key for. **Names only** — never a value, and not a prefix.

```json
{ "vendors": ["elevenlabs"] }
```

Nothing in the runtime but the worker's own `GET /v1/agents/{slug}/provider-keys` reads a stored
provider key back. An org's OpenID client secret is sealed under the same vault key and read back by
nothing; the box's two doors over it — `GET /v1/ops/orgs/{org}/sso`, and the break-glass `PUT
…/sso/required {required}` that lets a password open that org again — are in [people.md](people.md).

## Usage

What every org consumed, as a **projection over the log** and never a table: the runtime folds
its own `call.summary` and `call.score` entries, one row each, and nothing else is metered. The
cursor is the store's position across every log, so a reader that saw a row never sees it twice.
Nothing here is a price: `cost_eur` is the provider's bill as the log knows it, informational.

### `GET /v1/ops/usage?after=<cursor>&org=<id or slug>&limit=<n>`

The metered rows above the cursor, oldest first, and the cursor to resume from. `org` keeps one
org's rows; every org's when absent. `after=0`, the default, reads from the start.

```json
{ "rows": [
    { "cursor": 4812, "org": "org_3f2a9c1b8d0e", "agent": "clinica-norte",
      "call": "CA_9f2c4a1b8d0e", "type": "call.summary", "at": 1788800000.5,
      "minutes": 1.5, "messages": 6, "input_tokens": 1200, "output_tokens": 300,
      "characters": 450, "judge_calls": 0, "cost_eur": 0.012 },
    { "cursor": 4813, "org": "org_3f2a9c1b8d0e", "agent": "clinica-norte",
      "call": "CA_9f2c4a1b8d0e", "type": "call.score", "at": 1788800001.2,
      "minutes": 0, "messages": 0, "input_tokens": 0, "output_tokens": 0,
      "characters": 0, "judge_calls": 2, "cost_eur": 0.001 } ],
  "totals": { "org_3f2a9c1b8d0e": { "minutes": 1.5, "messages": 6, "input_tokens": 1200,
              "output_tokens": 300, "characters": 450, "judge_calls": 2,
              "cost_eur": 0.013, "calls": 1 } },
  "next": 4813 }
```

`next` is the cursor of the last row **read**, filtered or not — a page whose every row was another
org's still moves it — and `null` when there was nothing above `after`: the end. Hand it back as
`after` to resume; the same cursor twice reads the same rows twice.
With `Accept: text/event-stream` the same rows arrive as SSE frames, `event: usage`, the cursor as
the `id`, followed by each new one as the log grows. A reconnecting `EventSource` resumes from its
`Last-Event-ID` by sending it as `after`.

## The fleet

Every `dev` or `start` worker heartbeats to the gateway every 5 s; the gateway keeps the roster
in memory, and these doors read it. [../scaling.md](../scaling.md) is the whole picture.

### `GET /v1/ops/fleet`

`{now, stale_after_s, workers: [{worker, active, max_jobs, load, draining, cordoned, seen_at}],
totals: {workers, active, seats, free, accepting, full, busy}}` — every worker ever heard from,
stale ones included so a reader sees when one went quiet; the totals count the ones heard in the
last `stale_after_s` (30 s). `max_jobs`
is null for a worker gated on CPU, which counts no seats. `full` is workers > 0 and accepting = 0.

### `POST /v1/ops/fleet/{worker}/cordon` · `DELETE …/cordon`

`204`: the worker learns on its next heartbeat, takes no new call, finishes what it holds and exits
3. `404` for a name nobody has knocked with, so a typo never reads as done.

## The CLI over the same doors

`pinecall-runtime orgs · keys · routes · fleet` speak nothing but this API, on `PINECALL_GATEWAY_URL`
with `PINECALL_OPS_KEY`; every verb, flag by flag, is [../the-runtime-cli.md](../the-runtime-cli.md).
Two rules worth repeating here: `orgs provider-key set` reads the key from **stdin** and never from
a flag, because argv is in `ps` and in a shell history; and `keys issue` prints **the key alone on
the first line** — a script reads it with `head -1` — and the org, the label and the warning under
it. `pinecall-runtime migrate up` mints nothing: on a database whose `default` org has no key it
says so and names `keys issue --org default`, which is the one verb that creates a key.

## An agent's pipeline

`GET /v1/agents/{slug}/pipeline` is the agent's own door, on the org's API key, and has a page of
its own: [pipeline-api.md](pipeline-api.md).
