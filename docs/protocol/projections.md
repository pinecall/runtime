# The projections — what leaves the platform, and to whom

A call's log holds everything. What a reader receives is a **projection** of it, chosen by who is
reading, and there are exactly two: `tenant` and `public`. This is a public contract: a console, a
widget or a customer's own reader may rely on every row below. The runtime writes the table once,
in `log/readers.py`.

| the reader knocks with | projection |
|---|---|
| an org's key (the tenant's process, the console, the CLI) | **tenant** |
| a token bound to one call (a browser, a page's `log_token`) | **public**, or what the mint asked (`log: tenant`) |

## Tenant: everything, the declared PII masked when read

Every entry and every field. The one thing done to it is **masking at read**: a state field the
agent declared `pii` reads as `***` in `state.changed`, in `call.attached` and in the reduced
state. The key stays, so a reader knows a value exists; the value goes whole, so nothing leaks
through its type. Nothing is masked when it is written: the log keeps what was said, and a tool's
arguments reach the tenant whole. Transcripts and metrics are never touched.

## Public: a whitelist, row by row

A participant learns nothing beyond their own call: never `agent`, never `call` in an envelope,
never a cost, never a vendor. The envelope keeps `seq`, `ts`, `type`, `ephemeral`; an entry type
absent from the table is dropped whole, and a field absent from a row is dropped.

| entry | fields kept |
|---|---|
| `call.ringing` · `call.dialing` | `channel` |
| `call.started` | `channel` `direction` `started_at` |
| `call.ended` | `reason` `ended_by` `ended_at` `duration_s` |
| `user.state` · `agent.state` | `state` |
| `user.transcript` | `text` `final` |
| `agent.transcript` | `speech_id` `text` `final`: one delta, never the reply so far |
| `turn.user` | `speech_id` `text` |
| `turn.agent` | `speech_id` `text` `interrupted` `metrics`, of the metrics `e2e_latency` alone |
| `room.opened` | `name` `sid` |
| `participant.joined` | `identity` `kind` `name` |
| `participant.left` | `identity` |
| `participant.speaking` | `identity` `speaking` |
| `confirm.request` | `phrase` `ttl_s` |
| `confirm.granted` · `confirm.declined` | none: the type is the verdict |
| `call.transferred` | `to` `mode` `ok` `error` |
| `call.line` | `held` |
| `event.received` | `name` `data` `source` `identity`, only for the viewer who sent it |
| `state.changed` | `state` `changed`, filtered to the fields declared `public`, never its cause |
| `log.gap` | `from_seq` `to_seq` `snapshot` |
| `log.caught_up` | `seq` |

An entry of those three the projection reads into (`turn.agent`, `state.changed`, `log.gap`) that
does not read in today's shape is withheld from the public, since what it would leak cannot be
told. The reduced state a public reader gets keeps `seq`, `status`, `user_state`, `agent_state`,
`live`, `turns` (role, speech_id, text, interrupted; an agent turn's `e2e_latency`), `app_state`
(the public fields), `room` (identity, kind, name, joined_at, speaking), `confirms` (phrase,
status), `transfer`, `held` and the viewer's own `events`.

No third projection, no per-field opt-in beyond the three visibilities an agent declares on its
state fields (`public`, `tenant`, `pii`), no masking of transcripts.
