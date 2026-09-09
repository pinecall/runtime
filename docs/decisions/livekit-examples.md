# livekit's official examples — what they set, and what we do about each

Two repositories are the pattern, and they are the only two:

| repo | read at | the files that matter |
|---|---|---|
| `github.com/livekit/agents` | `6bf544c`, 2026-09-07 · `main`, 2026-09-08 for the last three | `examples/voice_agents/basic_agent.py` · `examples/telephony/` · `examples/healthcare/` · `examples/drive_thru/` · `examples/homepage/` · `examples/warm-transfer/` · `examples/survey/` · `examples/data_capture_sim/` · `examples/primitives/echo-agent.py` · `examples/avatar/hold_music.py` |
| `github.com/livekit-examples/agent-starter-python` | `6b63960`, 2026-09-04 | `src/agent.py` · `AGENTS.md` |

They are read the way `docs/decisions/livekit-1.8.md` reads the library: as the source, not
from memory. That file says what livekit **does**; this one says what livekit's own authors
**choose**, which is a different question and the one a brief has to answer. The rule that
comes out of it is one paragraph in `CLAUDE.md` under *The library first*: a brief names the
knob it sets and its example `file:line`, and a card that sets a session option the examples
do not — or skips one they do — says why. **This table is where the why is written down.**

