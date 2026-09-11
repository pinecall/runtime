# The gateway API

Every door a tenant's own code may knock at, and what comes back. This is the contract the
`pinecall` package speaks — the CLI, the console and the SDK all use exactly these doors and
nothing else — so an app written against this document in any language is a first-class client.

The operator's half (`/v1/ops/*`, orgs, quotas, routes, usage) is [operator-api.md](operator-api.md)
and takes a different key; who a key belongs to at all is [../multi-tenancy.md](../multi-tenancy.md),
and the terminal that issues one is [../the-runtime-cli.md](../the-runtime-cli.md). Every wire shape named below — each event, each command, the envelope —
is generated from the schema into the **protocol** repo's `docs/`: `events.md`, `commands.md`,
`shapes.md`. The terminal that speaks all of this is the **agents** repo's `docs/the-cli.md`.

## The shape of it

A gateway is an API and serves no page. Three kinds of connection, and only three:

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

**The one exception to the header** is `?token=` on the two log doors, because an `EventSource` in
a browser cannot set a header. Only a short-lived room token is accepted there (see Tokens), never
an API key: a URL ends up in an access log.

**Refusals** are FastAPI's shape — `{"detail": "…"}` under the status — and the sentence names the
fix. `401` no key; `403` another org's, or a token reading a call it was not minted for; `404` a
thing that is not there; `409` a request that disagrees with what is stored; `422` a body that is
not the shape; `429` a quota; `503` the request was right and this box cannot honour it (no
database, no embedder, no vault key). A socket has no status to answer with, so it closes with
**1008** and the reason carries the sentence, up to 123 bytes.

---

## 1. Your own app: `WS /v1/apps`

This is the door. An app is a process that **holds an agent**: it declares what the agent is, it
receives every entry of every call that agent takes, and it answers the tool calls the model
makes. It binds no port and needs no public address — the socket is outbound.

The upgrade carries the key on the header. Then the app sends **commands** and receives
**entries**; both are single-line JSON.

```jsonc
// → a command (envelope.json)
{"type": "agent.register", "agent": "clinica-norte", "call": null, "id": "1", "data": {…}}
// ← an entry (envelope.json), exactly as the log stores it
{"seq": 1, "ts": 1789…, "call": null, "agent": "clinica-norte", "type": "agent.registered",
 "ephemeral": false, "data": {"app": "app_9f…", "routes": […]}}
```

### The three commands that open the shop

| command | what it does |
|---|---|
| `agent.register` | this socket speaks for this agent, and answers these doors (`routes`, `sdk`, `takes_unclaimed`). Answers `agent.registered`, whose `app` is **this socket's id** |
| `agent.configure` | what the agent IS: the tool list, the voice, the models, the language, the greeting, the state fields it declares. Only the fields you send change. Answers `agent.configured` |
| `ping` | answers `pong` with the gateway's clock |

`takes_unclaimed: false` is what makes a process a console rather than a server: it holds the
agent but is never handed a call that named no app — every phone call, and every web visit that
did not name one. `pinecall chat`, `pinecall test` and the console all register that way.

Several sockets may hold the same agent at once. A call goes to the one it names (`?app=`), and
otherwise to the newest socket that takes unclaimed calls.

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
  headers: { authorization: `Bearer ${process.env.PINECALL_API_KEY}` },
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
answers `{server_url, participant_token, call}`. The call id is minted **before** the browser
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
agent, one stream, useful for a dashboard.

Four more doors read a call without its log:

| | |
|---|---|
| `GET /v1/calls/{call}/state` | the call reduced: who, where, the agent's state, the prompt, the room |
| `GET /v1/agents/{slug}/sessions?limit=` | one line per finished call: when, how long, why it ended, the cost, the outcome |
| `GET /v1/calls/{call}/recording` | the audio, with byte ranges so a player can seek |
| `GET /v1/agents/{slug}/config` | what the agent declared, with the operator's overrides applied |

---

## 4. Watching and steering a live call

