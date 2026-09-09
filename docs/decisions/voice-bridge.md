# The voice bridge: livekit's session on one side, the log and the app on the other

`src/pinecall/session/voice/` is what tk-940488 left as two Protocols in
`worker/entry.py` — `Bridge` (the livekit `Agent` this call runs, `opened(live)`,
`closed(reason)`) and `Bridging` (how one call gets one) — implemented. The worker was
written against those shapes and did not move. `a_bridge` is the `Bridging`; `VoiceBridge`
is the `Bridge`, and it is also the one object livekit's `Agent` reads (`Speaking`), the
command applier reaches (`Prompting`, `Ending`) and the job's shutdown callback closes.
Nothing in the package is module-level: a worker process runs one call, and the bridge is
built per call anyway.

## The files

| file | the one idea |
|---|---|
| `voice.py` | one call's bridge: the session, the log, the app's tools, the ending |
| `agent.py` | livekit's `Agent` for a spoken call: the view per request, the words as they play, the gate |
| `events.py` | session events → entries, in the order livekit produced them |
| `metrics.py` | every typed block, off the components, under livekit's names; the usage rows |
| `commands.py` | protocol commands → session actions, and which are refused by name |
| `tools.py` | every `ToolSpec` as a raw-schema function tool that round-trips through the gateway |
| `writing.py` | the one hand on the log: a queue drained in order, so a sync callback can write |
| `stt_gate.py` | the energy gate: a final no louder than the line's own quiet is not the caller |
| `barge_in.py` | two words, and never two words of agreement |

## Where the log is written from, and why not from `metrics_collected`

`AgentSession.on("metrics_collected")` is deprecated in 1.8 and warns on subscribe
(`agent_session.py:725`), so `Meters` listens on the **components** the session was built
from — the llm, the stt, the tts, the VAD, the turn detector — which emit the very same
objects. Two things followed from that, both learned by running it:

- **An emitter's listeners are a set** (`rtc/event_emitter.py:15`). livekit's own listener
  stamps `speech_id` on `LLMMetrics` and `TTSMetrics` (`agent_activity.py:1971`) and ours may run
  before it, whichever subscribed first. So `Meters.collected` writes one loop tick later, with
  `call_soon`, after every listener has had the object. A test pins it by stamping after the
  emit and reading the entry.
- **`EOUMetrics` has no component.** It is emitted straight onto the session (`:2699`), so it is
  rebuilt from the user turn's own `MetricsReport`, which livekit fills from the same numbers
  (`end_of_turn_delay` is what `:2690` reads to build `end_of_utterance_delay`), with the turn
  detector's metadata the way `:2683` writes it. Under `metrics.eou`, joined by `speech_id`.

Everything else is livekit's object, `model_dump()`ed whole and revalidated through the wire
model of the same name. `tests/session/voice/test_metrics.py` builds an instance of **every**
class in `AgentMetrics` with every field set to a distinct value and asserts the entry equals
the dump: the day the library adds a field, the schema test fails first and this one second.

**A cancelled block is a spent block.** Preemptive generation is on by default in 1.8
(`voice/turn.py:223`); a discarded attempt still emits `LLMMetrics` with `cancelled=True`
(`agents/llm/llm.py:401,446`) and livekit's own `ModelUsageCollector` sums its tokens without
looking at the flag (`metrics/usage.py:202`). The bridge writes the block, the rows carry the
tokens, and `prices.cost_of` prices them. A test does exactly that with the library's collector.

The turns come from `conversation_item_added`, because a message reaches it with its
`metrics` already complete (`agent_session.py:2065`): `turn.user` carries the user report and
`turn.agent` the agent's (`llm_node_ttft`, `tts_node_ttfb`, `playback_latency`, `e2e_latency`
…), each `model_validate`d whole. The words the caller actually hears come from
`transcription_node`, which runs on the played text and not on the model's stream, so an
interrupted reply leaves a transcript that stops where the audio stopped.

## The ticks a streaming STT keeps emitting

