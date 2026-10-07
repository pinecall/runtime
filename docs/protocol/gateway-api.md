# The gateway API

Every door a tenant's own code may knock at, and what comes back. The `pinecall` package speaks
exactly these doors, so an app written against this page in any language is a first-class client.
The operator's half is [operator-api.md](operator-api.md); who a key is, [../multi-tenancy.md](../multi-tenancy.md);
every door in one table, [every-door.md](every-door.md).

## The shape of it

One gateway serves both worlds, production and the sandbox, at `/v1`, and beside it the console at
`/` and the widget at `/widget/pinecall-widget.js`, the one answer carrying
`Access-Control-Allow-Origin: *`. The console's page and its assets are framed by nobody
(`frame-ancestors 'none'`, `X-Frame-Options: DENY`), kept on HTTPS (`Strict-Transport-Security`),
read as served (`nosniff`), and tell another site its origin alone (`strict-origin-when-cross-origin`). The API's own description is public too, `GET /v1/docs` and
`/openapi.json`: every door and every body, none of them a secret. Three kinds of connection:

| | what it is | who opens it |
|---|---|---|
| **HTTP** | read a log, mint a token, push knowledge, set a knob | your backend, with the org's key |
| **`WS /v1/apps`** | the app socket: your process **holds an agent** and answers its tool calls | your backend, with the org's key |
| **`WS /v1/chat`** · a LiveKit room · a phone · WhatsApp | one caller, one call | a caller |

The log is the truth: everything that happens to a call is an entry with a `seq`, written before
control returns, and every door that shows a call shows those entries.

```
Authorization: Bearer <key>             every door, HTTP and WebSocket alike
pinecall-env: production | sandbox      the world a person's key acts in (the sandbox when unsaid)
```

**A key opens what its scopes say**, and every tenant door asks for exactly one:
`403 this key does not open knowledge: it opens calls · evals`. A server's key holds `app` ·
`calls` · `talk` · `knowledge` · `evals` and lives in the one world it was made for; a person's key
holds their role's scopes and names the world per request, production only with production access,
read from their row on every request ([people.md](people.md)). `pinecall-corner: <member id>` answers an
HTTP door in a colleague's sandbox scope, for a key that opens `team` and `app`.

