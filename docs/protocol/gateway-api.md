# The gateway API

Every door a tenant's own code may knock at, and what comes back. This is the contract the
`pinecall` package speaks — the CLI, the console and the SDK all use exactly these doors and
nothing else — so an app written against this document in any language is a first-class client.

The operator's half (`/v1/ops/*`, orgs, quotas, routes, usage) is [operator-api.md](operator-api.md)
and takes a different key; who a key belongs to at all is [../multi-tenancy.md](../multi-tenancy.md),
and the terminal that issues one is [../the-runtime-cli.md](../the-runtime-cli.md). Every wire shape
named below is generated from the schema into the **protocol** repo's `docs/` (`events.md`, `commands.md`,
`shapes.md`); the terminal that speaks all of it is **agents**' `docs/the-cli.md`.

## The shape of it

A gateway is an API at `/v1`, and beside it serves the console at `/`, the operator's page at
`/admin`, and the widget at `/widget/pinecall-widget.js` — the one answer carrying `Access-Control-Allow-Origin: *`
(`api/pages.py`). That console holds a person's scoped key (§8) and shows production; the sandbox's is the same page served by `pinecall serve` on a developer's machine.
Three kinds of connection, and only three:

| | what it is | who opens it |
|---|---|---|
| **HTTP** | read a log, mint a token, push knowledge, turn a knob | your backend, with the org key |
| **`WS /v1/apps`** | **the app socket**: your process HOLDS an agent and answers its tool calls | your backend, with the org key |
| **`WS /v1/chat`** · **LiveKit room** | one caller, one call | a caller — a browser, a phone, WhatsApp |

The log is the truth. Everything that happens to a call is an `Entry` with a `seq`, written before
control returns, and every door that shows you a call is showing you those entries — the console,
the CLI and your own code read the same bytes.

```
Authorization: Bearer <api key>          every door, HTTP and WebSocket alike
Content-Type: application/json           every body
```

A key IS an org. There is no other identity: the calls a key may read, the agents it may register,
the knowledge it may push and the provider keys it may bring are that org's. Another org's log is
`403 this key does not read that org's log`, never a 404 — whether a call exists is not another
tenant's business.

**A key opens what its scopes say.** Every tenant door asks for exactly one and refuses without it
as `403 this key does not open knowledge: it opens calls · evals`. A server's token holds `app` ·
`calls` · `talk` · `knowledge`; a person's holds their role's, whole, in either world (§8). `app`: the app socket and the
worker's doors, and `POST /v1/apps/{app}/stop`. `calls`: `GET /v1/agents`, `GET /v1/apps` (the processes holding them: machine, SDK, since when), every read of a log, and — beside `app` — the one door that opens to either, `GET /v1/agents/{slug}/config`: a declaration is read by the worker holding the agent and by the console drawing its state. `talk`: `POST /v1/tokens`, `WS
/v1/chat`, and `POST /v1/agents/{slug}/dial`, the one door that PLACES a call ([console-api.md](console-api.md) §4; the trunk it dials through is `GET`·`POST /v1/carrier/outbound`, under `numbers`). `supervise`: listen, the seat, the verbs by key. `pipeline` · `knowledge` · `memory` ·
`evals` · `numbers` · `usage` · `team`: the doors of that name. `keys`: the org's own API keys.
`providers`: the vendor keys it brought. `GET /v1/whoami`, `POST /v1/login/codes` and the person's own login doors — `GET /v1/login/orgs`, `POST /v1/login/org` — ask for none. **`fleet` is the box's own worker's, and only its**: one worker answers every org's spoken calls, so at the worker's doors — `GET /v1/routes`, `/agents/{slug}/config`, `/provider-keys`, `POST /v1/calls` and the call's doors — a key holding it resolves by the corner the request names, `?org=&env=&holder=`, which is the corner the call's dispatch named; `GET /v1/routes?number=&channel=` is its question for a phone call whose dispatch named no org, and `GET /v1/agents/{slug}/rings-for?caller=&org=` its question about that call once routed to production (§3). Any other key naming a corner but its own is `403 this key works in its own org and world: only the fleet's key names another`.

