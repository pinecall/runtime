# State

What a log reduces to: fold every entry in `seq` order and this is what you hold. The runtime's reducer (`pinecall/log/reduce.py`) and the SDKs' are written by hand and must agree on the golden call log.

### `CallStatus`

Where the call is in its life. idle before any call.* entry, which is what an agent's own log looks like.

One of: `idle`, `ringing`, `dialing`, `active`, `ended`.

### `UserTurn`

One finished turn of the caller, with what the session measured about it.

| field | type | required | meaning |
|---|---|---|---|
| `role` | `"user"` | yes | Who spoke. |
| `speech_id` | `string` | yes | The id of the reply this turn triggered; the same id the agent's turn and its blocks carry. |
| `item_id` | `string` | no | The session's id for this message in the chat history. |
| `text` | `string` | yes | The final transcript, or the message as typed. |
| `language` | `string` | no | The language the recognizer heard, as a BCP 47 tag. |
| `transcript_confidence` | `number` | no | The recognizer's confidence in the transcript, 0 to 1, when it reports one. |
| `metrics` | `UserTurnMetrics` | yes | What the session measured about the caller's turn. |

### `AgentTurn`

One finished reply of the agent, with what the session measured about it.

| field | type | required | meaning |
|---|---|---|---|
| `role` | `"agent"` | yes | Who spoke. |
| `speech_id` | `string` | yes | The reply's id, shared with the caller's turn that triggered it and with the llm, tts and eou blocks. |
| `item_id` | `string` | no | The session's id for this message in the chat history. |
| `text` | `string` | yes | What the agent said. When interrupted, only what was actually played. |
| `interrupted` | `boolean` | yes | True when the caller cut the reply short. |
| `metrics` | `AgentTurnMetrics` | yes | What the session measured about the agent's reply. |

### `Turn`

One turn of either side, told apart by role.

One of `UserTurn`, `AgentTurn`, told apart by `role`.

### `CollectedMetrics`

Every raw metric block of the call, by kind, in the order it arrived. The turns hold the join; this holds the measurements.

| field | type | required | meaning |
|---|---|---|---|
| `llm` | `LLMMetrics[]` | yes | Every LLM request. |
| `stt` | `STTMetrics[]` | yes | Every STT request or stream segment. |
| `tts` | `TTSMetrics[]` | yes | Every TTS request. |
| `vad` | `VADMetrics[]` | yes | Every VAD health report that was kept. |
| `eou` | `EOUMetrics[]` | yes | One per caller turn. |
| `eot` | `EOTInferenceMetrics[]` | yes | Every end-of-turn prediction. |
| `interruption` | `InterruptionMetrics[]` | yes | Every interruption detector report. |
| `realtime` | `RealtimeModelMetrics[]` | yes | Every realtime model response, when a speech-to-speech model runs. |
| `avatar` | `AvatarMetrics[]` | yes | Every avatar report, when an avatar runs. |

### `ToolRun`

One tool call and, once the app answered, its result.

| field | type | required | meaning |
|---|---|---|---|
| `call_id` | `string` | yes | The id the model's call carried. |
| `name` | `string` | yes | The tool's name. |
| `arguments` | `object` | yes | The arguments the model chose, as JSON. |
| `speech_id` | `string` | no | The reply during which the model called it. |
| `status` | `"running" | "done" | "failed"` | yes | running until tool.result arrives; then done, or failed when it carried an error. |
| `output` | `any` | no | What the tool returned, as JSON, once done. |
| `error` | `string` | no | What went wrong, once failed. |
| `summary` | `string` | no | The app's one-line summary of the output, when it wrote one. |
| `duration_s` | `number` | no | How long the app's method took, seconds. |
| `seq` | `integer` | yes | The seq of the tool.call entry. |

### `PromptBlockState`

What is known about one block of the prompt without storing its text.

| field | type | required | meaning |
|---|---|---|---|
| `hash` | `string` | yes | sha256 of the block's text, so two states can be compared. |
| `chars` | `integer` | yes | The block's length in characters. |
| `seq` | `integer` | yes | The seq of the prompt.changed that set it. |