`fleet` is the box's own workers', one key per world, and it acts for one call at a time. A request
that names a call, in its path or as `?call=`, acts in the scope that call's head row keeps, and in
no other: a `?org=&env=&holder=` that disagrees is `404`, and so is a call nobody opened yet. A call
a dial placed is opened by the worker only in the scope the dial wrote. Before its call is opened, a
worker asks in the scope its dispatch named, `?org=&env=&holder=` (the agent's routes, declaration,
stages and hold audio, whether a ring is a developer's). It reads a call's events, state and
recording without naming a scope, a call of its own world once opened; it reads no agent's log, no
org's floor and no list of calls. The overflow's `POST /v1/callbacks` names the call it answered, and
the agent must be that call's org's.

**The one exception to the header** is `?token=`, because an `EventSource` cannot set one: only a
token of ours for one call (a page's `log_token`), never an API key, since a URL ends up in an access
log.

**Refusals** are `{"detail": "…"}` under the status, and the sentence names the fix: `401` no key;
`403` a key that does not open the door, or another world's; `404` a thing that is not there, and
another org's call, whose existence is nobody else's business; `409` a request that disagrees with
what is stored; `400` or `422` a body that is not the shape; `413` a body over 32 MiB, more than
any door takes, refused as it streams in (a webhook, which carries no key, over 4 MiB, before its
signature is read); `429` a quota, a door that runs a model past its minute's pace (120 caller lines, 6 memory
extraction runs of 50 cases at most, per org), or a minute's requests
spent (below); `502` a vendor or a
carrier that did not answer, in its own words; `503` the request was right and this box cannot
honour it, or not now: a database too busy to answer within its timeouts is a `503` to retry. A
socket closes with **1008** and the sentence. A key past its expiry is `401` saying
when it expired, never the silence of a key nobody made.

**A minute's requests.** Every door a key opens by its scopes counts the org's requests in the
world, per family of doors (the scopes the door asks for, `calls` for a log's readers): 6 000 a
minute each, and the next one that minute is `429 this org sent its calls doors 6000 requests this
minute, in production: try again in 12 s` with `Retry-After: 12`. One org's count never slows
another's, nor its other world, nor its other families. A call's own doors (the ones a worker
writes and reads a call through, which admission already bounds), the fleet's and the runner's
keys and a person who runs the box are never counted; the doors that take no key or any key
(`/v1/login…`, `/v1/whoami`, `/v1/keys`, `/v1/limits`) and the sockets are not either. The count
is the box's, not one gateway's: each gateway counts what reaches it and says its busy names on
the signal every second, and the wall is the sum, so an org spread over every gateway meets it
within a second of reaching it (a gateway with no `PINECALL_REDIS_URL` counts alone).

## 1. Your own app: `WS /v1/apps`

An app is a process that holds an agent: it declares what the agent is, receives every entry of
every call the agent takes, and answers the tool calls the model makes. It binds no port; the
socket is outbound. Commands go up, entries come down, one JSON object each.

| command | what it does |
|---|---|
| `agent.register` | this socket speaks for this agent (`routes`, `sdk`, `takes_unclaimed`); answers `agent.registered` with this socket's `app` id |
| `agent.configure` | what the agent is: tools, language, the prompt's layout, the state fields it declares. What it runs on (vendors, voice, greeting, hang-up, memory, bases) is the world's, [settings-api.md](settings-api.md) |
| `ping` | `pong` with the gateway's clock |
| `agent.drain` | this process is leaving; [a-deploy-never-cuts-a-call.md](a-deploy-never-cuts-a-call.md) |

`takes_unclaimed: false` makes a process a console: it holds the agent but is never handed a call
that named no app. `answers_dev: true` makes it the one the console's directory verbs go to
([dev-verbs.md](dev-verbs.md), "Which process answers"). Several sockets may hold one agent; a call goes to the one it names or to the
newest that takes unclaimed calls. In the sandbox an agent is held per person: two developers each
run the same slug and reach their own. The same slug in production and the sandbox is two agents.

When a call opens the socket receives `call.ringing` or `call.dialing`, `call.started`, then every
entry of the call through `call.score`. The call-scoped commands carry `"call": "<id>"`:

| command | when |
|---|---|
| `session.configure` | a declaration for this call only, before the first turn |
| `state.set` | the app's state changed, and this is all of it; `state.changed` is written and the prompt re-rendered |
| `prompt.set` · `tools.set` | one named block of the prompt, whole; the tools the model may see now |
| `agent.say` · `agent.reply` | say these words; make the model speak now, guided by an instruction the caller never hears |
| `call.event` · `call.log` | a fact from your backend (`event.received`); a line of your own (`custom`) |
| `call.hangup` | end the call |
| `call.transfer` · `call.attention` · `call.hold` · `call.unhold` · `call.dtmf` · `call.callback` | the line: [the-line.md](the-line.md) |
| `call.claim` | the caller said a page's code: [codes.md](codes.md) |
| `tool.result` | the answer to a `tool.call`, by its `call_id`: an `output` or an `error`, never both |

A refusal (`error`, with the command's `type` and `id`) and a `pong` land on the agent's log only
when this socket holds that agent; a frame naming any other slug, or no JSON at all, is answered
down the socket alone (`seq: 0`) and the socket stays. A `tool.result` answers a call of an agent
this socket holds, in its own org.

The prompt has three regions, in this order: the static blocks (cached by the provider), the
history, and the dynamic blocks at the end. The default layout is `identity`, `knowledge`, `tools`
before the history and `view` after it. A spoken call runs in a worker; the tool round trip is the
same from your side. The smallest app that works: [the-smallest-app.md](the-smallest-app.md). What
only the process in the agent's directory can do: [dev-verbs.md](dev-verbs.md).

## 2. Callers

| door | the caller |
|---|---|
| `WS /v1/chat?agent=<slug>` | text: send `{"text": "…"}`, receive the call's entries; `app=`, `contact=`, `caller=`, `persona=`, `state=` (a JSON object, 16 KB at most: the state the call opens in, carried on its `call.started`), and `call=` to take up a call whose gateway restarted |
| `POST /v1/tokens` | web voice: [tokens.md](tokens.md) |
| a phone number | a route to the agent: [numbers.md](numbers.md) |
| `POST /v1/codes` | a phone call tied to a page: [codes.md](codes.md) |
| WhatsApp | [whatsapp.md](whatsapp.md) |
| `POST /v1/agents/{slug}/dial` | a call the agent places, past the org's dial guards: [numbers.md](numbers.md) |

When every worker is full the token door answers `503` and a page offers a call back:
`POST /v1/callbacks {agent, number, channel?, via?, call?}` writes `callback.requested` on the
agent's log; `GET /v1/callbacks?agent=&after=` lists them for your app to dial.

The worker's doors (`POST /v1/calls`, `/events`, `/entries`, `/sealed`, `/tools`, `/lookup`,
`/remember`, `/recording/key`, `/commands`) belong to whoever opened the call: the fleet's key
for a call the fleet's worker opened, an org's `app` key for one its own worker opened, nobody
for a written call, which the gateway runs itself. Another key is answered `404`. What the
gateway writes on a log itself — `call.ringing`, `call.dialing`, `call.attached`,
`call.claimed`, `call.summary`, `call.score`, `memory.ops`, `docs.sources` — is refused `403` at
every append door, the fleet's included: the summary and the score are the gateway's own words.

## 3. Reading a log

`GET /v1/calls/{call}/events` answers a page of JSON, `{entries, live, next}`, or with
`Accept: text/event-stream` the same entries as a stream that ends where the log does, at
`call.score`. `after` is the cursor (a reconnecting `EventSource` sends `Last-Event-ID`; the higher
wins), `limit` up to 500, `types` a comma list, `durable=1` drops the ephemeral entries, `token` a
page's log token. `next` is the last seq the page read, so a filtered page still moves you on. The
SSE frames are `id: <seq>`, `event: <type>`, `data: <the entry>`, a `: ping` every 25 s.

A reader may watch before there is anything to read. A call id no worker has opened yet reads as
an empty log to any key, so a backend that minted a token can tail the call it names before the
caller arrives; a call id is random, and nothing is in it until its org's worker opens it, after
which only its org reads it (`404` to anyone else). An agent's own log reads the same way while no
org has registered the slug, so `pinecall logs` can wait for a first `start`; once an org holds
it, it is that org's alone.

| door | |
|---|---|
| `GET /v1/agents/{slug}/calls` | an agent's own log: registrations, declarations, errors |
| `GET /v1/calls/{call}/state` | the call reduced |
| `GET /v1/calls/{call}/recording` | the audio, seekable; a written call keeps none. It is kept sealed ([../security/private-values.md](../security/private-values.md)) and answered as recorded |
| `GET /v1/agents/{slug}/sessions` · `GET /v1/sessions` | one line per call, filtered, counted and paged, in the reader's scope |
| `GET /v1/calls/{call}/settings` | the exact settings the call was built on |
| `GET /v1/calls/{call}/prompt` | the exact prompt the call was told: `{call, blocks: [{seq, name, hash, chars, text}]}`, one per `prompt.changed` in order, each block's words as the org kept them under its hash the first time a gateway met them (a call's knowledge when it opened, an app's `prompt.set`), once per distinct text and never per call; `text` is null for a block nobody kept (a call from before they were kept). A key's read, never a page's token |
| `GET /v1/events` | the org's floor as it changes, in the world the key acts in: agents registered and detached, calls ringing, starting and ending, a person asked for and taken |

What each reader receives is its projection: [projections.md](projections.md).

### Erasing

The log is append-only: a trigger on `call_log` refuses every `UPDATE`, and every `DELETE` but
the one path below, which sets `pinecall.erasing` in its own transaction. An erasure deletes, in
one transaction, the log's entries and head, the private values sealed beside its entries, the
call's facts and tokens, the memories the call
taught, and the call's recording (its directory on the disk, its object in the recordings
bucket when the box has one, and the key it is sealed under); then it writes one row of the org's trail, `{id, at,
what, subject, env, asked_by, calls, entries, memories, recordings}`. The dial ledger stays, and a
phone call leaves its detail record in `call_records` — the numbers, the direction, when it
started and ended, how it ended; no name, no words, no outcome — for a carrier's traceback, until
the nightly run forgets it, and every dial, 24 months on. A backup of the database taken before
the erasure still holds what was erased until the backup expires, which an answer to a data
subject says.

A sealed log is the database's word too, not only the gateway's: `call_log` refuses an entry on a
sealed log but its `call.score` (a judge scores a call again after its seal), and `call_log_head`
refuses opening a sealed log again. An erasure deletes, so neither stands in its way.

| door | |
|---|---|
| `DELETE /v1/calls/{call}` | one ended call (`team`); `409` while it runs, `404` for a call the key does not read |
| `DELETE /v1/contacts/{contact}` | a contact in the key's world (`team`): every call they were on (by `call_facts.contact`), every fact kept of them, what each reader had read of their thread; `409` while they are on a call |
| `GET /v1/org/erasures` | the trail, newest first (`team`) |

### Who read what

Every read of what a call or a contact left writes one row of the org's access log, once an hour
at most for the same reader, subject and kind; `reader` is the person's member id, or a server's
key id (`k_…`):

| what | subject | the doors |
|---|---|---|
| `log` · `recording` | the call | `GET /v1/calls/{call}/events`, `/state`, `/prompt`, `/recording` |
| `listen` · `supervise` | the call | a seat handed out, `POST /v1/calls/{call}/listen`, `/supervise` |
| `export` | the org | `GET /v1/org/export` |
| `memory` | the contact, the agent, or the org | `GET /v1/contacts/{contact}/memory`, `/v1/agents/{slug}/memory`, `/v1/memory` |

A visitor's token reading its own call and the fleet reading the call it serves write none. The
operator's reads off the box (`pinecall-runtime sessions show|tail|recording`) and its tracebacks
(`pinecall-runtime traceback`, `GET /v1/ops/traceback`, one row in each org the lookup showed,
`what: traceback`, the number as subject) write rows with `reader: "operator"`. A row names what
was read, never what it said. The row is one statement the read waits on; one that cannot be
written is said in the gateway's journal and the read is answered all the same.
`GET /v1/org/reads?subject=` (`team`) answers `{reads: [{subject, what, env, reader, at}]}`,
newest first, of one subject when named.

An org is erased whole by the operator: `DELETE /v1/ops/orgs/{named}`
([operator-api.md](operator-api.md)). The trail row outlives the org.

## 4. Watching and steering a live call

`POST /v1/calls/{call}/listen` and `/supervise` (`supervise`) mint a seat, a LiveKit token for one
call: listening is hidden and silent; supervising publishes a microphone and sends verbs.
`POST /v1/calls/{call}/verbs` takes one verb, `say`, `whisper`, `takeover`, `release`, `transfer`
or `end`, with the seat or the org's key as the bearer, and answers `202`; each lands in the log as
its own `supervisor.*` entry. A call with no room is steered the same way, less `transfer`.
Where a ring lands, a developer's own phone and the agent's line: `PUT`·`DELETE /v1/line/from`,
`GET /v1/line/numbers`, `GET`·`POST`·`DELETE /v1/agents/{slug}/line`.

## 5. What an agent runs on, knows and remembers

An agent's settings and its lexicon: [settings-api.md](settings-api.md). The pipeline and its hold
melody: [pipeline-api.md](pipeline-api.md). The knowledge bases and a contact's memory, and when a
call looks either up: [../retrieval/spec.md](../retrieval/spec.md). The vendors, the org's own
keys, the voices: [provider-keys.md](provider-keys.md). The widget: [console-api.md](console-api.md).
An agent the box runs itself, from sources the org uploads, and the org's secrets:
[hosting.md](hosting.md).

## 6. Evals

The goldens, the replay and the judges, the simulated callers: [evals.md](evals.md).

## 7. The org

People, keys, sign-in, sign-up, the org's provider and mailbox: [people.md](people.md). Numbers
and carriers: [numbers.md](numbers.md). Usage, insights, limits and judging:
[console-api.md](console-api.md).

The org's compliance settings are one row, replaced whole (`team`): `GET /v1/org/policy` answers
`{policy: {retention_days, calling_hours: {from, until} | null, per_number_day, consent_everywhere, disclosure, recording_notice}, set_by, set_at}`
and `PUT /v1/org/policy` takes the policy. `retention_days` is how many days a sealed call is kept
before the nightly run erases it through the erasure path (§3); `null` keeps everything, which is
also an org nobody set. `calling_hours` and `per_number_day` are the org's outbound calling rules,
applied by destination at the dial ([numbers.md](numbers.md)): a `+1` number keeps the US hours and
three calls a day whatever the org sets wider, and a consent on file; `consent_everywhere` asks for
one for every country. `disclosure` and `recording_notice` are what a spoken call says before its
greeting, spoken by the agent's voice and logged as its first `turn.agent`: an outbound call opens
with `disclosure` — `null` is the platform's sentence in the agent's language (*"This is an
automated assistant calling on behalf of {org name}."*), `""` is none, anything else is said as
written — and every recorded spoken call, either direction, then says *"This call may be recorded."*
unless `recording_notice` is `false`. A call nobody records says no notice; a written call says
neither. Both play in the sandbox too, so a test hears what a caller will. A client that changes
one field reads the row and writes it back whole. The trail is `GET /v1/org/erasures`.

`GET /v1/org/export` (`team`) is the org's calls, memories, settings, words, documents and consents
in the key's world, as a download of JSON
Lines (`application/x-ndjson`): a header `{kind: "export", org, env, exported_at}`, then one line
per call (`{kind: "call", call, agent, holder, started_at, sealed, facts, entries}`, oldest first,
each with its whole log), then every memory (`kind: "memory"`, without its embedding), every
version of every agent's settings (`agent_config`) and words (`lexicon`), and every document of its
knowledge bases (`knowledge_file`), and every fact about a number (`consent`). A recording is not inlined: `GET /v1/calls/{call}/recording`.