The three verdicts: **ours-too** (we set it, or livekit's default already is it),
**not-yet** (we want it; the card that lands it is named), **not-for-us** (we deliberately
do not, and the reason is here). A row moves when a card moves it, and the card says so.

## The session's own knobs

Example lines are `examples/…` in the agents repo unless they say `starter`.

| knob | the examples that set it | verdict | why |
|---|---|---|---|
| `stt` / `llm` / `tts` as `inference.*` model strings | `voice_agents/basic_agent.py:80-86`, `starter src/agent.py:27,106-111` | **not-for-us** | those strings reach LiveKit Cloud's agent-gateway, whose model lists carry none of our three vendors and which needs a cloud key a self-hosted box does not have (`livekit-1.8.md` §17). `providers/` builds the plugins itself. |
| `turn_handling.turn_detection = inference.TurnDetector()` | `starter src/agent.py:118`, `homepage/agent.py:78` | **ours-too** | `session/voice/session.py:91`, with `version="v1-mini"` named out loud so no environment variable can send a caller's transcript to the hosted model. |
| `turn_handling.interruption` `resume_false_interruption` + `false_interruption_timeout` | `voice_agents/basic_agent.py:91-92` | **ours-too** | Resume is already livekit's default (`voice/turn.py:195`); only the number is a decision, and the example's `1.0` is the one we took over the library's `2.0`. |
| `turn_handling.interruption.mode = "adaptive"` | `starter src/agent.py:121` | **not-for-us** | adaptive is a WebSocket to `agent-gateway.livekit.cloud` carrying the caller's audio (`agent_activity.py:4804-4849`). A self-hosted box must not open it, and with no cloud key it 401s every two seconds for the length of the call. We pin `"vad"` (`session/voice/session.py:76`). |
| `turn_handling.preemptive_generation` | `voice_agents/basic_agent.py:96`, `telephony/amd.py:45`, `healthcare/agent.py:766` | **ours-too** | on for a spoken call, off for a written one, where the turn arrives whole and there is nothing to race (`session/voice/session.py:32-33`). |
| `tts_text_transforms` | `voice_agents/basic_agent.py:100-104`, `homepage/agent.py:82` | **ours-too** | The two filters are already the default (`agent_session.py:351`); they are written because the parameter *replaces* the list, so a tenant's `replace({…})` alone would take the asterisks out loud with it. |
| `stt_context_options.keyterms` | `voice_agents/basic_agent.py:107` | **ours-too** | as the tenant's declared `hears` mapped to each vendor's door. |
| `stt_context_options.keyterm_detection` | `voice_agents/basic_agent.py:108-111` | **not-for-us** | a model call every `turn_interval` (`keyterm_detection.py:88-96`) to guess words the agent already declared. The tenant knows its own vocabulary; we do not spend an LLM asking. |
| `aec_warmup_duration` | `voice_agents/basic_agent.py:99` | **ours-too** | by not writing it: `3.0` is already the default (`agent_session.py:101,559`), and livekit sets it to `None` for an outbound SIP call by itself (`:1928`). A number copied here would be a second place to change it. |
| `max_tool_steps` | `drive_thru/agent.py:493` | **not-yet** | the text session names `8` (`session/text/session.py:44`) and the spoken one leaves livekit's `3`. One number, two places to read it: the card that unifies them says which. |
| `user_away_timeout` | `drive_thru/agent.py:496`, `healthcare/agent.py:769` | **not-yet** | the bridge already logs `user_state_changed` (`session/voice/events.py:105`) and nothing acts on it. What an idle caller hears is a product decision, not a knob — ms-8's desk. |
| `expressive` | `starter src/agent.py:130`, `homepage/agent.py:81` | **not-yet** | it injects the TTS provider's markup guide into the LLM prompt, which lands in the static prefix the tenant owns (the agents repo's `prompt-regions.md`). It waits for a voice we ship whose markup it can steer. |
| `ivr_detection` | `telephony/bank-ivr/ivr_navigator_agent.py:91` | **not-yet** | it is for an agent calling *into* somebody else's phone tree, and it hands the agent a `send_dtmf_events` tool. Outbound navigation is ms-8. |
| `min_endpointing_delay` | `telephony/bank-ivr/ivr_navigator_agent.py:92` | **not-for-us** | deprecated as a kwarg for v2.0 (`agent_session.py:357`), and endpointing is the ASR's own number, handed to the STT by `providers/` — setting livekit's delay from the same knob makes one number wait twice (`session/voice/session.py:79-83`). |
| `userdata` | `drive_thru/agent.py:470`, `healthcare/agent.py:758` | **not-for-us** | our state is the agent object's fields and the log; the session stays `AgentSession[None]`. That is the thesis, not a preference. |
| `tools=[EndCallTool()]` | `voice_agents/basic_agent.py:38` | **not-yet** | ms-8, with the supervisor verbs. Hanging up is a state change and needs a log entry, not just a tool. |
| `tool_handling` / `tool_options` | `voice_agents/mcp/mcp-agent.py:53` | **not-yet** | it arrives with MCP, and with it the question of what a cancellable tool does to the confirmation gate. |
| `ctx.log_context_fields` | `voice_agents/basic_agent.py:74`, `starter src/agent.py:98`, `telephony/amd.py:38`, `telephony/basic_dtmf_agent.py:130` | **ours-too** | in `worker/entry.py`: every livekit log line of the job names its room, which is the call id. |
| `record=` | nowhere — no example passes it | **not-for-us** | the default defers to the server and is only safe today because a self-hosted LiveKit is not a cloud host. We say it out loud on every call (`livekit-1.8.md` §7). |

## `session.start`, the room, and the server

