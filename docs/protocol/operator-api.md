# The operator API — `/v1/ops/*`

The doors an operator opens on a runtime: the ones that change what the box does, as opposed to the
ones a worker, an app or a browser uses. It is a **public contract**. Everything needed to
self-host is in this repo; everything needed to charge for it lives outside, in `pinecall/cloud`,
and that half only ever reaches a runtime through this API. Nothing here prices anything, and
nothing here is private.

## Authentication

One key, out of the environment: `PINECALL_OPS_KEY`, sent as `Authorization: Bearer <key>`.

It is the **box's** key, not an org's, so every door here names its org explicitly. An API key
(the kind an app or a worker holds) does not open these doors, and the ops key does not open
theirs. An unset `PINECALL_OPS_KEY` closes `/v1/ops/*` entirely, which is the safe default: a
runtime that was never given one cannot be operated remotely at all.

A wrong or missing key is `401` with `WWW-Authenticate: Bearer` and nothing about why.

## Routes

A number is a route to an agent. A row here outranks whatever a running app declares for the same
door — see `docs/decisions/routes.md` for the order and the reason. Changes take effect on the next
call: the gateway reads the table on every request and the worker asks before every job. Nothing is
restarted, and nothing is deployed.

### `GET /v1/ops/routes?org=<id or slug>`

Every door the org answers right now, in the order the worker is given them, each saying which
table put it there.

```json
[
  { "route": { "org": "default", "agent": "tienda-sur", "channel": "phone",
               "number": "+59829000000", "label": null },
    "source": "operator" },
  { "route": { "org": "default", "agent": "clinica-norte", "channel": "web",
               "number": null, "label": null },
    "source": "app" }
]
```

`source` is `operator` for a row in this table and `app` for a door a connected socket declared.

### `POST /v1/ops/routes`

Add a number, or move one. The body is one route:

```json
{ "org": "default", "number": "+59829000000", "agent": "tienda-sur", "channel": "phone" }
```

`org` is an id or a slug; the stored row names the id.

`channel` is `phone`, `whatsapp` or `web`; `number` is E.164 (`+` and up to fifteen digits, the
first never zero) and is required — a widget answers at no number, and a `web` body is refused for
that reason. A number that already has a row is **moved**, never doubled.

```json
{ "route": { "org": "default", "agent": "tienda-sur", "channel": "phone",
             "number": "+59829000000", "label": null },
  "overrides": "clinica-norte" }
```

`overrides` names the agent that had declared the same door and no longer answers it, or `null`
when the number took nothing from anybody.

`400` with the reason in `detail` for a number or a channel the domain refuses.

### `DELETE /v1/ops/routes/{number}?org=<id or slug>`

Forget the number. `204` when a row went; `404` when no row answered to it — a typo in `routes rm`
must never read as done. Whatever a running app declares for that number answers again from the
next call.

## Orgs

The tenants. An org is a row: a minted **id** every other row names it by, the **slug** people type,
and a name. Every door here takes the org by id or by slug, and the `default` org is the first row
of every runtime. See `docs/decisions/orgs.md`.

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

One org, with the quotas set on it. `null` is no limit.

```json
{ "id": "org_3f2a9c1b8d0e", "slug": "clinica-norte", "name": "Clínica Norte",
  "quotas": { "minutes": 1000, "messages": null, "agents": 5, "concurrent_calls": 10 } }
```

### `DELETE /v1/ops/orgs/{org}`

Forget the org and its quotas. `204` when the row went; `409` while a live key or a route still
names it — revoke the keys and remove the numbers first, in this same API — and `404` for an org
nobody typed.

## Quotas

What an org may consume. The runtime holds the **mechanism**; whoever charges sets the numbers. A
self-hosted box never sets any, and an org nobody limited has no limits.

### `PUT /v1/ops/orgs/{org}/quotas`

The whole set, replaced: a limit left out is no limit. Zero is a real limit and refuses everything.