**The one exception to the header** is `?token=` on the two log doors, because an `EventSource` in
a browser cannot set a header. Only a short-lived room token is accepted there (see Tokens), never
an API key: a URL ends up in an access log.

**Two more headers**: `pinecall-env: sandbox|production` names the world a person's key works in for this request — none is the sandbox, production only while their member row opens it (`403 <name> has no production access: …`), read at every request — and a server's token stays in the one it was made for (`403 this token was made for …`), sockets closing with the sentence; `pinecall-corner: <member id>` answers an HTTP door in that member's sandbox corner — an admin opening a colleague's copy ([multi-tenancy.md](../multi-tenancy.md)).
**Refusals** are FastAPI's shape — `{"detail": "…"}` under the status — and the sentence names the
fix. `401` no key; `403` another org's, or a token reading a call it was not minted for; `403` also a key whose scopes do not open the door; `404` a
thing that is not there; `409` a request that disagrees with what is stored; `422` a body that is
not the shape; `429` a quota; `503` the request was right and this box cannot honour it (no
database, no embedder, no vault key). A socket has no status to answer with, so it closes with
**1008** and the reason carries the sentence, up to 123 bytes.

---

## 1. Your own app: `WS /v1/apps`

This is the door. An app is a process that **holds an agent**: it declares what the agent is, it
receives every entry of every call that agent takes, and it answers the tool calls the model
makes. It binds no port and needs no public address — the socket is outbound.

The upgrade carries the key on the header. Then the app sends **commands** and receives **entries**; both are single-line JSON.

```jsonc
// → a command (envelope.json)
{"type": "agent.register", "agent": "clinica-norte", "call": null, "id": "1", "data": {…}}
// ← an entry (envelope.json), exactly as the log stores it
{"seq": 1, "ts": 1789…, "call": null, "agent": "clinica-norte", "type": "agent.registered",
 "ephemeral": false, "data": {"app": "app_9f…", "routes": […], "env": "production"}}
```

### The three commands that open the shop

| command | what it does |
|---|---|
| `agent.register` | this socket speaks for this agent, and answers these doors (`routes`, `sdk`, `takes_unclaimed`). Answers `agent.registered`, whose `app` is **this socket's id** |
| `agent.configure` | what the agent IS: the tool list, the voice, the models, the language, the greeting, the state fields it declares. Only the fields you send change. Answers `agent.configured` |
| `ping` | answers `pong` with the gateway's clock |

What only the process in the agent's directory can do — a written call to its class, its goldens
and personas, its knowledge folder — a console asks the gateway for, and the gateway asks that
process over this socket: [dev-verbs.md](dev-verbs.md).

`takes_unclaimed: false` is what makes a process a console rather than a server: it holds the
agent but is never handed a call that named no app — every phone call, and every web visit that
did not name one. `pinecall chat`, `pinecall test` and the console all register that way. Several
sockets may hold one agent at once: a call goes to the one it names (`?app=`), and otherwise to
the newest socket that takes unclaimed calls.