| knob | the examples that set it | verdict | why |
|---|---|---|---|
| `room_options.audio_input.noise_cancellation` | `starter src/agent.py:139`, `warm-transfer/support_agent.py:87` | **not-for-us** | Krisp BVC and ai-coustics are plugins we do not ship and that run against LiveKit Cloud. A box gets what the carrier sends it. |
| `room_options.delete_room_on_close` | `survey/agent.py:395` (`True`), `warm-transfer/support_agent.py:89` (`False`) | **not-yet** | the room outliving the agent is exactly what a warm transfer needs — ms-8. Today the default (`False`, `room_io/types.py:131`) is what a cold transfer wants. |
| `room_options.text_input` / `text_output` | `other/transcription/multi-user-transcriber.py:100-106` | **not-for-us** | our written channel is the gateway's socket, not the room's text stream. A written call has no room. |
| `room_io.set_participant(identity)` | `telephony/amd.py:73` | **ours-too**, by the earlier door | the seat is pinned BEFORE start with `RoomOptions.participant_identity` (`worker/entry.py`, `voice-bridge.md`): the caller's SIP leg, or the browser holding the talk token, never a listener or a supervisor. `set_participant` itself stays for the second leg, where the identity is only known after start. |
| `session.start(room=…)` **before** `ctx.connect()`, or with no `connect` at all | `voice_agents/basic_agent.py:127` (never connects), `starter src/agent.py:134,158` (starts, then connects) | **not-for-us**, and measured | an inbound phone call is a room job: the number that was dialled is on the caller's SIP seat, so there is nothing to route by until we are in the room (`worker/entry.py:62-64`). A measurement of the gap that ordering was blamed for showed it and it was not the HTTP (0.05–0.13 s) but the first `pipeline_for` importing the vendor plugins in a cold job process — fixed with `setup_fnc`, `worker.md`. |
| `@server.rtc_session(agent_name=…)` | `starter src/agent.py:94`, `telephony/basic_dtmf_agent.py:128` | **ours-too** | `worker/main.py:76`, and ours refuses an empty name: an unnamed worker answers every room in the deployment. |
| one `AgentServer`, `cli.run_app(server)` at the foot of the file | every example | **ours-too** in shape | the process is `pinecall-runtime worker`, which builds the same one server and hands livekit the url and the key pair rather than letting it read the environment (`worker/main.py:63-77`). |

## What the starter says that is not code

`agent-starter-python/AGENTS.md` is written for a coding agent, and three of its rules are
ours now:

- **The LiveKit CLI is how you read current documentation** (`AGENTS.md:19-29`): `lk docs
  overview`, `lk docs search`, `lk docs get-page`, and beyond docs it manages SIP trunks and
  dispatch (`:51-53`). `pinecall-runtime doctor` reports whether `lk` is on the PATH and how
  to install it; nothing in this tree ever shells out to it.
- **Handoffs and tasks over one long prompt** (`:39-41`) — the same argument as
  the agents repo's `prompt-regions.md`, from the other end.
- **Never guess at agent behaviour; write the test first** (`:43-49`). Their `scenarios.yaml`
  and `lk agent simulate` are LiveKit Cloud's; ours are the four rings and `pinecall test`.

## Publishing a caller's own audio into a room

`pinecall simulate --voice` is the first thing in this tree that publishes audio *as the
caller* rather than as the agent. No example does that — theirs are all the agent's side —
but three of their lines are the pattern all the same, and one of their examples is the
shape of the whole feature. See the agents repo's `docs/decisions/simulate.md`.

| knob | the examples that set it | verdict | why |
|---|---|---|---|
| `rtc.AudioSource` → `LocalAudioTrack.create_audio_track` → `publish_track(..., TrackSource.SOURCE_MICROPHONE)` | `primitives/echo-agent.py:45-50` | **ours-too** | `pinecall.evals/calling.py`, unchanged down to the microphone source. Ours publishes at the rate the box's speech tool wrote, not at a number of its own. |
| `await source.capture_frame(frame)` with **no sleep between frames** | `primitives/echo-agent.py:94` | **ours-too** | the source paces them (its queue is a second of audio); sleeping as well puts the caller on the line at half speed. Written down because the obvious thing to add is the sleep. |
| 10 ms as the frame unit | `primitives/echo-agent.py:52` (*"10 seconds of audio (1000 frames * 10ms)"*) | **ours-too** | it is what a lost packet is a unit OF, which is what `--packet-loss` drops. |
| mixing by summing into a buffer and clipping to ±32767 as `int16` | `avatar/hold_music.py:74-81` | **ours-too** | `pinecall.evals/line.py:mixed`. A wrap instead of a clip is a click the caller never made, and the STT hears it as one. |
| `lk agent simulate --scenarios scenarios.yaml`, `instructions` / `agent_expectations` / `userdata` | `data_capture_sim/scenarios.yaml` + the simulations doc page | **not-for-us as a runner, ours as a design** | *"Simulations run on LiveKit Cloud using your project's credentials"* — a self-hosted box has no project. The three ideas are taken and named in the agents repo's `docs/decisions/simulate.md`; the runner is ours. |