```json
{ "minutes": 1000, "agents": 5, "concurrent_calls": 10 }
```

`minutes` is minutes of call, summed from every `call.summary` in the org's logs; `messages` is
turns, both sides, the same way; `agents` is how many agents the org's sockets may hold at once;
`concurrent_calls` is how many of its calls may be open on this gateway at once. The answer is the
set as kept. A limit bites the **next** call and the next register: the gateway refuses with a
`credits.exhausted` entry in the agent's own log and a `429` whose `detail` is the same sentence —

```
org clinica-norte has used 1000 of its 1000 minutes: credits.exhausted
```

— on `POST /v1/calls`, on `POST /v1/tokens` (before the browser joins), on the chat socket (as the
close reason) and on `agent.register` (as the `error` frame that follows the entry).

## Keys

An API key is what a worker and a tenant's app knock at the runtime's own doors with — `GET
/v1/routes`, the app socket, the log. It **is** the org: every door reads the org off the key and
none takes one from a parameter. It is **not** the ops key: the ops key is the box's and opens only
`/v1/ops/*`; an API key is the tenant's and opens none of them. See `docs/decisions/keys.md`.

The table stores `sha256(key)` and never the key. Nothing here, and nothing anywhere else in the
runtime, reads a key back: the response to `POST /v1/ops/orgs/{org}/keys` is the only place a key
is ever carried in the clear, and it is carried once.

### `POST /v1/ops/orgs/{org}/keys`

Issue a key for the org. The body says what it is for:

```json
{ "label": "the worker on this box" }
```

`label` defaults to `null`. The answer carries the key, once:

```json
{ "key": "pk_yT3…", "key_id": "k_9f2c4a1b8d0e6f37",
  "org": "org_3f2a9c1b8d0e", "label": "the worker on this box" }
```

Copy it. There is no verb that shows it again, and no recovery path — a lost key is revoked and
another is issued.

### `GET /v1/ops/orgs/{org}/keys`

Every key of the org, oldest first, revoked ones included and named as revoked. Fingerprints,
never keys:

```json
[
  { "fingerprint": "3f2a…", "org": "org_3f2a9c1b8d0e",
    "label": "issued by migrate up", "created_at": "2026-09-07T14:02:11+00:00",
    "revoked_at": null }
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
with no row, it runs on the box's own `ANTHROPIC_API_KEY`, `SONIOX_API_KEY` and the rest, exactly
as every call did before this existed. That is the whole of managed versus BYOK, and nothing here
prices anything. See `docs/decisions/provider-keys.md`.

The vendor is one of `anthropic`, `deepgram`, `elevenlabs`, `openai`, `soniox` — the vendor files
this build has — and anything else is `400` with that list in `detail`.

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

Forget it: the org goes back to the box's own key for that vendor on its next call. `204` when a
row went; `404` when the org had no key for that vendor — a typo in `provider-key rm` must never
read as done.

### `GET /v1/ops/orgs/{org}/provider-keys`

Which vendors the org brought a key for. **Names only** — never a value, and not a prefix.

```json
{ "vendors": ["elevenlabs"] }
```

Nothing in this API, and nothing anywhere else in the runtime but the worker's own
`GET /v1/agents/{slug}/provider-keys`, reads a stored provider key back.

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

## The CLI over the same doors

```
pinecall-runtime orgs list
pinecall-runtime orgs add <slug> [--name <text>]
pinecall-runtime orgs rm <org>
pinecall-runtime orgs quota <org> [--minutes N] [--messages N] [--agents N] [--concurrent-calls N]
pinecall-runtime orgs provider-key set  <org> <vendor>   # the key is read from stdin, one line
pinecall-runtime orgs provider-key rm   <org> <vendor>
pinecall-runtime orgs provider-key list <org>

pinecall-runtime routes list [--org default]
pinecall-runtime routes add <number> <agent> [--channel phone] [--org default]
pinecall-runtime routes rm  <number> [--org default]
pinecall-runtime routes seed [--file infra/seed/routes.json]