### `PromptState`

Every block the app has written, by name, without its text. The history between the static and the dynamic blocks is the turns.

A map by name: every value is a `PromptBlockState`.

### `Confirm`

One confirmation the platform asked for, and how it went.

| field | type | required | meaning |
|---|---|---|---|
| `tool` | `string` | yes | The tool that needed a yes. |
| `call_id` | `string` | yes | The tool call waiting on it. |
| `audience` | `string` | yes | sha256 of tool plus arguments: what the yes is bound to. |
| `phrase` | `string` | yes | What the agent read back to the caller. |
| `status` | `"pending" | "granted" | "declined"` | yes | pending until the caller answered. |
| `said` | `string` | no | What the caller actually said, once they answered. |
| `reason` | `string` | no | Why it was declined: no, timeout, changed, cancelled. |

### `Handoff`

Whether a supervisor holds the line right now.

| field | type | required | meaning |
|---|---|---|---|
| `active` | `boolean` | yes | True between supervisor.took_over and supervisor.released. |
| `by` | `Supervisor | null` | yes | Who holds it, or null. |

### `TransferState`

The transfer in flight or the one that happened.

| field | type | required | meaning |
|---|---|---|---|
| `to` | `string` | yes | The destination. |
| `mode` | `TransferMode | null` | no | Cold or warm, or null while nobody has said which yet. |
| `status` | `"requested" | "done" | "failed"` | yes | requested until call.transferred reports the outcome. |
| `by` | `"agent" | "supervisor"` | yes | Who asked for it. |

### `AttentionState`

The agent's last ask for a person, from attention.requested until attention.answered settles it.

| field | type | required | meaning |
|---|---|---|---|
| `reason` | `string` | yes | Why a person was wanted, in the app's words. |
| `wait_s` | `number` | yes | How long the caller was to wait. |
| `status` | `"open" | "answered" | "lapsed"` | yes | open while the caller waits; answered when a supervisor took the line; lapsed when nobody did in time, or the call ended first. |
| `asked_at` | `number` | yes | When it was asked, unix seconds: the entry's own ts. |
| `by` | `Supervisor | null` | yes | Who took the line, once somebody did. |

### `LiveTranscript`

The words on screen right now: interim transcripts that a finished turn clears.

| field | type | required | meaning |
|---|---|---|---|
| `user` | `string | null` | yes | What the caller is saying, so far: the recognizer's latest interim, which arrives whole. |
| `agent` | `string | null` | yes | What the agent is saying, so far: every agent.transcript delta of the reply in flight, joined, since it arrives one word or one token at a time. |

### `Gap`

A stretch of seqs this reader never saw.

| field | type | required | meaning |
|---|---|---|---|
| `from_seq` | `integer` | yes | The first missing seq. |
| `to_seq` | `integer` | yes | The last missing seq. |

### `LoggedError`

An error entry, kept so the console can show what went wrong and when.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The seq of the error entry. |
| `code` | `string` | yes | The error's code. |
| `message` | `string` | yes | The error's message. |

### `Participant`

One participant in the room right now, as the room reported them when they joined.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | The identity the room knows them by: sip_+34600123456, agent-AJ_5d2e8f, a visitor id. |
| `kind` | `ParticipantKind` | yes | Who a participant is to the call: the person the agent serves (over SIP or the widget), the agent itself, a supervisor who took a seat in the room, a listener who only hears, or a second SIP leg that room.invite brought in. |
| `name` | `string` | no | Their display name, when the room carried one. |
| `joined_at` | `number` | yes | When they joined, unix seconds: the ts of their participant.joined. |
| `speaking` | `boolean` | yes | True while the room reports them speaking, from participant.speaking. |
| `attributes` | `object` | yes | livekit's participant attributes verbatim, sip.* included. The public projection drops them. |

### `Room`

