# Commands and verbs

What reaches the gateway: the commands an app sends over its socket, each with the event it lands in the log as, and the verbs a supervisor sends to `POST /v1/calls/{call}/verbs`. The frame around a command is in `README.md`.

| type | scope | lands as | what it does |
|---|---|---|---|
| `agent.configure` | agent | `agent.configured` | Declare or change what the agent is: voice, models, language, greeting, the full tool list. |
| `agent.drain` | agent | `agent.draining` | This socket is leaving: hand it no new call for the agent, move the live calls it holds to the newest other socket holding the agent, or park them until one registers. |
| `agent.register` | agent | `agent.registered` | The app's first message: this socket speaks for this agent and answers these doors. |
| `agent.reply` | call | `turn.agent` | Make the model speak now, guided by an instruction it reads and the caller never hears: 'tell them a slot at 10:15 just opened'. |
| `agent.say` | call | `turn.agent` | Make the agent say this text now, verbatim, outside the model's turn: a greeting, a read-back, a system notice. |
| `call.attention` | call | `attention.requested`, `call.line`, `attention.answered` | Ask for a person without sending the caller anywhere: the call waits on hold until a supervisor takes the line, or until wait_s passes with nobody taking it. |
| `call.callback` | call | `callback.requested` | Write down that the caller wants to be called back. |
| `call.claim` | call | `call.claimed` | The caller said the code a page shows: bind this call to it, so the page follows the call from here. |
| `call.dial` | agent | `call.dialing` | Place an outbound call as this agent. |
| `call.dtmf` | call | nothing | Send touch tones down the line, for an IVR on the far end. |
| `call.event` | call | `event.received` | Hand the agent a fact from the tenant's backend: a slot freed, an order shipped, a payment confirmed. |
| `call.hangup` | call | `call.ended` | End the call from the app's side. |
| `call.hold` | call | `call.line` | Put the caller on hold: they hear hold audio, the agent hears nothing. |
| `call.log` | call | `custom` | Write a line of the app's own into the call's log. |
| `call.mute` | call | `call.line` | Mute the agent: it keeps listening and thinking, produces no audio. |
| `call.opt_out` | call | nothing | The caller asked never to be called again: their number joins the org's do-not-call list. |
| `call.transfer` | call | `call.transferred` | Send the caller to another number, or bring that number into the call. |
| `call.unhold` | call | `call.line` | Take the caller off hold. |
| `call.unmute` | call | `call.line` | Unmute the agent. |
| `dev.answer` | agent | nothing | The app's answer to a dev.request: what the verb produced, or the refusal it ended in — a status and a sentence, which the gateway hands the console verbatim. |
| `participant.mute` | call | `track.unpublished` | Silence a participant for the rest of the call: their audio leaves the room, for everyone in it. |
| `participant.remove` | call | `participant.left` | Put a participant out of the room. |
| `ping` | agent | `pong` | Is the socket alive? The gateway answers pong. |
| `prompt.set` | call | `prompt.changed` | Rewrite one block of the prompt, whole, by name. |
| `room.invite` | call | `participant.joined` | Bring somebody else into the call's room. |
| `room.send` | call | `room.sent` | Push a payload to a browser in the room over the DataChannel: a card to render, a form to open. |
| `session.configure` | call | `state.changed`, `agent.configured` | Set up this one call before the first turn: the app's initial state, and any config that differs from the agent's defaults for this caller. |
| `state.set` | call | `state.changed` | The app's state changed and this is all of it. |
| `supervisor.verb` | call | `supervisor.said`, `supervisor.whispered`, `supervisor.took_over`, `supervisor.released`, `supervisor.transferred`, `supervisor.ended` | One supervise verb, from the human the door named. |
| `tool.result` | call | `tool.result` | The app ran the tool the platform asked for in tool.call and this is what came back. |
| `tools.set` | call | `tools.changed` | The tools the model may see now. |

### `agent.configure`

Declare or change what the agent is: voice, models, language, greeting, the full tool list. Only the fields sent change.

Lands in the log as: `agent.configured`.

| field | type | required | meaning |
|---|---|---|---|
| `config` | `AgentConfig` | yes | What an app declares about its agent: the prompt's layout, the language, the tools, whether it searches its bases itself, and who may see and send what. |

### `agent.drain`

