# livekit-agents 1.8.0 — the session, the server, and the plugin defaults

Invariants 7 to 12, 15 to 17 and 20 of [livekit-1.8.md](livekit-1.8.md), which holds the verdict
table, the version they were read in and the rest of the chapters. Nothing here was reworded: it
was moved.

## 7 — recording

`AgentSession.start(record=…)` (`agents/voice/agent_session.py:868`) takes `True`, `False` or a
granular `RecordingOptions` (`:105`). Given nothing, it defers to the server:
`job_ctx.job.enable_recording`, and **`False` when there is no job context at all**
(`:898-902`) — which is what our text session already relies on.

Two destinations, and neither is a surprise:

- **the audio** — `RecorderIO` writes `audio.ogg` into `job_ctx.session_directory`
  (`:1043`), which is a `tempfile.TemporaryDirectory` unless the agents console is driving
  the process (`agents/job.py:232-240`). Nothing is written to the room.
- **traces, logs and the session report** — uploaded only when `_observability_url`
  resolves (`agents/job.py:830`, `:337`), and that is `LIVEKIT_OBSERVABILITY_URL` or a
  **LiveKit Cloud** host (`agents/job.py:66-74`). Against our own self-hosted LiveKit it is
  `None` and nothing leaves the box.

**Our rule:** never leave `record=` to the default. The default is safe today only because the
self-hosted URL is not a cloud one, and that is a property of the deployment, not of the code. A
text session and a box with `RECORD=0` pass `record=False`; a call that keeps audio passes
`recordings.AUDIO_ONLY` — audio on, the three uploads off, every key said — and the job's
directory is pointed at ours first, because the temporary one is removed with the job
(`worker/recordings.py`, `docs/decisions/talk.md`).

## 8 · 9 — interruptions and preemptive generation

Every turn-handling knob moved out of the `AgentSession` kwargs into two `TypedDict`s under
`turn_handling` (`agents/voice/turn.py:150,201`); the old kwargs still work and are
`@deprecate_params`-marked for **v2.0** (`agent_session.py:355-370`).
`InterruptionOptions.min_words` defaults to `0` (`turn.py:194`) and is read only where an STT
exists (`agent_activity.py:2146`, `:2483`) — with a realtime model it is inert.

`InterruptionOptions.mode` absent is "auto", and auto resolves to the **adaptive** detector — a
WebSocket to LiveKit Cloud with the caller's audio — whenever `utils.is_dev_mode()` or
`utils.is_hosted()` (`agent_activity.py:4804-4849`); only production mode disables it by default.
`session/voice/session.py` pins `"vad"` (see `worker.md`).

**`preemptive_generation` is enabled by default in 1.8** (`turn.py:223`). It was opt-in in
1.7.x. This is the one verdict that changes what we build: the LLM runs **before the turn is
confirmed**, up to 3 attempts per user turn, for utterances under 10 s (`turn.py:223-228`);
TTS stays behind the turn unless `preemptive_tts` is set. A discarded attempt is cancelled
(`agent_activity.py:1226`) and **still emits `LLMMetrics` with `cancelled=True`**
(`agents/llm/llm.py:401,446`) — the tokens were spent. The bridge must therefore expect
`metrics.llm` entries with no turn behind them, and `prices.py` must count them.

`resume_false_interruption` is the library's answer to a room that cuts the agent off and then says
nothing. It is NOT an answer to a room that says something: a television that transcribes into real
sentences becomes a turn, and neither the VAD nor the turn detector filters it. We wrote an energy
gate for that in ms-3 and deleted it in tk-4009d9 — five measured calls, the numbers and why an
energy gate of that shape cannot work are in `voice-bridge.md`.

## 10 — handoff

`Agent.__init__` seeds `self._chat_ctx` from the `chat_ctx=` argument or leaves it
**empty** (`agents/voice/agent.py:81`). A handoff inserts an `AgentHandoff` item into the
**session's** context and emits `conversation_item_added` for it
(`agent_session.py:1774-1788`); it copies nothing into the new agent. `session.history`
(`agent_session.py:773`) is the whole conversation; `agent.chat_ctx` is one agent's slice.
Carrying history across a handoff is the author's explicit act, exactly as in 1.7.1.

## 11 · 12 — the server

`AgentServer.rtc_session` raises if a second entrypoint is registered
(`agents/worker.py:502`) — the "one rtc_session" half of our rule is the library's. The
`agent_name` half is **ours**: it resolves `LIVEKIT_AGENT_NAME_OVERRIDE` → the argument →
`LIVEKIT_AGENT_NAME` → `""` (`worker.py:512-520`), and `""` means implicit dispatch to every
room (`worker.py:219`). Nothing refuses it, so our own test must.
**Prewarm is livekit's own, and this is the verdict that changed.** `AgentServer` appends
`livekit.agents.inference._warmup` to the forkserver preload list (`worker.py:747-759`); that
module is three lines — `import livekit.local_inference as _li; _li.init_vad(); _li.init_eot()`
(`inference/_warmup.py:7-10`) — so the native VAD and the end-of-turn weights are paged into the
forkserver **before** any job, and every forked job inherits them by COW. No MODEL of ours belongs
in a `setup_fnc`, then — `a_server` registers one all the same, for our own vendor modules, and
the paragraph below the preload says why the two are not the same thing.

The preload is conditional: `if self._mp_ctx_str == "forkserver"` (`worker.py:747`), and the
default multiprocessing context is `"forkserver"` on Linux and `"spawn"` everywhere else
(`worker.py:259-260`). So our box — Linux, the container — gets the COW preload; a laptop running
`pinecall-runtime worker talk` spawns, and the first call of each job process pays the load lazily inside the
native singleton. We do not override the context: `spawn` is what livekit chose for macOS.