The first real voice call put 73 `metrics.stt` entries in the log for 42 seconds of audio, one
every ~120 ms, all identical but for `timestamp` and `audio_duration`. `livekit-metrics.md` #18 says
what they are: a streaming STT's `STTMetrics` is not a turn's block at all but the
`RECOGNITION_USAGE` meter (`agents/stt/stt.py:519-541`), its `duration` is `0.0` by contract
(`metrics/base.py:54-55`), it carries no `speech_id` to join a turn by
(`agent_activity.py:1969-1972`), and the rate is the plugin's own — five seconds of audio for
deepgram (`plugins/deepgram/stt.py:484`), every received frame for soniox
(`plugins/soniox/stt.py:604`).

**The decision: a tick rides the stream and no log keeps it.** `Meters._written` marks a
`metrics.stt` entry `ephemeral: true` exactly when `a_usage_tick` holds — the block is `streamed`
and measured no connection (`acquire_time` is 0 and `connection_reused` is false). A console
reading the SSE stream still sees every one of them, live, and can draw a meter from them; the
store forgets them, the way it already forgets `metrics.vad` and the transcripts as they form.
Nothing else about the block changes: it goes out whole, `model_dump()`ed, under livekit's names.

**Everything a tick measured is still stored, twice over.** The block that measured a *connection*
— `acquire_time`, `connection_reused`, emitted once per websocket
(`agents/stt/stt.py:445-459`) — is stored, and so is every non-streamed block from `recognize()`,
which has a real `duration` and is one per request. The call's audio seconds are stored in
livekit's own `STTModelUsage` row on `call.summary` (`metrics/usage.py:87-101`), which is what
`prices.cost_of` bills from — on that smoke call the 73 ticks summed to exactly the 42.48 s the
summary already carried. And the number a reader actually wants per turn, `transcription_delay`,
was never in these blocks: it rides the turn's own `MetricsReport`, stored whole as
`turn.user.metrics` and as `metrics.eou`.