This socket is leaving: hand it no new call for the agent, move the live calls it holds to the newest other socket holding the agent, or park them until one registers. The gateway answers agent.draining; the socket closes once its tools in flight have answered.

Lands in the log as: `agent.draining`.

No fields.

### `agent.register`

The app's first message: this socket speaks for this agent and answers these doors. The gateway answers agent.registered, or error.

Lands in the log as: `agent.registered`.

| field | type | required | meaning |
|---|---|---|---|
| `routes` | `Route[]` | yes | The doors this agent answers. A number may belong to one agent at a time. |
| `sdk` | `string` | no | The SDK and version the app runs: pinecall/2.0.0. |
| `host` | `string` | no | The machine the app runs on, as it names itself: what a person reading the org's live processes reads to tell a laptop from a server. |
| `takes_unclaimed` | `boolean` | no | Whether this socket may be handed a call that named no app — every phone call, and every web call that did not ask for one. A console holds the agent to serve the call it opens itself and says false, so a real caller is never answered from somebody's terminal. Absent means yes. |

### `agent.reply`

Make the model speak now, guided by an instruction it reads and the caller never hears: 'tell them a slot at 10:15 just opened'. On livekit's session.generate_reply; the sibling of agent.say, which speaks verbatim. The reply lands as turn.agent.

Lands in the log as: `turn.agent`.

| field | type | required | meaning |
|---|---|---|---|
| `instructions` | `string` | yes | What the model is told before it speaks, in words the model reads. Not what it says. |
| `allow_interruptions` | `boolean` | no | Whether the caller may cut it short. Default true. |

### `agent.say`

Make the agent say this text now, verbatim, outside the model's turn: a greeting, a read-back, a system notice. The reply lands as turn.agent.

Lands in the log as: `turn.agent`.

| field | type | required | meaning |
|---|---|---|---|
| `text` | `string` | yes | What to say, word for word. |
| `allow_interruptions` | `boolean` | no | Whether the caller may cut it short. Default true; a legal notice sets false. |

### `call.attention`

Ask for a person without sending the caller anywhere: the call waits on hold until a supervisor takes the line, or until wait_s passes with nobody taking it. attention.answered says which.

Lands in the log as: `attention.requested`, `call.line`, `attention.answered`.

| field | type | required | meaning |
|---|---|---|---|
| `reason` | `string` | yes | Why a person is wanted, in the app's words: what the supervisor reads before taking the line. |
| `wait_s` | `number` | yes | How long the caller waits for somebody to take the line before the agent has it back, above 0 and 900 at most (fifteen minutes). The app's to choose: there is no default. |

### `call.callback`

Write down that the caller wants to be called back. Lands as callback.requested with via agent; placing the call is the app's.

Lands in the log as: `callback.requested`.

| field | type | required | meaning |
|---|---|---|---|
| `number` | `string` | yes | The number to call back, E.164. |
| `when` | `string` | no | When the caller asked to be called, in their words or as a date: tomorrow morning, 2026-09-23T10:00. |
| `note` | `string` | no | What the call back is about, for whoever places it. |

### `call.claim`

The caller said the code a page shows: bind this call to it, so the page follows the call from here. Refused with `no_code` when nobody issued that code for this agent, it expired, or another call already took it.

Lands in the log as: `call.claimed`.

| field | type | required | meaning |
|---|---|---|---|
| `code` | `string` | yes | The four digits, as the page shows them. Matches `^[0-9]{4}$`. |

### `call.dial`

Place an outbound call as this agent. The new call's log opens with call.dialing; call.started follows when the far end answers.

Lands in the log as: `call.dialing`.

| field | type | required | meaning |
|---|---|---|---|
| `to` | `string` | yes | The number to call, E.164. |
| `from` | `string` | no | The number to show, E.164. Absent means the agent's first phone route. |
| `caller` | `Contact` | no | Who is on the line, as far as the platform knows. |
| `metadata` | `object` | no | Anything the app wants handed to the session at start, sealed by the platform, never seen by the far end. |

### `call.dtmf`

Send touch tones down the line, for an IVR on the far end.

| field | type | required | meaning |
|---|---|---|---|
| `digits` | `string` | yes | The tones to send, in order: 0-9, *, #. A comma is a short pause. |

### `call.event`

Hand the agent a fact from the tenant's backend: a slot freed, an order shipped, a payment confirmed. Lands as event.received with source app. The agent must have declared the name in its events with app among the senders, or the gateway answers error and nothing touches the log.

