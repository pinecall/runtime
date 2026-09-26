# The projections — what leaves the platform, and to whom

A call's log holds everything: every word, every number, every tool's arguments. What a reader
receives is a **projection** of it, chosen by who is reading, and there are exactly two. This is a
public contract: a console, a widget or a customer's own reader may rely on every row below.
The runtime spells the two names in two places and nowhere else — `log/projection.py` and
`tokens/scopes.py` — so no sink can decide on its own what "public" means.

## Who reads through which

| the reader knocks with | projection |
|---|---|
| an org's **API key** (the tenant's own process, the console, the CLI) | **tenant** |
| a **call token** with scope `talk`, `chat`, `observe`, `supervise` or `participate` (a browser, a supervisor's seat) | derived from the scope's grant: a scope that reads the log past its own call reads **tenant**; every scope bound to one call reads **public** |

A token is bound to one call; a key is not. `GET /v1/calls/{call}/events`, `/state`, the
DataChannel a browser receives in the room, and `WS /v1/chat` all serve through the reader's
projection — the same rows for every door.

## Tenant: everything, with declared PII masked

The tenant sees every entry and every field. The one thing done to it is **masking**, at the
moment an entry is written (`log/pii.py`), never at read time:

- A value whose field the agent declared `pii` — a state field with visibility `pii`, or a tool
  argument named in the ToolSpec's `pii` set — is replaced by the mask `***`. The key stays, so
  a reader knows a value exists; the type is gone, so nothing leaks through its shape.
- The masker also **learns** from `state.changed`: a value the app put in a `pii` state field is
  masked wherever it appears afterwards in the same call (a caller's name repeated in a tool's
  answer), if it is at least three characters long.
- **Never touched**, whatever was declared: the transcripts (`turn.*`, `user.transcript`,
  `agent.transcript`) and every metric (`metrics.*`). A transcript with holes is not a
  transcript, and a number is not a name.
- The app's own state (`app_state` in the reduced state) is masked at the sink against the
  declaration, since the tenant's console is the one reader that declared what is private.

## Public: a whitelist, row by row

A participant asked about one call and learns nothing beyond it: never `agent`, never `call` in
an envelope or a state, never a cost, never a vendor. Every entry type absent from the table is
dropped whole; every field absent from a row is dropped.

The envelope keeps `seq`, `ts`, `type`, `ephemeral`.

| entry | fields kept | feeds |
|---|---|---|
| `call.ringing` `call.dialing` | `channel` | status |
| `call.started` | `channel` `direction` `started_at` | status |
| `call.ended` | `reason` `ended_by` `ended_at` `duration_s` | status |
| `user.state` `agent.state` | `state` | user_state, agent_state |
| `user.transcript` | `text` `final` | live (the interim words, without the recognizer's confidence) |
| `agent.transcript` | `speech_id` `text` `final` — one **delta**, never the reply so far: a word with its timings in a voice call, one model token in a written one. `start` and `end` are dropped. The reducer joins every delta since the last `turn.agent` into `live.agent`; a `final` one clears it | live |
| `turn.user` | `speech_id` `text` | turns |
| `turn.agent` | `speech_id` `text` `interrupted` `metrics` — of the metrics, only `e2e_latency` | turns |
| `room.opened` | `name` `sid` | room |
| `participant.joined` | `identity` `kind` `name` | room |
| `participant.left` | `identity` | room |
| `participant.speaking` | `identity` `speaking` | room |
| `confirm.request` | `phrase` `ttl_s` | confirms |
| `confirm.granted` `confirm.declined` | — (the type IS the verdict) | confirms |
| `call.transferred` | `to` `mode` `ok` `error` | transfer |
| `call.line` | `held` (never `mute`) | held |
| `event.received` | `name` `data` `source` `identity` — kept only when the viewer is the one who sent it | events |
| `state.changed` | `state` `changed` — filtered to fields declared `public`, and never with its cause | app_state |
| `log.gap` | `from_seq` `to_seq` `snapshot` | the cursor |
| `log.caught_up` | `seq` | the cursor |

The **reduced state** a public reader gets keeps, in this order: `seq`, `status`, `user_state`,
`agent_state`, `live`, `turns` (each turn: `role`, `speech_id`, `text`, `interrupted`; an agent
turn's metrics down to `e2e_latency` — how long the caller waited, the one number that is about
them), `app_state` (the fields declared `public`), `room` (participants: `identity`, `kind`,
`name`, `joined_at`, `speaking`), `confirms` (`phrase`, `status`), `transfer`, `held`, `events`
(the viewer's own).

## What is deliberately not here

No third projection, no per-field opt-in beyond the three visibilities (`public`, `tenant`,
`pii`) an agent declares on its state fields, no masking of transcripts. A reader who needs more
than public holds a key, and a key is a tenant.