pinecall-runtime keys issue  [--org default] [--label <text>]
pinecall-runtime keys list   [--org default]
pinecall-runtime keys revoke <fingerprint>
```

`orgs provider-key set` reads the key from **stdin** and never from a flag: argv is visible in
`ps` to every user on the box, and a key pasted as an argument is a key in a shell history. It
prints which vendor the org now runs on its own key, and never the key.

`--org` takes an id or a slug and defaults to `default`. `keys issue` prints **the key alone on the
first line**, so a script reads it with `head -1`, and the org, the label and the warning
underneath. `pinecall-runtime migrate up` prints one the same way on a database whose `default` org
has none — that, and nothing else, is what creates the first key on a fresh box.

It reads `PINECALL_GATEWAY_URL` and `PINECALL_OPS_KEY` and speaks nothing but this API. `seed` takes
a JSON array whose elements are `POST` bodies, applied in order.


## An agent's pipeline — `/v1/agents/{slug}/pipeline`

In this file because they are the same public contract — everything needed to self-host is here —
but they are **not** the operator API and they take a different key: the org's own API key, as
`Authorization: Bearer <key>`, on the one bearer parser every other door uses. The ops key does not
open them. They belong to the AGENT, so a tenant's own dashboard opens them as readily as ours
would — any UI is one client of them; `docs/decisions/pipeline.md` argues the shape.

### `GET /v1/agents/{slug}/pipeline`

What one agent hears, decides and speaks with right now, what those three have cost it, and what an
operator has already turned. `404` when no app is holding the agent, in the same words the worker's
config door answers with — a pipeline is a live thing and there is no empty one to show.

```json
{ "agent": "clinica-norte",
  "hears":   { "vendor": "soniox", "model": null, "voice_id": null, "language": "es" },
  "decides": { "vendor": "anthropic", "model": "claude-haiku-4-5", "voice_id": null, "language": null },
  "speaks":  { "vendor": "elevenlabs", "model": null, "voice_id": "a-declared-voice", "language": "es" },
  "greeting": "Clínica Norte, buenas.",
  "overrides": { "voice": null, "tts_model": null, "stt": null, "llm": null, "greeting": null },
  "calls": 12,
  "medians": [ { "name": "transcription_delay", "seconds": 0.13, "turns": 41 },
               { "name": "e2e_latency", "seconds": 0.94, "turns": 39 } ],
  "unavailable_reasons": { "speaks": "elevenlabs has no API key in this process" } }
```

Each stage names the vendor a session **would** be built with, which is what the agent declared or
this build's default for that modality — never a guess. `medians` is `log/latencies.py` over every
turn of the agent's last calls together: livekit's own field names, in the order a turn happens,
the median and not the mean, and a measure nobody measured has no row rather than a zero. `calls`
is how many logs it read. `unavailable_reasons` names a stage whose vendor has no API key in this
process, so a screen can say so before the line goes dead instead of after.

### `PUT /v1/agents/{slug}/pipeline/overrides`

Turn one or more of the five knobs. They are applied on the agent's **next session**, through
`GET /v1/agents/{slug}/config` — the one door an agent's config has ever reached a worker by — so
nothing is restarted and nothing is deployed.

```json
{ "voice": "a-voice-an-operator-chose", "llm": "anthropic/claude-sonnet-4-5" }
```

`voice` is the voice id that speaks · `tts_model` the model it speaks with · `stt` and `llm` take
`vendor/model`, or a model alone to keep whichever vendor is already in use · `greeting` the first
thing said on the next call.

The body is the **whole** set: a knob left out stops being overridden and goes back to what the app
declared. A knob that is present but **blank is refused** with `400` and the sentence that says
why — an empty voice once reached the vendor and a whole line of calls went out silent. A `tts`
model this build will not run is refused too, in the words that name the one it runs instead, and
so is a vendor this build has no file for.

The answer is the same report `GET` gives, so a screen redraws from what the gateway now holds.