A **seat** is a LiveKit token for one call, minted by your key:

| door | scope | what it may do |
|---|---|---|
| `POST /v1/calls/{call}/listen` | `observe` | hear the room. Hidden and silent: the caller is never told anybody joined |
| `POST /v1/calls/{call}/supervise` | `supervise` | hear it, publish a microphone, and send the verbs |

Both answer `{server_url, participant_token, identity}`. Join the room with it (any LiveKit
client, browser or server), or use it as the bearer of:

- **`WS /v1/attach?call=<id>&token=<seat>`** — the call's log as it happens, and the verbs back up
  the same socket.
- **`POST /v1/calls/{call}/verbs`** — one verb. The bearer may be the seat **or the org key**: a
  desk that only reads and types needs no seat at all, which is what `pinecall supervise` is. It
  answers `202 {call, verb}`.

The six verbs (`protocol/schema/verbs.json`): `say` (the agent says your words), `whisper` (an
instruction the caller never hears), `takeover`, `release`, `transfer`, `end`. Each lands in the
caller's own log as its own `supervisor.*` entry with a seq, so what a human did to a call is read
the same way as what the agent did.

---

## 5. The knobs, the knowledge, the memory

**Pipeline.** `GET /v1/agents/{slug}/pipeline` answers the three legs as the NEXT call would be
built — vendor, model, voice, language — the class's greeting, the medians livekit measured over
recent calls, and which of the five knobs is turned. `PUT /v1/agents/{slug}/pipeline/overrides`
turns them: `{voice, tts_model, stt, llm, greeting}`. The body is the **whole set**, so leaving a
field out is how you give it back to what the app declared; a blank value is refused, because an
empty voice once silenced a whole line of calls.

**Knowledge.** `PUT /v1/knowledge/{base}` takes `{files: [{path, text}]}` and replaces the base
whole — it is never merged. `GET /v1/knowledge` lists the bases with their chunk counts and
embedder; `DELETE /v1/knowledge/{base}` drops one; `POST /v1/knowledge/{base}/eval` takes
`{questions: [{asks, expects}], k?}` and answers `recall@k` and `nDCG@10`, computed by code with
no model in the loop.

**Memory.** `GET /v1/contacts/{contact}/memory` is everything memory kept about one contact,
current facts first and superseded ones with the date they stopped holding.
`DELETE /v1/contacts/{contact}/memory` is the right to be forgotten and answers how many facts
went. `POST /v1/contacts/memory/eval` scores recall the same way knowledge is scored — every
question brings its own facts to a scratch contact, so no contact of yours is read or written.
`POST /v1/agents/{slug}/memory/extraction` runs the write side: one call written down per case,
one model call each — the very one a hang-up makes — judged by code.

> Both of these are **tables**. A gateway with no database answers `503 … it has no database …`
> and names what to do about it. That is not about your key.

---

## 6. Provider keys, and the vault

By default every call runs on **the box's own vendor keys**, out of its environment. An org may
bring its own account instead:

| | |
|---|---|
| `PUT /v1/provider-keys/{vendor}` | `{"key": "sk-…"}` — this org's own account with that vendor. Every call of the org runs on it from the next one |
| `DELETE /v1/provider-keys/{vendor}` | give that vendor back to the box's key |
| `GET /v1/provider-keys` | `{"vendors": ["elevenlabs", …]}` — **names only** |

No door of this runtime ever answers with a provider key: not a value, not a prefix, not a
fingerprint. A key that was lost was lost at the vendor, and the fix is to set it again.

The row is encrypted at rest with **Fernet**, under `PINECALL_VAULT_KEY` — one secret, generated
once on the box, that lives in the environment and never in the database, so a stolen dump is not
a stolen tenant. A runtime with no `PINECALL_VAULT_KEY` **cannot keep somebody else's secret and
says so**: these three doors answer

```
503 no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key
```