Lands in the log as: `event.received`.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The event's name, dotted, as the agent declared it: slot.released. |
| `data` | `object` | yes | Whatever comes with it, as JSON, for the app's handler. |

### `call.hangup`

End the call from the app's side. call.ended follows with reason agent_hung_up.

Lands in the log as: `call.ended`.

| field | type | required | meaning |
|---|---|---|---|
| `reason` | `string` | no | Why, in the app's words, for the log. |

### `call.hold`

Put the caller on hold: they hear hold audio, the agent hears nothing.

Lands in the log as: `call.line`.

No fields.

### `call.log`

Write a line of the app's own into the call's log. It gets a seq like everything else and lands as custom.

Lands in the log as: `custom`.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The app's name for the line. |
| `data` | `object` | yes | Whatever the app wants kept. |

### `call.mute`

Mute the agent: it keeps listening and thinking, produces no audio.

Lands in the log as: `call.line`.

No fields.

### `call.opt_out`

The caller asked never to be called again: their number joins the org's do-not-call list. The gateway writes it, whichever process runs the call, as an opt-out of the org's world with the call's id beside it (`GET /v1/org/consents/{number}`); no call of the org reaches the number again until a consent is recorded at `POST /v1/org/consents`. Nothing lands in the log: an SDK that predates the command would refuse an entry it has no shape for. Refused with `no_session` when the call is not running here, and with `bad_shape` on a call with no phone number at its far end (the widget, a chat).

Lands in the log as: nothing.

| field | type | required | meaning |
|---|---|---|---|
| `note` | `string` | no | What the caller said, in their words, kept with the opt-out. |

### `call.transfer`

Send the caller to another number, or bring that number into the call. call.transferred says whether it worked, and which mode it was.

Lands in the log as: `call.transferred`.

| field | type | required | meaning |
|---|---|---|---|
| `to` | `string` | yes | The destination number in E.164 form, or a SIP URI. |
| `mode` | `TransferMode` | no | Absent, the runtime picks: cold when the caller is on a SIP leg, warm when they are in a browser. A written conversation has no line and refuses both. |

### `call.unhold`

Take the caller off hold.

Lands in the log as: `call.line`.

No fields.

### `call.unmute`

Unmute the agent.

Lands in the log as: `call.line`.

No fields.

### `dev.answer`

The app's answer to a dev.request: what the verb produced, or the refusal it ended in — a status and a sentence, which the gateway hands the console verbatim. One of the two, never both.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | yes | The dev.request this answers. |
| `result` | `object` | no | What the verb produced, as the verb's own answer: the call that opened, the run's id, the roster, the score. |
| `refused` | `DevRefusal` | no | Why the verb did not run, in the words the console shows: the status it travels under, and the sentence. |

### `participant.mute`

Silence a participant for the rest of the call: their audio leaves the room, for everyone in it. Lands as track.unpublished for their microphone. There is no unmute; a leg that must speak again is invited again.

Lands in the log as: `track.unpublished`.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | Whom to silence, by the identity participant.joined gave them. |

### `participant.remove`

Put a participant out of the room. Lands as participant.left with reason participant_removed. Removing the caller ends the call.

Lands in the log as: `participant.left`.

| field | type | required | meaning |
|---|---|---|---|
| `identity` | `string` | yes | Whom to remove, by the identity participant.joined gave them. |

### `ping`

Is the socket alive? The gateway answers pong.

Lands in the log as: `pong`.

No fields.

### `prompt.set`

Rewrite one block of the prompt, whole, by name. The name must be one of the agent's declared blocks, or one of the default four; anything else is refused with the name.

Lands in the log as: `prompt.changed`.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The block to rewrite, by the name its PromptBlockSpec declares. |
| `text` | `string` | yes | The block's new text, whole. |

### `room.invite`

Bring somebody else into the call's room. A second SIP leg dialed to a number is the warm path: the agent stays on with the caller while the other side answers. Lands as participant.joined when they arrive, or error when they do not.

Lands in the log as: `participant.joined`.

| field | type | required | meaning |
|---|---|---|---|
| `to` | `string` | yes | A number in E.164 form for sip; an identity for participant. |
| `kind` | `"sip" | "participant"` | yes | sip: dial the number in to as a second leg of the same room; they join with kind sip. participant: admit the identity named in to, for a supervisor's or a listener's seat. |