So the milestone rule holds where it was aimed. The block that closes a turn is stored whole,
under livekit's names — it is simply `metrics.eou` and `turn.user`, because the library measures
no per-turn STT block. What was rejected: storing the last tick of each turn (there is no way to
know which tick that is, and its `audio_duration` is one 120 ms increment, not the turn's), and
summing the ticks into one block of our own (that is a number we invented, and livekit already
sums it correctly in the usage row).

`tests/session/voice/test_metrics.py` pins all of it: a scripted stream of interims plus a final,
the connection block stored and the ticks ephemeral, the usage row equal to their sum, a
non-streamed block stored whole, and the turn's `transcription_delay` reaching `metrics.eou`.

## The read-back, with the gate deferred

The confirmation gate is deferred (`confirm.md`). A tool declared with a `confirm` template
runs straight through, like every tool: `tool.call` and `tool.result` are written by the
gateway's `/v1/calls/{call}/tools` door, which is the side that holds the app's socket and so
the side that knows when the tool went out and when it came back. What the bridge adds is the
**read-back**: `Tools.ran` renders the template from the model's arguments and the result
(`{{at}}`, `{{result.ref}}`; an unfilled name stays visible), only when the tool did not fail,
and `VoiceBridge._tools_executed` speaks it with `session.say` on `function_tools_executed` —
the event livekit emits just before the outputs enter the history (`agent_activity.py:3721`).
`say` puts the sentence into the history as the agent's own words, so the order the model
reads is *output → the model's own follow-up → the read-back → the caller's next words*.
`test_the_read_back_lands_after_the_output_and_before_the_callers_next_words` runs a real
`AgentSession` on the scripted model and asserts the indices.

## What is ours and what is livekit's in an interruption

`InterruptionOptions.min_words` is a key of a `TypedDict` now (`voice/turn.py:172,194`), STT-only,
default 0 — every syllable stops the agent. `session/voice/session.py` hands livekit **two**
(`barge_in.MIN_WORDS`) unless the agent declared its own, and livekit does the counting
(`agent_activity.py:2146,2483`).

The stoplist is ours, for a reason that had to be checked first: livekit 1.8 *does* have a
backchannel detector — `InterruptionOptions.mode="adaptive"`, with `backchannel_boundary` and
`InterruptionMetrics.num_backchannels` — but it is `inference.AdaptiveInterruptionDetector`,
which talks to LiveKit Cloud's gateway over aiohttp (`inference/interruption.py`). A self-hosted
box runs mode `vad`, where "sí, sí" over the agent's voice is an interruption. So
`VoiceBridge.heard` drops a transcript — interim or final — made only of backchannel words
**while the agent is speaking**: livekit's interruption reads the running transcript, so what
never reaches it never cuts the agent off, and never becomes a turn after the agent stops.
When the agent is silent, "sí" is an answer and passes.

The energy gate stands in `stt_node`, before the recogniser's events reach the turn detector:
the frames pass through untouched and are measured on the way, the floor is learned from the
line itself (falls fast, rises slowly), and only a **final** is judged — an interim is a light
on a console and reaches no model. Nothing of convo's was opened; the lesson was the milestone
rule.

## The commands, and the ones that are not here

`agent.say` is `session.say` verbatim; `agent.reply` is `generate_reply(instructions=…)`;
`prompt.set` and `tools.set` go to `worker/state.py`'s `Regions` (the static region is
livekit's `instructions`, the view is read per request in `llm_node`) and write
`prompt.changed` / `tools.changed` the way the text session does; `call.hangup` ends the
session and then the **job** — `get_job_context()` is livekit's own accessor for it — because
ending the session alone would leave the room open. `call.transfer`, `call.dtmf`, `call.hold`
and the room verbs need the room, which the bridge does not hold: they are refused by name, in
the protocol's own words, and land with SIP in ms-4 / tk-08e08b. The transport that carries an
app's commands to a worker-run call is not in this card either — `VoiceBridge.apply` is the
door it will knock on.

## How a call ends, on the wire

The job's shutdown callbacks run gathered (`ipc/job_proc_lazy_main.py:429-436`), so `closed()`
first `aclose()`s the session — its own close drains the last speech and adds the last turn to
the history — and only then writes `call.ended` and `call.summary`. Who ended it comes from the
session's `close` event: `PARTICIPANT_DISCONNECTED` is the caller, our own `hangup` is the
agent, and `ERROR` is the platform. `JOB_SHUTDOWN` and `USER_INITIATED` are the platform too,
but not in error: the word is `drained` (ms-3 added it to `EndReason`) — a deploy, a stop or a
drain took the worker down with the call still on it, and the log says so rather than blaming
anybody. Until ms-3 both were written as `error`.

## The gateway side, which this card also landed

`worker/client.py` knocks on five doors and the gateway had one. `GET /v1/routes` and
`GET /v1/agents/{slug}/config` (`api/agents/endpoints.py`) read the registry, scoped to the
**key's** fleet — a worker cannot ask for another fleet's doors. `POST /v1/calls` opens the log
and writes `call.ringing` or `call.dialing` by direction; `POST /v1/calls/{call}/events` appends
what the protocol names and refuses the rest; `POST /v1/calls/{call}/sealed` seals and forgets
(`api/calls/endpoints.py`, same module as the GET of that path). `POST /v1/calls/{call}/tools`
(`gateway/tools.py`) is the tool round trip, and the one `tool.result` handler answers both a
text session's waiting room and a worker-run call's, on the same `Live`. All of it behind
`auth/bearer.py`'s one parser, through `deps.a_key`. `tests/gateway/test_worker_doors.py`
drives `worker/client.py` itself against the real app over httpx's ASGI transport.

`session/text/{connected,declaring,pending}.py` moved up to `gateway/` because the worker's
doors need them too, and `api/calls/endpoints.py` asks for the live memory by the one method
it needs (`Serving`) rather than importing `connected.py`, which imports the text session,
which imports the log package — the cycle a test found.

## The words, timed (tk-da9003)

There is no `tts.word` event, and there will not be one. `bot.word` was folded into
`agent.transcript` when the wire was designed (`docs/decisions/protocol.md`, the v1 table): one
event for the agent's words as they play, whatever measured them. A word timing is not another
kind of entry — it is two more fields on the delta that already carries the word, `start` and
`end`, in seconds from the beginning of that reply's audio, joined to the turn by the `speech_id`
the entry already carried.

**One source: `transcription_node`.** With `use_tts_aligned_transcript` on, livekit hands that
node one `TimedString` per word instead of the model's text chunks (livekit-words.md #19). The node
is already where `agent.transcript` is born, so the whole change is that the delta travels whole:
`str(delta)` at the top of `VoiceAgent.transcription_node` threw the timings away on the very line
they arrived. The alternative — reading the TTS node's frames and their `USERDATA_TIMED_TRANSCRIPT`
— would be a second stream of the same words, arriving before playout rather than during it, and
would have to be joined back to what the caller actually heard. The played text is the honest one:
an interrupted reply times exactly the words that were spoken and nothing after them.

**A voice that aligned nothing writes no timings, not two nulls.** livekit reports a missing
timing as `NOT_GIVEN`, never `None`, so `_word_timings` in `bridge/events.py` returns an empty
mapping and the keys are simply absent from the entry — which is also what the schema says, since
`start` and `end` are the only optional fields of `agent.transcript`.

The entries stay ephemeral, as `agent.transcript` always was: they are the live screen's, and the
turn keeps the reply whole.

## The words both processes write alike

Three small things were written on both sides of the HTTP seam because the worker may not import
the gateway and neither may import the other's package: the text the model reads back from a
tool, the `"no reply"` outcome of a call where the agent never spoke, and the sha256 of a prompt
region. Both processes may import `pinecall.log`, which holds the ideas and no framework, so
they live once in `log/wording.py` and `tests/log/test_wording.py` asserts that the text
session and the bridge hold the very same objects.

## A worker-run call is served by the app (tk-30d34e)

The first real voice call through this runtime got everything right and answered every tool with
`this call is no longer being served`. The app builds an instance for a call when **its socket**
receives `call.started` for it (`packages/pinecall/src/runtime/connect.ts:140`); a text call put
its entries on that socket by hand, and a call the worker opened put them nowhere. The app never
heard of the call, so `live.get(call.id)` was undefined and the model was told it was talking to a
hung-up line while the caller was right there. The other half of the same gap: nothing carried the
app's commands the other way, so `prompt.set` — which is how `render(state)` reaches the model —
had nowhere to go either.

### Entries: one registration, and the log is the delivery

`Live.serve(call, agent, log)` is the one registration, called from `POST /v1/calls` for a
worker's call and from `session/text/chat.py` for a text call, and torn down by `Live.close(call)`
which both `POST /v1/calls/{call}/sealed` and the chat socket already called. What it registers is
a **subscription to the call log's own fanout**, pumped down the `Send` of the app socket holding
that agent — so an entry reaches the app *because it was written*, never because a second code
path remembered to send it too. There is one app-socket table (`Live._apps`) and one delivery.

Two things fell out of that and both are better:

- `gateway/tools.py` used to append the `tool.call` / `tool.result` entries **and** send them to
  the app itself. With the call served, the send is the fanout's; sending twice was the first
  thing the new test caught, and the door is two lines shorter.
- `Live` is built with the `Registry` (`gateway/app.py`), because "which socket holds this agent"
  is the one question the delivery has to answer and the registry is where that lives. The
  alternative — a second `agent → socket` index next to `_apps` — is the table criterion 3 of the
  card forbids.

### Commands: one door, and the log stays the log

The card floated the worker tailing its own call's log for command entries, since it needs no new
door. Read with the code in front of us that is not available: a log entry may only be one of the
protocol's declared **events** (`api/calls/endpoints.py`, `UNKNOWN_EVENT`), and there is no
`command` event — inventing one would put app-to-worker plumbing into the vocabulary every reader
of a call parses, the console and the SDK included, to carry text that the log deliberately never
keeps (`prompt.set` logs a hash, not the prompt). So the commands travel on a door of their own:

**`GET /v1/calls/{call}/commands`** (`gateway/commanding.py`), server-sent events, one frame per
command in the protocol's own `Command` envelope, opened once per call by the worker
(`worker/commanding.py`, started in `worker/entry.py` right after `bridge.opened(live)`), ending
when `Live.close` drops the sentinel into the queue. It is the mirror image of
`POST /v1/calls/{call}/tools`: there the worker asks the app to run something, here the app tells
the worker's call what to do, and both exist because this process is the one holding the app's
socket. What the app sends is queued the moment the socket takes the frame — the one ceremony in
`session/text/commands.py` asks `Live.commanded` before it refuses a call it does not run — so a
command that arrives before the worker has opened the stream is not lost, only waiting.

The log is still the truth: what a command *did* is written by the process that ran it, exactly as
the text session writes its own. That is also why `state.set`, `call.event` and `call.log` are
appliers in `session/voice/commands.py` and not a shortcut where the gateway writes the entry
itself — one transport, one writer, and `call.event` is refused against the declaration by the
side that holds the config.

A command the bridge cannot run lands in the call's log as `error` with the command's own type and
id (`worker/commanding.py`), which is where the app that sent it is already reading, and the caller
hears nothing.

### What the tests pin

`tests/gateway/test_served_call.py`: a call opened through `POST /v1/calls` arrives on the app
socket as `call.ringing` then `call.started`; a tool of it travels down that same socket and its
answer comes back to the worker; a `prompt.set` for it is held instead of refused, and refused by
name once the call is sealed; and the gateway's own command door, read by `worker/client.py`'s own
client, reaches a fake bridge's `apply` in order. The bytes between the two halves are spliced by
hand in that last one because httpx's ASGI transport buffers a response whole
(`httpx 0.28, _transports/asgi.py`), so an endless SSE cannot be driven through it in-process.

## The energy gate is gone, and the five calls that ended it (tk-4009d9)

ms-3 put an energy gate on the STT path — `session/voice/stt_gate.py`, convo's lesson — to refuse a
final transcript whose loudest frame did not stand 6 dB over a noise floor the gate learned from
the line itself. No official livekit example hand-rolls one. This card asked whether it earns its
place; the answer, measured, is no. It is deleted, and with it `tests/session/voice/test_stt_gate.py`
and the `gate=` parameter of `VoiceAgent`.

### The five calls

A synthetic caller (`say -v Mónica`, 48 kHz mono, published as a microphone track in 10 ms frames
into a room a `gate-probe` fleet answered, with real Soniox / Anthropic / ElevenLabs keys) says one
sentence: her name, her phone and the day she wants. A second `say` voice reads a sports bulletin
as the television in the room, mixed into her line at four levels; a white-noise bed 20 dB under
her stands in for a fan. Every drop was written into the call's own log as a `custom` entry named
`stt_gate.dropped`, carrying the loudest frame, the floor and the text refused.

| call | the line | gate drops | television sentences that became `turn.user` |
|---|---|---|---|
| `gate-R1-clean` | quiet, caller only | **0** | 0 |
| `gate-R2-tv15` | television 15 dB under the caller | **0** | 2 |
| `gate-R3-tv22` | television 22 dB under the caller | **0** | 8 |
| `gate-R4-tv28` | television 28 dB under the caller | **2** | 3 |
| `gate-R5-softcaller` | caller 20 dB down (−37.5 dBFS), fan only | **0** | — (she was heard) |

Two drops against thirteen phantom turns, and both of them arrived after the television had already
been answered: in `gate-R3-tv22` the bulletin took the conversation over completely — eight user
turns, `freeSlots(day="martes")` called off a sentence about a football match — with the gate
letting every one of them through.

### Why it cannot work, which is the part worth keeping

The floor tracked down at 0.25 per frame and up at 0.02, so it collapses into the quietest gap on
the line rather than settling on the room. Measured live, at the moment each final was judged:

```
loudest=-15.1  floor=-42.3   'A continuación, el resumen deportivo de la jornada…'   passed
loudest=-11.0  floor=-40.4   ' Soy Ana García, mi teléfono es 600 000 001…'          passed
loudest=-28.3  floor=-42.3   ' El equipo local venció por 2 goles a 1…'              passed
loudest=-39.3  floor=-100.9  ' El equipo local venció por 2 goles a 1 en un.'        passed
```

Speech is peaky, and a floor learned from the gaps between words sits 13 to 27 dB below any talker
in the room — the caller and the television alike. The 6 dB threshold is therefore crossed by both,
always. The only thing an energy gate of this shape can refuse is a signal within 6 dB of the line's
quietest moment: a hum, a fan, a hiss — none of which produces a transcript to refuse. Its domain is
empty. Raising the threshold does not help either: the caller in `gate-R5-softcaller` was heard at
−11 dB over a −38.7 dB floor, the same distance the television keeps, so any threshold that catches
the television refuses her too.

And it was not free: `level_of` summed and squared every sample of every frame in a Python loop —
48 000 multiplications a second, per call — to decide nothing.

### What actually covers it, and what does not

`resume_false_interruption` (on since tk-c020cd, `turn.py:175-196`) is a different problem: it
resumes the agent when an interruption produced no transcript. A television that transcribes into
real Spanish sentences is not a false interruption; the words are there. The VAD and the turn
detector are likewise not filters — they say *when* a turn ends, never *whose* it was.

So nothing in the library covers the phantom turn either, and this is written down rather than
papered over: the remedy for a noisy room is input-side noise cancellation
(`livekit-plugins-noise-cancellation`, BVC — not installed here, Cloud-only for the moment) or
livekit's adaptive interruption detector, which is a WebSocket to LiveKit Cloud a self-hosted box
must not open (`session/voice/session.py`, `INTERRUPTION_MODE`). Both belong to the input, where the
signal still exists; by the time a transcript reaches `stt_node` the energy that would have decided
it has been thrown away. A future card that wants this solved buys a denoiser or ships one — it does
not write a second gate.

`VoiceBridge.heard` keeps its other half: the backchannel stoplist (`barge_in.py`), which judges the
TEXT and only while the agent is speaking. That one is measured elsewhere in this file and stays.

## The seat the session listens to (tk-4a3189)

Until this card the room held two people — the caller and the agent — so `RoomIO` picked the
caller by luck: left without an identity it links the first remote participant of an accepted kind
(`room_io.py:385-403`, kinds at `agents/job.py:151-155`), and there was only ever one. That stops
being true in ms-14. A listener joins on an `observe` token, hidden and silent, and from ms-8 a
supervisor joins on a `supervise` token and **publishes audio into the same room**. A session that
linked the supervisor would transcribe the desk and answer it, on the caller's line.

So the session listens to the caller's seat, pinned before start, and a supervisor's audio is never
the agent's input. `worker/seat.py` answers the one question — for a phone call it is the SIP leg
`sip.the_sip_leg` already finds, for a web call the participant whose `pinecall.scope` attribute is
`talk`, which is the only scope minted for the person the agent serves — and `worker/entry.py`
hands the answer to `live.start` as `room_options=RoomOptions(participant_identity=…)`.

Two doors were available and the earlier one was taken. `RoomIO` reads `participant_identity` in its
constructor (`room_io.py:60-66`) and `RoomIO.start` builds the audio input from it
(`room_io.py:117-133`), so an identity passed through the options is fixed before a single frame is
subscribed. `session.room_io.set_participant(identity)` (`room_io.py:303`) does the same job after
the fact, which is what `examples/telephony/amd.py:73` needs — that example dials the callee *after*
`session.start`, so it has no identity to pass. We know the seat before start, so we pass it.

The field itself is `RoomOptions.participant_identity` (`room_io/types.py:125`), not
`RoomInputOptions`: 1.8 deprecates `RoomInputOptions` and `RoomOutputOptions` and logs a warning
when either is used (`room_io/types.py:171-174,241`), and `AgentSession.start` marks both kwargs
deprecated (`agent_session.py:840-842`). Same field, current door.

Nobody seated yet is left unset rather than guessed at — `NOT_GIVEN`, which is exactly livekit's
own first-comer rule. It is the honest answer for an outbound call, where the leg does not exist
until a verb dials it, and for a browser that has not finished joining. The residual risk is named
here rather than papered over: if the room is empty at start and a supervisor arrives before the
caller, livekit links the supervisor. The card that puts a supervisor in the room owns closing that
window, and `set_participant` is the door it will use.