`initialize_process_timeout` is `10.0` seconds (`worker.py:215`) — the budget for whatever a
`setup_fnc` would do. `WorkerOptions` is an alias of `ServerOptions` (`worker.py:285`) and
`prewarm_fnc` is now `setup_fnc` (`worker.py:326`).

Prewarm of livekit's models is livekit's; the process's own imports are `setup_fnc`'s. Our vendor
tables are filled by importing their package, and that import cost a call between 1.3 s and 4.2 s
inside the job — so `warmed` reads them in the idle process (`worker.py:620` hands `setup_fnc` to
the pool as `initialize_process_fnc`), which is a different thing from the model prewarm ms-3
deleted. A per-call cache instead would be a cache of one: every job gets its own process, handed
out warmed and consumed (`ipc/proc_pool.py:164,251`). See `docs/decisions/worker.md`.

## 15 · 16 · 17 — the plugin defaults the providers card must not inherit

`livekit-plugins-deepgram` and `livekit-plugins-soniox` already default to `sample_rate=16000`
(`plugins/deepgram/stt.py:92`, `stt_v2.py:74`, `plugins/soniox/stt.py:119`) — the milestone's rule
that a PSTN call is upsampled rather than talked down to 8000 is satisfied without an argument.

**The silero plugin's `min_silence_duration=0.55` is not the number a session runs.** An
`AgentSession` given no `vad=` builds `inference.VAD(model="silero")` (`agent_session.py:606-607`)
— the native model in `livekit-local-inference`, not the plugin — and its own default
`min_silence_duration` is **0.25** (`inference/vad.py:64`), which is the value we would have asked
for. `livekit-plugins-silero` is not installed here and nothing imports it; the 0.55 in the 1.7.1
notes was a comparison against a class no call constructs.

**`livekit-plugins-elevenlabs` defaults to `model="eleven_turbo_v2_5"`**
(`plugins/elevenlabs/tts.py:109`), which our rules forbid outright, next to `eleven_v3`
(`plugins/elevenlabs/models.py:8,11`). `providers/` must name the model on every construction;
a default is a bug waiting for the day somebody omits it.

### 17 — what `livekit.agents.inference` is, and why `providers/` still exists

Two unrelated things live in one package (`inference/__init__.py:32`), and the invariants card
missed both.

**The local half** is `inference.VAD` and `inference.TurnDetector`: the native VAD and end-of-turn
models of `livekit-local-inference`, loaded by the `.so` at import and warmed in the forkserver
(verdict 12). `AgentSession` builds both by itself — `turn_handling["turn_detection"]` defaults
eagerly to `inference.TurnDetector()` (`agent_session.py:541-542`) and `vad=` to `inference.VAD`
(`:606-607`). `TurnDetector` resolves an unset `version` by reading the environment — `v1`, the
hosted one, when `is_hosted()` or dev mode, else the local `v1-mini`
(`inference/eot/detector.py:55-59`) — so `session/voice/session.py` names `version="v1-mini"` out loud
rather than letting a variable decide whether a caller's transcript leaves the box.

**The gateway half** is `inference.STT` / `TTS` / `LLM`, which reach LiveKit Cloud's own
agent-gateway by model string. Their model lists are closed unions —
`STTModels` (`inference/stt.py:377`: deepgram, cartesia, assemblyai, xai, speechmatics, inworld,
google), `TTSModels` (`tts.py:80`: cartesia, deepgram, rime, inworld, xai, fishaudio), `LLMModels`
(`llm.py:175`: openai, google, kimi, deepseek, z-ai, xai) — and **Soniox, ElevenLabs and Anthropic
are in none of them**. That is why `providers/` exists at all, and why it holds those three
plugins and no VAD or turn table.

`from_model_string` is not free either: it builds the gateway client, which reads
`LIVEKIT_INFERENCE_API_KEY` → `LIVEKIT_API_KEY` and raises without one (`llm.py:243-249`), against
`https://agent-gateway.livekit.cloud/v1` by default (`inference/_utils.py:14,54-70`). A
self-hosted box has no such key, which settles the question for us.

## 20 — the conversation knobs livekit's own examples set

Read against `examples/voice_agents/basic_agent.py:73-112` at HEAD 2026-09-07. The skeleton was
ours already; these four were never set, and what we do with them is `docs/decisions/worker.md`.

`tts_text_transforms` (`agent_session.py:387`) takes `"filter_markdown"`, `"filter_emoji"` and any
callable. Both filters are ALREADY the default (`agent_session.py:351`), so the only reason to
name them is that the parameter replaces the list rather than extending it — a tenant's
`text_transforms.replace({…})` passed alone would take the asterisks out loud with it.

`stt_context_options` (`agent_session.py:380`, shape at `keyterm_detection.py:62`) supersedes the
deprecated `keyterms_options`. `keyterms` reaches only the STTs advertising
`STTCapabilities.keyterms` (`stt/stt.py:139,293`) — Deepgram Flux does (`stt_v2.py:124`), Soniox
does not (`soniox/stt.py:185-191`) and takes `context.terms` instead. `keyterm_detection` is an
LLM call every `turn_interval` (`keyterm_detection.py:88-96`) to guess words the agent could have
declared. An EMPTY keyterms list reaches no STT at all (`keyterm_detection.py:253`).

`interruption.resume_false_interruption` is `True` by default and `false_interruption_timeout`
`2.0` (`turn.py:195-196`); the example sets `1.0`. The behaviour convo hand-rolled is the
library's, and only the number is a decision. `ctx.log_context_fields` is a plain assignment on
`JobContext`; every livekit log line of the process carries it afterwards.
