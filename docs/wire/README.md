# The wire

The seam between whoever does the real time and the application: every entry the gateway writes
and every command an app sends. It is the runtime's: `pinecall/wire/` holds each one as a pydantic
model, and these pages describe them field by field. A rule of the runtime's suite
(`tests/rules/test_21_wire_pages.py`) holds the two together: an event, a command or a shape the
code has and a page does not, a field on one side only, a type or a required flag that differ,
each fails the suite by name. The reference, page by page, is listed at the end.

## One vocabulary, one seq

The envelope of the log **is** the event. What the gateway streams to a subscriber and what
it stores are the same bytes:

```json
{ "seq": 29, "ts": 1786537584.38, "call": "CA_8f4a", "agent": "clinica-norte",
  "type": "confirm.granted", "ephemeral": false,
  "data": { "tool": "book_slot", "call_id": "toolu_02", "audience": "sha256:…", "said": "sí", "ttl_s": 120 } }
```

| field | meaning |
|---|---|
| `seq` | the entry's place in its log, from 1, written before control returns. The cursor. |
| `ts` | unix seconds, fractional |
| `call` | the call id, or `null` for an entry about the agent itself |
| `agent` | the agent's slug |
| `type` | the event, dotted; the closed list is below |
| `ephemeral` | a store may drop it and a slow reader may miss it: interim transcripts, VAD health, replay markers |
| `data` | the event's payload, whose shape is the event's own, in [events.md](events.md) |

snake_case on the wire. A key name is touched in one place per language: in Python the one
alias (`from` → `from_`), in the TypeScript SDK `toCamel`/`toSnake` for whoever wants camelCase,
never under the app's own JSON (`state`, `arguments`, `output`, `data`, …).

A command is the frame the app sends: `{ "type", "agent", "call", "id"?, "data" }`. The
gateway answers with the events the command lands as, or with `error` naming the `id`.

## Every turn carries every metric

livekit-agents 1.8 measures a great deal per turn, and all of it travels under its own
field names. `turn.user` and `turn.agent` carry the `ChatMessage` metrics report split by
role; each typed block the session emits (`LLMMetrics`, `STTMetrics`, `TTSMetrics`,
`VADMetrics`, `EOUMetrics`, `EOTInferenceMetrics`, `InterruptionMetrics`,
`RealtimeModelMetrics`, `AvatarMetrics`) is its own `metrics.<block>` entry with every
field, joined to the turn by `speech_id`; `call.summary` carries the per-model usage rows
the session sums and the provider cost in US dollars, row by row. A text session fills
what it can measure; an absent field is absent, never zero. The full table is at the end.

The join: livekit gives the caller's finished turn, the end-of-turn decision and the reply
that follows one `speech_id`. So `turn.user`, `metrics.eou`, `metrics.llm`, `metrics.tts`
and `turn.agent` for one exchange all carry the same id. `metrics.stt`, `metrics.vad`,
`metrics.eot` and `metrics.interruption` have no `speech_id` in the library; they belong to
the call. `metrics.vad` is ephemeral by default because it is a once-a-second health tick
of the detector, not a measurement of a turn: a live reader sees it, a store may drop it,
and nothing about the turn is lost with it.

## The room and the outside environment