**In the sandbox an agent is held per person.** The sandbox is namespaced by the member the key was
minted for: two developers each run `tienda-sur` on their own laptop, and what each reaches — `GET
/v1/agents`, `WS /v1/chat`, the config door, a suite — is their own socket. A sandbox key naming
nobody (CI's) holds the org's own, which a person holding none falls back to. Production has one
corner, the org's own, whether a server's token or a person with production access holds it (§8). A **dialled** door is the exception:
a number exists once in a world, and a ring reaches the caller's own copy or the agent's line (§3), even through a production number. **And somebody sees all of them**: `GET /v1/agents` answers a key that opens `team` one row per corner, each carrying `holder` (absent for the org's own), and the `pinecall-corner` header opens any of them.

**Which world.** A request runs in `production` or `sandbox` — a token's own, or what a person's key names — and the agent this socket registers
is held in that world alone: the same slug in production and in the sandbox is two agents, and
neither sees the other's calls, doors or declaration. `agent.registered` and `call.started` carry
`env`; a number claimed in one world is refused to a key of the other, naming the world that holds it.
When a socket that held the agent goes, the gateway writes `agent.detached` to the agent's log —
which socket, which world, and `left: true` when nobody holds the agent there any more.

### A call, from the app's side

When a call opens on an agent this socket holds, the gateway sends its entries down the socket:
`call.ringing` or `call.dialing`, `call.started`, then everything — `turn.user`, `turn.agent`,
`state.changed`, `metrics.*`, `tool.call`, `call.summary`, `call.score`. There is one delivery:
what you receive is what the store kept.

The commands below are **call-scoped**: they carry `"call": "<id>"` and are refused with
`no_session` when that call is not running anywhere this gateway can reach.

| command | when |
|---|---|
| `session.configure` | before the first turn: the state this call opens in, and anything that differs from the agent's defaults |
| `state.set` | the app's state changed and this is **all** of it; the gateway logs `state.changed` and re-renders the prompt |
| `prompt.set` | rewrite one named block of the prompt, whole |
| `tools.set` | the subset of the declared tools the model may see right now |
| `agent.say` | say this, verbatim, outside the model's turn |
| `agent.reply` | make the model speak now, guided by an instruction the caller never hears |
| `call.event` | hand the agent a fact from your backend mid-call; lands as `event.received` |
| `call.log` | write a line of your own into the call's log; lands as `custom` |
| `call.hangup` | end the call; `call.ended` follows with `agent_hung_up` |
| `tool.result` | **the answer to a `tool.call`** — see below |

### Tools: the round trip

The model asks for a tool; the gateway writes `tool.call` into the log, which reaches your socket;
you run it in your own process, with your own database; you answer `tool.result` with the same
`call_id`. Both entries are written by the gateway, so the log shows the request and the answer
with their seqs, and a call whose tool never came back is visible as such.

```jsonc
// ← tool.call
{"seq": 42, "call": "call_ab…", "agent": "clinica-norte", "type": "tool.call",
 "data": {"call_id": "tc_1", "name": "freeSlots", "arguments": {"day": "martes"}}}
// → tool.result — `name` travels back too, so a log line stands on its own
{"type": "tool.result", "agent": "clinica-norte", "call": "call_ab…", "id": "7",
 "data": {"call_id": "tc_1", "name": "freeSlots", "output": [{"when": "martes a las diez"}]}}
```

A tool that failed answers `{"call_id": "tc_1", "name": "freeSlots", "error": "…"}` — an output or
an error, never both. The model reads whichever it gets. `summary` and `duration_ms` are optional
and are what the console and memory read instead of the whole output.

**The prompt has three regions, in this order**: the static blocks (cached by the provider), the
append-only history, and the dynamic blocks at the end. `agent.configure.prompt` declares which
blocks exist and which region each lives in; `prompt.set` writes a block's TEXT, per call, whole.
Declare none and the layout is the default: `identity`, `knowledge`, `tools` before the history,
`view` after it. Never reorder them — the static region is what the provider caches.

> A **spoken** call runs in a worker process, not in the gateway; the round trip is identical from
> your side. The worker asks `POST /v1/calls/{call}/tools`, the gateway hands it to your socket,
> and your `tool.result` goes back the same way. You never talk to a worker.

### The smallest app that works

```js
import WebSocket from "ws";

const AGENT = "clinica-norte";
const FREE_SLOTS = {
  name: "freeSlots",
  description: "Las horas libres de un día. Úsala antes de ofrecer una hora.",
  parameters: { type: "object", properties: { day: { type: "string" } }, required: ["day"] },
};
const ws = new WebSocket(`${process.env.PINECALL_URL.replace("http", "ws")}/v1/apps`, {
  headers: { authorization: `Bearer ${process.env.PINECALL_WORKER_KEY}` },
});
const send = (type, data, call = null) =>
  ws.send(JSON.stringify({ type, agent: AGENT, call, id: String(Date.now()), data }));

// What the agent IS. Declared once, for every call this socket takes.
ws.on("open", () => {
  send("agent.register", { routes: [{ channel: "web", number: null }], sdk: "mine/0.1" });
  send("agent.configure", {
    config: {
      language: "es-ES",
      greeting: { say: "Clínica Norte, ¿en qué puedo ayudarle?" },
      tools: [FREE_SLOTS],
    },
  });
});

ws.on("message", async (frame) => {
  const entry = JSON.parse(frame.toString());

  // A call opened on this agent: write the prompt and say which tools the model may see.
  if (entry.type === "call.started") {
    send("prompt.set", { name: "identity", text: "Eres la recepción de Clínica Norte. Hablas de usted." }, entry.call);
    send("tools.set", { tools: [FREE_SLOTS] }, entry.call);   // whole specs, so a tool may change
  }

  // The model asked for a tool. It runs HERE, in your process, against your database.
  if (entry.type === "tool.call") {
    const output = await freeSlots(entry.data.arguments.day);
    send("tool.result", { call_id: entry.data.call_id, name: entry.data.name, output }, entry.call);
  }
});
```

That is a complete Pinecall app: thirty lines, one dependency, no Pinecall package at all.
Everything `pinecall` adds — the class, the `@tool` decorator, the JSX prompt, the goldens — is
sugar over these frames. It was run against a gateway on the way into this file, and the call it
answered went: `agent.registered` · `agent.configured` · `call.started` · `prompt.changed` ·
`tools.changed` · `turn.user` · `tool.call` · `tool.result` · `turn.agent`.

---

## 2. Callers: how a call starts

| door | the caller |
|---|---|
| `WS /v1/chat?agent=<slug>` | **text**. Your key on the header. Send `{"text": "…"}`, receive every entry of this call. Closing the socket hangs up |
| `POST /v1/tokens` | **web voice**. Your backend mints a token; the browser joins the LiveKit room with it and never sees your key |
| a phone number | **telephony**. The number is routed to the agent (`GET /v1/routes`); a call arrives with no help from you |
| `POST /v1/whatsapp/webhook` | **WhatsApp**, from Meta |

`WS /v1/chat` takes three more query parameters: `app=<socket id>` to name which of your processes
serves it, `contact=<id>` to say who is calling (memory files the call under it), and
`caller=<id>` for the `from` on `call.started`.

`POST /v1/tokens` takes `{agent, scope, contact?, ttl_s?, metadata?, participant_identity?}` and
answers `{server_url, participant_token, call}` — or **`503`** when every worker of the fleet is
full, with the numbers and the way out in the sentence (`every seat of the fleet is taken: 12
calls on 3 workers. Offer a call back — POST /v1/callbacks with the number — or try again in a
minute.`) and `fleet.full` in the agent's log. Your page offers the visitor a call back **before**
any room is made: `POST /v1/callbacks` with `{agent, number, channel?, via?, call?}` writes
`callback.requested` onto the agent's log and answers `204`, and `GET /v1/callbacks[?agent=&after=]` is every
request your agents took, oldest first, for your app to dial. A phone caller who arrives when the
fleet is full is answered by the overflow agent on the hub, hears one sentence, and lands on the
same log the same way, `via: "overflow"`. The call id is minted **before** the browser
joins, so your page can watch the log from the first entry. `scope` is `talk` (audio) or `chat`;
both are single-use and live 60 s by default, 600 s at the most. The room is the call, the
identity is `web_…` unless you set one, and the fields the gateway owns — the room name, the
participant metadata, the `pinecall.*` attributes — are refused with a 400 if you try to set them.

---

## 3. Reading a log

One door, two flavours, decided by `Accept`:

```bash
# a page of JSON: {"entries": [...], "live": true, "next": 41}
curl -H "authorization: Bearer $KEY" \
     "$GW/v1/calls/call_ab…/events?after=0&limit=500"

# the same entries as a stream that stays open and ends where the log does
curl -H "authorization: Bearer $KEY" -H "accept: text/event-stream" \
     "$GW/v1/calls/call_ab…/events?after=0"
```

| parameter | |
|---|---|
| `after` | the cursor: the last `seq` you have. A reconnecting `EventSource` sends `Last-Event-ID` instead, and the higher of the two wins |
| `limit` | up to 500 entries per page |
| `types` | `turn.user,turn.agent` — only those |
| `durable` | `1` drops the ephemeral entries (interim transcripts, VAD) |
| `token` | a room token, for a browser that cannot set a header |

`next` is the last seq the page **read**, not the last it kept, so a page whose every entry was
filtered still moves you forward. `null` means you have reached the end of what is written.

The SSE frames are `id: <seq>`, `event: <type>`, `data: <the entry's data>`, a `: ping` comment
every 25 s so no proxy cuts a quiet call, and `retry: 1000`. The stream ends when the log does —
on `call.score`, the entry a finished call seals with.

The same shape reads an **agent's whole log**: `GET /v1/agents/{slug}/calls` — every call of that
agent, one stream. Six more doors read a call without its log, or say where one lands:

| | |
|---|---|
| `GET /v1/calls/{call}/state` | the call reduced: who, where, the agent's state, the prompt, the room |
| `GET /v1/agents/{slug}/sessions?limit=&q=&channel=&before=` | one line per call: when, how long, why it ended, the cost, the outcome, the verdict and the flags — filtered, counted and paged as [console-api.md](console-api.md) §1 says. **The reader's corner's calls only** — the world and the holder the call was opened in, which its head row keeps (`0024`): a developer's sandbox key lists their own test calls, a production key the telephone's, and an admin with `pinecall-corner` the colleague's. A call from before `0024` reads as production's, the org's own |
| `GET /v1/calls/{call}/recording` | the audio, with byte ranges so a player can seek. A written (chat) call keeps no recording and its `call.summary` points at none: `404` in a sentence |
| `GET /v1/agents/{slug}/config` | what the agent declared, with its world's settings on it. `app` or `calls`: the worker and the console both read it |
| `PUT/DELETE /v1/line/from` · `GET /v1/line/numbers` · `GET/POST/DELETE /v1/agents/{slug}/line` | **where a RING lands**, in two steps. (`/v1/line/numbers`, `app`: the phones this person said are theirs, the org's production phone numbers and the agent each reaches — what a developer's phone dials, which the numbers door would not tell a sandbox key.) First whose phone dialled: a developer says which number they call FROM (`app`, the sandbox, a key naming a person) and every call they make lands in their own corner — no coordination, three of them testing at once. Then, for a number nobody claimed, the agent's **line**: reading it takes `calls`, claiming and releasing take `app`; the first corner to hold an agent takes it and it is handed on when that terminal closes, instead of the newest `pinecall start` silently answering in a colleague's scrollback. Neither is a row — both are only meaningful next to a socket, and `pinecall start` re-says the phone on every connect. Production has one corner and the box holds it — with one exception, the next row. `TheLine` in `rest.json` |
| `GET /v1/agents/{slug}/rings-for?caller=` | **a production ring from a developer's own phone.** The worker asks it on every phone call to a production number that no dispatch aimed (`app`; the fleet's key adds `&org=`): is the phone dialling one a developer registered with `PUT /v1/line/from`, and are they holding this agent in the sandbox, in this org? `{holder}` names that developer, and the worker builds the call in their sandbox corner — the declaration and the provider keys asked for their corner, their app socket, a sandbox log whose context metadata carries `diverted_from: production`. `{holder: null}` is production's, and so is any failure to ask: the call stays where it rang. Every other caller of the real number reaches production |

---

## 4. Watching and steering a live call

A **seat** is a LiveKit token for one call, minted by your key:

| door | scope | what it may do |
|---|---|---|
| `POST /v1/calls/{call}/listen` | `observe` | hear the room. Hidden and silent: the caller is never told anybody joined |
| `POST /v1/calls/{call}/supervise` | `supervise` | hear it, publish a microphone, and send the verbs |

Both answer `{server_url, participant_token, call, identity}`. Join the room with it (any LiveKit
client, browser or server), or use it as the bearer of:

- **`WS /v1/attach?call=<id>&token=<seat>`** — the call's log as it happens, the verbs back up it.
- **`POST /v1/calls/{call}/verbs`** — one verb. The bearer may be the seat **or the org key**: a
  desk that only reads and types needs no seat, which is what `pinecall supervise` is. It answers
  `202 {call, verb, seq}`, `seq` null: the entry is written after, and read off the log.

The six verbs (`protocol/schema/verbs.json`): `say` (the agent says your words), `whisper` (an
instruction the caller never hears), `takeover`, `release`, `transfer`, `end`. Each lands in the
caller's log as its own `supervisor.*` entry with a seq: what a human did is read as what the agent did.

---

## 5. The knobs, the knowledge, the memory

**Settings.** What an agent runs on is the org's, per world, per corner, a version a row —
`GET`/`PUT /v1/agents/{slug}/settings`, its `history`, `diff` and `rollback`, and
`/v1/lexicon` for the org's words: [settings-api.md](settings-api.md). `pipeline` sets everything,
`words` the opening's words, the lexicon and what is remembered; a request in production sets
production directly — there is no promote, the goldens run in CI before a deploy. What the agent
knows by heart is `knowledge` there, a Markdown text read whole into the static block of every
call; the class carries none of it. `GET …/pipeline` reads the three legs as the NEXT call would
be built, and turns nothing ([pipeline-api.md](pipeline-api.md)).

**The bases.** `PUT /v1/knowledge/{base}` takes `{files: [{path, text}]}` and replaces the base
whole, never merged: the RAG, chunked and indexed, a turn's search fans out over every base the
agent's settings attach (`bases`). `GET /v1/knowledge` lists the bases; `DELETE …/{base}` drops
one; `POST …/{base}/eval` takes `{questions: [{asks, expects}], k?}` and answers `recall@k` and
`nDCG@10` by code; `GET /v1/knowledge/attached` says which agents read each base.
`agent.configure` refuses a class that searches for itself (`uses_knowledge`) in a world that
attaches it none, and one whose world attaches a base never pushed there, naming `pinecall docs`.

**Memory.** `GET /v1/contacts/{contact}/memory` is everything memory kept about one contact,
current facts first and superseded ones with the date they stopped holding; `DELETE` there is the
right to be forgotten and answers how many facts went. `POST /v1/contacts/memory/eval` scores
recall as knowledge is scored — every question brings its own facts to a scratch contact, so no
contact of yours is read or written. `POST /v1/agents/{slug}/memory/extraction` runs the write
side: one call written down per case, one model call each — the one a hang-up makes — judged by code.

**Both are one world's.** A base and a contact's facts carry the `env` of the request that pushed or the
call that taught them: a sandbox push never replaces the telephone's base, nor a test call's facts its memory.
Production's base is pushed there directly, with production access or a server's token. The quotas count both worlds.

> Both of these are **tables**. A gateway whose `DATABASE_URL` did not answer has none, and these
> doors say so — `503 … no database answered at DATABASE_URL …` — and name the way out: the dev
> stack up, `pinecall-runtime migrate up`, the gateway started again.

---

## 6. Provider keys, and the vault

An org may bring its own vendor keys, sealed under the box's vault key and read back by the worker alone (`providers`): [provider-keys.md](provider-keys.md).

---

## 7. Evals

| door | |
|---|---|
| `POST /v1/evals/run` | a suite of goldens driven through the connected app, scored and stored. Answers an `EvalRun` |
| `GET /v1/evals/runs?agent=&limit=` · `GET /v1/evals/runs/{id}` | what this gateway has run |
| `POST /v1/evals/replay/{call}` | ring 3: one finished call rebuilt from its log and answered by four **code** checks — consent, register, errors, latency. Takes `{banned?, budget?}` · `POST /v1/evals/judge/{call}` runs the model judges over one nobody judged: [console-api.md](console-api.md) §6 |
| `POST /v1/evals/caller` | one improvised line from a persona: `{persona, heard, turns_left}` → `{say, hangup}` |
| `POST /v1/evals/voice` | a spoken eval call held in the runtime: a room, the persona's voice, the line spoiled on purpose |

`POST /v1/calls/{call}/lookup` and `/remember` are the worker's own doors (retrieval and the
hang-up's one model call); `lookup` also answers the app (`app`) for `this.knowledge.search`, as `SearchFound {chunks: [{path, heading, text}]}`.

---

## 7b. The org's floor

Before anybody picks an agent: `GET /v1/sessions?limit=&q=&agent=&channel=&before=` is every agent's newest calls in one
list, the rows `GET /v1/agents/{slug}/sessions` draws, newest first across the org (`agent` on each
row says whose) — and, like that door, the reader's corner's alone. `GET /v1/events` is the floor changing, as SSE from now on and nothing before:
`agent.registered` when a process holds an agent, `call.ringing` · `call.dialing` ·
`call.started` · `call.ended` as calls arrive and go — each the very entry of its own log, tapped
as it is written, so what to resume from is that log and its `seq`. A turn is never on it. Both
take a key with `calls`; a room token reads its one call and neither of these. Beside them,
`GET /v1/usage?after=&limit=` is the org's own metered rows, totals and cursor (`usage`), the
tenant's read of what the operator's `/v1/ops/usage` pages; and `GET /v1/numbers` is every door
the org answers in the key's world, each saying whether an operator typed it or an app declared
it (`numbers`). `GET /v1/keys` (any key) is the org's tokens by fingerprint, never a key: every
server's, and the asker's own person keys — every person's with `keys` — each `{fingerprint, label, kind: person|server, env (null for a person's), name, created_by, created_at, last_used_at, revoked_at, scopes}`.
`POST /v1/keys {label, env}` makes a **server's token**, on a person's key with `app` (`403` on any other; production only with production access): `app` · `calls` · `talk` · `knowledge`, `pc_live_…` or `pc_test_…`, answered in the clear the once, and it outlives the person who made it.
`POST /v1/keys/{fingerprint}/revoke` stops your own key, a token you made, or any with `keys`; anything else is `404` like nobody's.
A person holds ONE key per device, `pc_…`, and names the world per request; `POST /v1/login/org {org}` mints one in another of their orgs (§8). Keys minted before, `pk_…`, still verify. A tenant's own
carrier and its numbers imported — Twilio or SIP, the carrier's trunk pointed at the box, the SFU's
trunk admitting the number, the route — are [numbers.md](numbers.md).

## 8. People: members, login, sign-up

An org's people are rows, not shared keys: invited with a one-use token, active with a password of
their own, each key minted for one person and one device (`subject`, `name`), a code a browser
spends for a key of its own — and, where `PINECALL_SIGNUP` is on, `POST /v1/signup` makes an org
allowed what the gateway's policy says. **A person is their email**, one password across every org, and an admin's `production` switch on their row (`POST`/`PATCH /v1/members`; an admin always has it) says whether their requests may run in production. `DELETE /v1/members/{id}` (`team`) removes one for good — keys revoked, the seat free, `409` for yourself and for the last active admin. Invitations and resets are **mailed** over generic SMTP — the box's `PINECALL_SMTP_URL`, or the org's own account at `GET/PUT/DELETE /v1/org/mail` — and a forgotten password is asked for at `POST /v1/login/reset`: [people.md](people.md).

Every door, method and path, in one table: [every-door.md](every-door.md).