and every call runs on the box's own keys, which is a complete self-hosted install. To turn the
doors on: `pinecall-runtime box secrets` generates one (32 random bytes, url-safe base64) among
the seven a fresh box is made of; on a laptop, put one line in the runtime's `.env` and restart
the gateway. Rotating it is not a migration the runtime does for you: the rows are unreadable
under a new key, so tenants set their keys again.

---

## 7. Evals

| door | |
|---|---|
| `POST /v1/evals/run` | a suite of goldens driven through the connected app, scored and stored. Answers an `EvalRun` |
| `GET /v1/evals/runs?agent=&limit=` · `GET /v1/evals/runs/{id}` | what this gateway has run |
| `POST /v1/evals/replay/{call}` | ring 3: one finished call rebuilt from its log and answered by four **code** checks — consent, register, errors, latency. Takes `{banned?, budget?}` |
| `POST /v1/evals/caller` | one improvised line from a persona: `{persona, heard, turns_left}` → `{say, hangup}` |
| `POST /v1/evals/voice` | a spoken eval call held in the runtime: a room, the persona's voice, the line spoiled on purpose |

`POST /v1/calls/{call}/lookup` and `/remember` are the worker's own doors (retrieval and the
hang-up's one model call) and are documented with the log, not here.

---

## Every door, in one table

| | | |
|---|---|---|
| `WS` | `/v1/apps` | the app socket: hold an agent, answer its tools |
| `WS` | `/v1/chat?agent=` | one text caller |
| `WS` | `/v1/attach?call=&token=` | a seat's live log, and the verbs back |
| `GET` | `/v1/whoami` | the org, the key's id, its label |
| `GET` | `/v1/agents` | the agents this gateway is holding for your org |
| `GET` | `/v1/agents/{slug}/config` | what it declared, overrides applied |
| `GET` | `/v1/agents/{slug}/pipeline` · `PUT …/pipeline/overrides` | what it runs on, and the five knobs |
| `GET` | `/v1/agents/{slug}/provider-keys` | which vendors this agent's calls would use |
| `GET` | `/v1/agents/{slug}/sessions` | one line per finished call |
| `GET` | `/v1/agents/{slug}/calls` | every call of the agent, as a log |
| `GET` | `/v1/calls/{call}/events` | one call's log: a page, or SSE |
| `GET` | `/v1/calls/{call}/state` | the call reduced |
| `GET` | `/v1/calls/{call}/recording` | the audio, seekable |
| `POST` | `/v1/calls/{call}/listen` · `/supervise` | a seat |
| `POST` | `/v1/calls/{call}/verbs` | one supervisor verb |
| `POST` | `/v1/tokens` | a room token for a browser |
| `GET` | `/v1/routes` | the numbers and doors your org answers |
| `PUT`·`DELETE`·`GET` | `/v1/provider-keys[/{vendor}]` | the org's own vendor accounts |
| `PUT`·`GET`·`DELETE` | `/v1/knowledge[/{base}]` · `POST …/eval` | the base the agent answers from |
| `GET`·`DELETE` | `/v1/contacts/{contact}/memory` · `POST /v1/contacts/memory/eval` | what it keeps about a person |
| `POST` | `/v1/agents/{slug}/memory/extraction` | what a hang-up makes of a call |
| `POST` | `/v1/evals/run` · `GET /v1/evals/runs[/{id}]` · `POST /v1/evals/replay/{call}` | the suites and ring 3 |
| `POST` | `/v1/evals/caller` · `/v1/evals/voice` | the improvising caller, and a spoken eval |
| `POST` | `/v1/calls` · `/v1/calls/{call}/events` · `/sealed` · `/tools` · `/lookup` · `/remember` · `GET /commands` | the worker's own doors |
| `GET`·`POST` | `/v1/whatsapp/webhook` | Meta's |
| | `/v1/ops/*` | the operator's, with the ops key — [operator-api.md](operator-api.md) |

`GET /openapi.json` is the generated schema of all of it, and `pinecall-runtime doctor` on the box
says which of these doors can actually answer today.
