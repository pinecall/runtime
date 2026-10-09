# Events

Every event the gateway writes, one line each, with the page that holds its data shape. The envelope around an event is in `README.md`.

| type | scope | ephemeral | page | what it says |
|---|---|---|---|---|
| `agent.configured` | agent | no | [events-control.md](events-control.md) | The gateway applied an agent.configure. |
| `agent.detached` | agent | no | [events-control.md](events-control.md) | A socket that held the agent is gone — the process exited, the connection dropped — and the agent's endpoints are whoever is left holding it. |
| `agent.draining` | agent | no | [events-control.md](events-control.md) | The answer to agent.drain, written to the agent's own log once the socket's live calls have moved: it takes no new call, and each call it held went to another socket holding the agent or waits, parked, for the next one that registers. |
| `agent.registered` | agent | no | [events-control.md](events-control.md) | The gateway accepted an agent.register: this socket now speaks for the agent and answers its routes. |
| `agent.state` | call | no | [events-call.md](events-call.md) | The agent's state changed, in the session's own words. |
| `agent.transcript` | call | yes | [events-call.md](events-call.md) | One delta of the reply the agent is giving, never the reply so far: in a voice call one word, as the voice plays it, with the seconds it was aligned to; in a written call one model token. |
| `attention.answered` | call | no | [events-control.md](events-control.md) | An ask for a person settled: a supervisor took the line, or the wait ran out and the agent has the caller back. |
| `attention.requested` | call | no | [events-control.md](events-control.md) | The agent asked for a person: the caller is on hold and waits for a supervisor to take the line. |
| `call.attached` | call | no | [events-call.md](events-call.md) | A live call is now served by this socket, mid-conversation: the socket that held it drained, or died, or the gateway restarted. |
| `call.claimed` | call | no | [events-call.md](events-call.md) | This call is the one a page was waiting for: the caller keyed the code the page shows, or the agent claimed it from what they said. |
| `call.dialing` | call | no | [events-call.md](events-call.md) | The platform is placing an outbound call and the far end has not answered yet. |
| `call.ended` | call | no | [events-call.md](events-call.md) | The call is over. |
| `call.line` | call | no | [events-call.md](events-call.md) | The line's hold and mute flags after one of them changed. |
| `call.ringing` | call | no | [events-call.md](events-call.md) | An inbound call is offered to this agent and has not been answered yet. |
| `call.score` | call | no | [events-call.md](events-call.md) | The last entry of a call: what the judges said about it at hang-up, one row per judge. |
| `call.started` | call | no | [events-call.md](events-call.md) | Media is up: the caller and the agent can hear each other, or the text session is open. |
| `call.summary` | call | no | [events-call.md](events-call.md) | What the call was about, how it went, what it consumed and what that cost. |
| `call.transferred` | call | no | [events-call.md](events-call.md) | A transfer asked for by the agent or a supervisor finished, one way or the other. |
| `callback.requested` | agent | no | [events-control.md](events-control.md) | Somebody asked to be called back: a phone caller the overflow agent answered, a web visitor who left a number at the widget, or a caller who asked the agent for one (call.callback). |
| `code.claimed` | agent | no | [events-control.md](events-control.md) | A code is off the table: a call claimed it, or it expired with none. |
| `code.issued` | agent | no | [events-control.md](events-control.md) | A page asked for a code to show beside the agent's phone number (POST /v1/codes): the four digits a caller keys to bind their call to that page. |
| `confirm.declined` | call | no | [events-app.md](events-app.md) | The caller did not say yes, or the request lapsed. |
| `confirm.granted` | call | no | [events-app.md](events-app.md) | The caller said yes. |
| `confirm.request` | call | no | [events-app.md](events-app.md) | A tool with confirm set is about to run and the platform is asking the caller. |
| `credits.exhausted` | agent | no | [events-control.md](events-control.md) | The gateway refused a call, a written turn or a register because one of the org's quotas ran out. |
| `custom` | call | no | [events-app.md](events-app.md) | A line the app wrote into the log with call.log. |
| `monitor.fired` | agent | no | [events-control.md](events-control.md) | A number the org watches crossed its line over its window: said once a day per monitor. |
| `spend.unusual` | agent | no | [events-control.md](events-control.md) | The org's calls cost more today than its own days usually do: said once a day. |
| `dev.request` | agent | yes | [events-control.md](events-control.md) | The gateway asks the app process holding the agent to do something only that process can — read a file of the agent's directory, mount its class, run its goldens — on a console's behalf. |
| `docs.sources` | call | no | [events-app.md](events-app.md) | What retrieval put in front of the model for this turn. |
| `dtmf.received` | call | no | [events-call.md](events-call.md) | A touch tone the caller keyed. |
| `error` | agent | no | [events-control.md](events-control.md) | Something went wrong. |
| `event.received` | call | no | [events-room.md](events-room.md) | A fact arrived from outside the conversation: the tenant's backend sent call.event, or a participant's browser sent pinecall.event. |
| `fleet.full` | agent | no | [events-control.md](events-control.md) | The gateway refused to open a call because every worker of the fleet was full. |
| `log.caught_up` | call | yes | [events-control.md](events-control.md) | The replay is done: everything up to seq has been sent and what follows is live. |
| `log.gap` | call | yes | [events-control.md](events-control.md) | This reader missed a stretch: it reconnected too late for the store, or fell behind and the fanout dropped ephemeral entries. |
| `memory.ops` | call | no | [events-app.md](events-app.md) | What memory did for this turn or at hangup: a recall before the reply, a remember after the call, a forget on request. |
| `message.taken` | agent | no | [events-control.md](events-control.md) | A message that was waiting (message.waiting) was answered, on this call — or will never be: `call` is null when it outlived WhatsApp's customer-service window and nothing may be said to it any more. |
| `message.waiting` | agent | no | [events-control.md](events-control.md) | A message reached one of the org's numbers while no app held the agent it routes to — the process between two deploys, a gateway just restarted. |
| `metrics.avatar` | call | no | [events-call.md](events-call.md) | One avatar timing report: livekit's AvatarMetrics, every field. |
| `metrics.eot` | call | no | [events-call.md](events-call.md) | One end-of-turn prediction: livekit's EOTInferenceMetrics, every field. |
| `metrics.eou` | call | no | [events-call.md](events-call.md) | How long closing the caller's turn took: livekit's EOUMetrics, every field. |
| `metrics.interruption` | call | no | [events-call.md](events-call.md) | The interruption detector's latest inference and running counts: livekit's InterruptionMetrics, every field. |
| `metrics.llm` | call | no | [events-call.md](events-call.md) | One LLM request as the session measured it: livekit's LLMMetrics, every field. |
| `metrics.realtime` | call | no | [events-call.md](events-call.md) | One speech-to-speech response: livekit's RealtimeModelMetrics, every field, its token details nested as the library nests them. |
| `metrics.stt` | call | no | [events-call.md](events-call.md) | One STT request or stream segment as the session measured it: livekit's STTMetrics, every field. |
| `metrics.tts` | call | no | [events-call.md](events-call.md) | One TTS request as the session measured it: livekit's TTSMetrics, every field. |
| `metrics.vad` | call | yes | [events-call.md](events-call.md) | The VAD's health for the last second or so: livekit's VADMetrics, every field. |
| `participant.joined` | call | no | [events-room.md](events-room.md) | Somebody joined the room: the caller over SIP or the widget, the agent, a supervisor, a listener, or a second SIP leg. |
| `participant.left` | call | no | [events-room.md](events-room.md) | Somebody left the room. |
| `participant.speaking` | call | yes | [events-room.md](events-room.md) | The room's own voice activity for one participant flipped. |
| `pong` | agent | yes | [events-control.md](events-control.md) | The answer to ping. |
| `prompt.changed` | call | no | [events-app.md](events-app.md) | A block of the prompt was rewritten. |
| `room.opened` | call | no | [events-room.md](events-room.md) | The LiveKit room exists and the call lives in it. |
| `room.sent` | call | yes | [events-room.md](events-room.md) | The agent pushed a payload to a browser in the room, on the tenant's room.send. |
| `state.changed` | call | no | [events-app.md](events-app.md) | The app's declared state changed. |
| `supervisor.ended` | call | no | [events-control.md](events-control.md) | A supervisor hung up the call. |
| `supervisor.released` | call | no | [events-control.md](events-control.md) | The supervisor gave the line back; the agent resumes with the history intact. |
| `supervisor.said` | call | no | [events-control.md](events-control.md) | A supervisor made the agent say this to the caller. |
| `supervisor.took_over` | call | no | [events-control.md](events-control.md) | A supervisor took the line; the agent is quiet until supervisor.released. |
| `supervisor.transferred` | call | no | [events-control.md](events-control.md) | A supervisor asked for a transfer. |
| `supervisor.whispered` | call | no | [events-control.md](events-control.md) | A supervisor told the agent something the caller never heard. |
| `tool.call` | call | no | [events-app.md](events-app.md) | The model called a tool. |
| `tool.result` | call | no | [events-app.md](events-app.md) | The app answered a tool.call. |
| `tools.changed` | call | no | [events-app.md](events-app.md) | The tools the model can see changed. |
| `track.published` | call | no | [events-room.md](events-room.md) | A participant put a track on the room: their microphone, their camera, a screen. |
| `track.unpublished` | call | no | [events-room.md](events-room.md) | A participant's track left the room: they stopped sharing, or a participant.mute took their audio away. |
| `turn.agent` | call | no | [events-call.md](events-call.md) | The agent's reply is over and this is what was said. |
| `turn.user` | call | no | [events-call.md](events-call.md) | The caller's turn is over and this is what they said. |
| `user.state` | call | no | [events-call.md](events-call.md) | The caller's state changed, in the session's own words. |
| `user.transcript` | call | yes | [events-call.md](events-call.md) | Words from the caller as the recognizer hears them. |
| `vendor.switched` | call | no | [events-call.md](events-call.md) | A stage's vendor failed or came back, and the vendor that serves the stage now. |