The LiveKit room the call lives in, and who is in it right now.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The room's name, as room.opened reported it. |
| `sid` | `string` | yes | livekit's id for the room. |
| `participants` | `Participant[]` | yes | Who is in the room now, in the order they joined. A participant.left removes them. |
| `caller` | `string | null` | yes | The identity of the participant of kind caller, or null before they joined or after they left. |

### `ReceivedEvent`

One fact that reached the agent from outside the conversation, kept by name and origin. Its data is in the log at that seq.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The seq of the event.received entry. |
| `name` | `string` | yes | The event's name. |
| `source` | `EventSource` | yes | Where an outside fact came from: the tenant's backend over the app socket (app), or a participant's browser over the DataChannel (participant). |
| `identity` | `string` | no | The participant that sent it, for source participant. |

### `CustomNote`

A line the app wrote into the log with call.log.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The seq of the custom entry. |
| `name` | `string` | yes | The app's name for the line. |
| `data` | `object` | yes | Whatever the app attached. |

### `State`

The whole of what a log says, at the seq it was read to.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The last seq folded in. 0 for an empty log. |
| `agent` | `string` | yes | The agent's slug. Empty for an empty log. |
| `call` | `string | null` | yes | The call id, or null for an agent's own log. |
| `status` | `CallStatus` | yes | Where the call is in its life. |
| `channel` | `Channel | null` | yes | The door, once known. |
| `direction` | `Direction | null` | yes | Inbound or outbound, once known. |
| `from` | `string | null` | yes | The calling number or identity, once known. |
| `to` | `string | null` | yes | The called number or identity, once known. |
| `caller` | `Contact | null` | yes | Who is on the line, once known. |
| `room` | `Room | null` | yes | The room the call lives in and who is in it, once room.opened. Null for a text session. |
| `started_at` | `number | null` | yes | When media came up, unix seconds. |
| `ended_at` | `number | null` | yes | When the call ended, unix seconds. |
| `end_reason` | `EndReason | null` | yes | Why it ended, once it did. |
| `outcome` | `string | null` | yes | The one-line outcome from call.summary. |
| `user_state` | `UserState | null` | yes | What the caller is doing, latest. |
| `agent_state` | `AgentState | null` | yes | What the agent is doing, latest. |
| `live` | `LiveTranscript` | yes | The words on screen right now: interim transcripts that a finished turn clears. |
| `turns` | `Turn[]` | yes | Every finished turn, in order. |
| `metrics` | `CollectedMetrics` | yes | Every raw metric block of the call, by kind, in the order it arrived. |
| `tools` | `ToolRun[]` | yes | Every tool call, in order, with its result once it came. |
| `app_state` | `object` | yes | The app's declared state, as of the last state.changed. |
| `events` | `ReceivedEvent[]` | yes | Every fact that arrived from outside, in order. |
| `prompt` | `PromptState` | yes | Every block the app has written, by name, without its text. |
| `tools_visible` | `string[]` | yes | The tools the model can see right now, by name. |
| `confirms` | `Confirm[]` | yes | Every confirmation asked, in order. |
| `memory` | `MemoryOp[]` | yes | Every memory operation, in order. |
| `sources` | `DocSource[]` | yes | The chunks retrieved for the latest turn that used the knowledge base. |
| `handoff` | `Handoff` | yes | Whether a supervisor holds the line right now. |
| `held` | `boolean` | yes | True while the caller is on hold. |
| `muted` | `boolean` | yes | True while the agent's audio is muted. |
| `transfer` | `TransferState | null` | yes | The transfer, once one was asked for. |
| `attention` | `AttentionState | null` | no | The last ask for a person, once the agent made one. Absent from a state folded before it existed. |
| `usage` | `ModelUsage[]` | yes | The usage rows from call.summary. |
| `cost` | `Cost | null` | yes | The cost from call.summary. |
| `routes` | `Route[]` | yes | The agent's doors, from agent.registered. |
| `gaps` | `Gap[]` | yes | Every stretch this reader missed. |
| `errors` | `LoggedError[]` | yes | Every error entry. |
| `custom` | `CustomNote[]` | yes | Every line the app wrote. |