### `room.send`

Push a payload to a browser in the room over the DataChannel: a card to render, a form to open. Lands as room.sent with the size, never the payload. The widget listens on pinecall.ui; a topic of the tenant's own reaches the tenant's own page code.

Lands in the log as: `room.sent`.

| field | type | required | meaning |
|---|---|---|---|
| `topic` | `string` | yes | The DataChannel topic: pinecall.ui, or the tenant's own. |
| `data` | `object` | yes | The payload, as JSON. The browser gets it whole; the log keeps its size. |
| `to` | `string` | no | One participant's identity. Absent: everyone in the room. |

### `session.configure`

Set up this one call before the first turn: the app's initial state, and any config that differs from the agent's defaults for this caller.

Lands in the log as: `state.changed`, `agent.configured`.

| field | type | required | meaning |
|---|---|---|---|
| `state` | `object` | no | The app's state at the start of the call, whole. |
| `config` | `AgentConfig` | no | What an app declares about its agent: the prompt's layout, the language, the tools, whether it searches its bases itself, and who may see and send what. |

### `state.set`

The app's state changed and this is all of it. The platform logs state.changed and re-renders.

Lands in the log as: `state.changed`.

| field | type | required | meaning |
|---|---|---|---|
| `state` | `object` | yes | The whole state after the change. |
| `changed` | `string[]` | no | The names of the fields that changed, when the app knows. Absent means: compare yourself. |

### `supervisor.verb`

One supervise verb, from the human the door named. Each verb lands in the caller's log as its own supervisor.* entry before the session acts on it.

Lands in the log as: `supervisor.said`, `supervisor.whispered`, `supervisor.took_over`, `supervisor.released`, `supervisor.transferred`, `supervisor.ended`.

| field | type | required | meaning |
|---|---|---|---|
| `by` | `Supervisor` | yes | The human who sent a supervise verb, as the token that let them in names them. |
| `verb` | `Verb` | yes | One supervise verb, told apart by its verb field. |

### `tool.result`

The app ran the tool the platform asked for in tool.call and this is what came back. The model reads the output or the error.

Lands in the log as: `tool.result`.

Data: `ToolResult`, in [shapes.md](shapes.md).

### `tools.set`

The tools the model may see now. The full list was declared in agent.configure; this is the subset whose when allows them in this state.

Lands in the log as: `tools.changed`.

| field | type | required | meaning |
|---|---|---|---|
| `tools` | `ToolSpec[]` | yes | The visible tools, whole specs, so a tool can change its description with the state. |

## Verbs

### `SayVerb`

Make the agent say this, verbatim, to the caller. Logged as supervisor.said.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"say"` | yes | The verb. |
| `text` | `string` | yes | What the agent will say, word for word. |

### `WhisperVerb`

Tell the agent something the caller never hears. It reaches the agent as an instruction for its next reply; logged as supervisor.whispered.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"whisper"` | yes | The verb. |
| `text` | `string` | yes | The instruction for the agent, in words the model reads. |

### `TakeoverVerb`

The supervisor takes the line: the agent goes quiet and the supervisor's audio replaces it. Logged as supervisor.took_over.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"takeover"` | yes | The verb. |

### `ReleaseVerb`

The supervisor hands the line back to the agent, which resumes with the history intact. Logged as supervisor.released.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"release"` | yes | The verb. |

### `TransferVerb`

Send the caller to another number. Logged as supervisor.transferred, then call.transferred says whether it worked.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"transfer"` | yes | The verb. |
| `to` | `string` | yes | The destination number in E.164 form, or a SIP URI. |
| `mode` | `TransferMode` | no | Absent, the runtime picks, as call.transfer does. |

### `EndVerb`

Hang up on the caller's behalf, at once: a sentence playing and a reply still being written are cut, not finished. Logged as supervisor.ended, then call.ended with reason supervisor_ended.

| field | type | required | meaning |
|---|---|---|---|
| `verb` | `"end"` | yes | The verb. |
| `reason` | `string` | no | Why, in the supervisor's words, for the log. |

### `Verb`

One supervise verb, told apart by its verb field.

One of `SayVerb`, `WhisperVerb`, `TakeoverVerb`, `ReleaseVerb`, `TransferVerb`, `EndVerb`, told apart by `verb`.