A phone or web call lives in a LiveKit room, and the room's facts are entries like any other:
`room.opened`, `participant.joined` (with livekit's participant attributes verbatim, the `sip.*`
keys included — the caller's number is a fact of the room, not a field we invent),
`participant.left`, `participant.speaking` (ephemeral), `track.published`, `track.unpublished`. The
agent never touches LiveKit; it reads these, and acts on the room through commands: `room.invite`
(a second SIP leg, the warm path), `participant.mute`, `participant.remove`, `room.send` (a payload
to a browser, logged as `room.sent` with its size).

A fact from outside the conversation — the tenant's backend saying a slot was freed, a browser
saying a form was submitted — is one event, `event.received`, told apart by `source`. It reaches the
log only if the agent declared the name in its `events` with that source among the senders; the
app's handler may then move the state, and `state.changed` says so with a `cause` of kind `event`.
`agent.reply` makes the model speak to it. The page is `events-room.md`.

## Writing a log

A worker writes its call's entries in batches, `POST /v1/calls/{id}/entries`, and the answer is
`{entries}`: each entry as the log numbered it, in the order it was sent. The body:

| field | meaning |
|---|---|
| `after` | how many entries the log had taken from this worker on this call before this batch, from 0 |
| `entries` | 1 to 256 entries, each `{type, data, ephemeral?, ts}`: the protocol's own `type`, its `data`, `ephemeral` when the writer overrules the wire's default, and `ts`, the worker's clock when the event happened. The log keeps that `ts`, clamped to the gateway's clock and never earlier than the entry before it in the batch |

The batch is taken whole or refused whole. Sent again with the same `after`, it is answered with
the seqs and stamps it was given and nothing is written twice. `POST /v1/calls/{id}/events` takes one entry
the same way and counts nothing: it is the endpoint of a worker of an older release, which retried it
and could write an entry twice.

## Reading a log

Six endpoints, and two of them are the same URL twice — `Accept` decides:

| endpoint | gives |
|---|---|
| `GET /v1/calls/{id}/events?after=<seq>` | the call's entries after the cursor as `{entries, live, next}` — `next` is the last seq the page READ, and `null` when the read came back empty; with `Accept: text/event-stream`, the same entries as SSE that stays open. `Last-Event-ID` is an alias of `after`, and a cursor at the end of a sealed log is `204` in both flavours |
| `GET /v1/calls/{id}/state` | the whole log folded: `{state, last_seq, live}`, projected by what the caller is. `last_seq` is the cursor to open the stream at |
| `GET /v1/agents/{slug}/calls?after=<seq>` | the agent's OWN log, in the same two flavours and the same `{entries, live, next}` page: `agent.registered`, `agent.configured`, `error`. Every entry of it has `call: null` — what happens inside a call is written into that call's log, so nothing on this stream names one. `live` is always true: an agent's log never ends |
| `GET /v1/agents/{slug}/sessions?limit=<n>` | which calls that agent handled: `{calls: [...]}`, newest first, one row per call — `call`, `live`, `last_seq`, and the fields of the call's own reduced state that a list draws (`status`, `channel`, `from`, `caller`, `started_at`, `ended_at`, `outcome`, `cost`, …), through the same projection. It is the endpoint a list of calls is built from; the log above is not |
| `GET /v1/agents` | which agents this key's fleet is holding right now: `{agents: [{slug, channels}]}`, in the order their sockets claimed them. An agent no socket holds answers no call, so it is not listed; its log is still readable by slug |
| `POST /v1/calls/{id}/verbs` | one supervise verb onto a live call, with a `supervise` token or the org key. `202`; its effect is read off the log above |

A reader that only wants the app's state subscribes with `types=state.changed`: one entry per
change instead of every transcript and every metric, on the same cursor, and `GET .../state`
is the snapshot it starts from.

Reconnecting is the same URL with a fresher `after`. A reader that missed a stretch gets
`log.gap` (with a `snapshot` of the state when the platform has one), then `log.caught_up`
when the replay ends and what follows is live. A marker stands at the seq of the last entry it
speaks for: the gap at `to_seq`, `log.caught_up` at the last seq sent. The cursor is the whole
protocol. Which gateway wrote an entry, or which one a reader is connected to, changes none of
it: every gateway hears every log something of its own reads, and hands each entry on in seq
order.

## Tokens

| token | may |
|---|---|
| `talk` | connect to ONE agent, once, for 60 s (web widget), and read its own call. Minted at `POST /v1/tokens` — LiveKit's standard token endpoint, in front of our organisation check: `tokens.md`. Its metadata is sealed by the tenant's server; the browser cannot forge it |
| `chat` | `talk` with no microphone: the same room, and it SUBSCRIBES — LiveKit hands a text stream to subscribers only, so a token that could not would type into the room and never see the reply |
| `observe` | hear the room, hidden and silent. It reads no log at all |
| `supervise` | read the one call it was minted for, and send the verbs to `POST /v1/calls/{id}/verbs` |
| `participate` | read its own call, through the public projection, and send `pinecall.event` |

What each token sees is a **projection**, applied at the sink and never by a client: `public` for
`talk`, `chat` and `participate`, `tenant` for `observe` and `supervise`. The contract is `projections.md`.

## Verbs

What a supervisor sends to `POST /v1/calls/{id}/verbs`, one per request. Each lands in the log as its own event.

| verb | does | lands as |
|---|---|---|
| `say {text}` | the agent says this, verbatim | `supervisor.said` |
| `whisper {text}` | the agent is told this; the caller never hears it | `supervisor.whispered` |
| `takeover` | the supervisor takes the line, the agent goes quiet | `supervisor.took_over` |
| `release` | the line goes back to the agent, history intact | `supervisor.released` |
| `transfer {to, mode}` | the caller is sent on, cold or warm | `supervisor.transferred`, then `call.transferred` |
| `end {reason?}` | hang up | `supervisor.ended`, then `call.ended` |

## State

`State` is what a log reduces to: fold every entry in `seq` order and that is what you hold. The
runtime's reducer (`pinecall/log/reduce.py`) and the SDKs' must agree: TypeScript's and Ruby's are
written by hand, Python's is a copy of this one. All of them agree, field for field, on the runtime's golden call log
(`tests/wire/golden/call-log.json`) and the state it folds to (`call-log.state.json`); each SDK
keeps a copy of both and a test that folds one into the other. The shape is in `state.md`.

## The reference

One page per domain. A change of the wire changes its page in the same commit, or the suite fails.

| page | holds |
|---|---|
| [events.md](events.md) | every event, one line each, with the page its data shape is on |
| [events-call.md](events-call.md) | `call.*`, `user.*`, `agent.state`, `agent.transcript`, `turn.*`, `metrics.*`: the call and the conversation, field by field |
| [events-app.md](events-app.md) | `tool.*`, `state.changed`, `prompt.changed`, `tools.changed`, `confirm.*`, `memory.ops`, `docs.sources`, `custom` |
| [events-control.md](events-control.md) | `supervisor.*`, `log.gap`, `log.caught_up`, `error`, `pong`, `agent.registered`, `agent.configured` |
| [events-room.md](events-room.md) | `room.*`, `participant.*`, `track.*`, `event.received`: the room's facts and the outside environment's |
| [commands.md](commands.md) | every command with what it lands as, and the supervise verbs |
| [metrics.md](metrics.md) | the metrics table: block · field · unit · required · measured by · meaning |
| [state.md](state.md) | what a log reduces to |
| [shapes.md](shapes.md) | the envelope and the shared shapes |
| [projections.md](../protocol/projections.md) | what `public` and `tenant` keep, and the DataChannel topics |
| [operator-api.md](../protocol/operator-api.md) | `/v1/ops/*`, the ops key, and the routes endpoints |
| [tokens.md](../protocol/tokens.md) | `POST /v1/tokens`, LiveKit's token endpoint with the org check, the contact id and single use in front of it |
