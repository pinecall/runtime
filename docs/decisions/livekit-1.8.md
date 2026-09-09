# livekit-agents 1.8.0 — the invariants, re-verified

`~/prueba-abai` (convo) pinned **1.7.1** and its ms-10 read of the framework is what our
rules were written from. This tree runs **1.8.0**. Everything below was read in the
installed package — `runtime/.venv/lib/python3.12/site-packages/livekit/agents` — not in
documentation and not from memory. Paths are relative to `livekit/` inside that directory;
line numbers are 1.8.0's.

Confirm the version the verdicts belong to:

```
cd runtime && uv run python -c "import livekit.agents as a; print(a.__version__)"   # 1.8.0
```

`tests/protocol/test_livekit_invariants.py` pins keyless everything the chapters
below can reach, and the verdicts table itself; `tests/protocol/test_livekit_fields.py`
pins the metrics tables against the library and against `livekit-metrics.md`.

What livekit's own EXAMPLES set — a different question — is `docs/decisions/livekit-examples.md`.

## The verdicts, at a glance

| # | invariant | verdict | where |
|---|---|---|---|
| 1 | the framework drops an orphan `tool_use` / `tool_result` | **still true** | `agents/llm/_provider_format/utils.py:93,134,167` |
| 2 | a mid-conversation system message is rewritten as a user turn | **still true, and now the default for every provider** | `agents/llm/_provider_format/utils.py:49`, `anthropic.py:28` |
| 3 | the first system message is the cached prefix | **still true** | `plugins/anthropic/llm.py:226,234` |
| 4 | `update_instructions` rewrites one pinned item, in place, at index 0 | **still true** | `agents/voice/generation.py:1232,1249,1262` |
| 5 | `update_instructions` / `update_tools` invalidate the prompt cache | **still true, and now they also write an item into the history** | `agents/voice/agent_activity.py:597,614,631` |
| 6 | a config update or a handoff never reaches the provider | **new in 1.8, and it holds** | `agents/llm/chat_context.py:387,395,409` · `_provider_format/utils.py:157,199` |
| 7 | recording is opt-in and never leaves the box unless LiveKit Cloud is the host | **still true** | `agents/job.py:66,232,830` · `agents/voice/agent_session.py:898,1043` |
| 8 | `InterruptionOptions.min_words` is STT-only and defaults to 0 | **changed shape**: a `TypedDict` under `turn_handling`, not a kwarg | `agents/voice/turn.py:172,194` · `agent_activity.py:2146,2483` |
| 9 | preemptive generation is off by default | **CHANGED — it is ON by default in 1.8** | `agents/voice/turn.py:223` |
| 10 | a handoff carries no history to the new agent | **still true** | `agents/voice/agent.py:81` · `agent_session.py:773,1774` |
| 11 | one `AgentServer`, one `rtc_session`, `agent_name` never empty | **library-enforced for the first half only** | `agents/worker.py:219,502,512` |
| 12 | prewarm is ours to write | **half — livekit preloads its own two models**; the 10 s budget goes on OUR vendor imports, in a `setup_fnc` | `agents/worker.py:747,215,620` · `agents/inference/_warmup.py:7` |
| 13 | `metrics_collected` carries every typed block | **still true, but the session-level event is deprecated** | `agents/voice/agent_session.py:725` · `agent_activity.py:1967` |
| 14 | a turn's own latencies ride the `ChatMessage` | **new in 1.8** | `agents/llm/chat_context.py:232,318` · `agent_activity.py:3568,3620` |
| 15 | the STT default sample rate is 16000, and the session's own VAD already waits 0.25 | **the STT half still true; the silero PLUGIN's 0.55 is irrelevant — no session builds it** | `plugins/deepgram/stt.py:92`, `stt_v2.py:74` · `soniox/stt.py:119` · `agents/inference/vad.py:64` |
| 16 | the ElevenLabs plugin default is a model we forbid | **true, and it is a trap** | `plugins/elevenlabs/tts.py:109` |
| 17 | `livekit.agents.inference` — the native VAD and EOT, and the Cloud gateway by model string | **it carries none of our three vendors** | `agents/inference/__init__.py:32` · `stt.py:377` · `tts.py:80` · `llm.py:175,243,249` |
| 18 | a streaming STT's `STTMetrics` is a per-turn block | **NO — it is a usage meter, and the plugin picks its cadence** | `agents/stt/stt.py:519,529` · `agents/metrics/base.py:54` · `plugins/soniox/stt.py:604` · `plugins/deepgram/stt.py:484` |
| 19 | word timings come off the TTS, and only if the session asks | **they never reach the agent unless `use_tts_aligned_transcript` is on** | `agents/voice/agent_activity.py:588,3014,3542,120` · `agents/voice/generation.py:437,514` · `plugins/elevenlabs/tts.py:166,699,1332` |
| 20 | the conversation knobs the official examples set — `tts_text_transforms`, `stt_context_options`, `interruption.false_interruption_timeout`, `log_context_fields` | **all four exist and all four were unset**; the two text filters are already the default, the resume already the default, the numbers and the words are ours | `agents/voice/agent_session.py:351,380,387` · `voice/turn.py:195` · `voice/keyterm_detection.py:62` · `examples/voice_agents/basic_agent.py:73-112` |
| 21 | evaluating an agent is ours to write | **NO — 1.8 ships two evaluation surfaces**: an in-process turn-level framework (`session.run` · `RunResult.expect` · `.judge` · `mock_tools`) and an `agents.evals` package of judges. What it does NOT ship is the simulator: a persona, a scenario run and its verdict are a LiveKit Cloud service | `agents/voice/agent_session.py:809,1034` · `agents/voice/run_result.py:98,142,317,953,1132` · `agents/voice/generation.py:951` · `agents/evals/__init__.py:20` · `agents/simulation.py:13,43,139` |
| 22 | which participant the session listens to is livekit's to choose | **it takes the first one of an accepted kind** — a room holding a listener or a supervisor makes that a coin toss. `RoomOptions.participant_identity` pins it before RoomIO builds the audio input; `set_participant` moves it after. And in 1.8 `RoomInputOptions` is DEPRECATED: the same field lives on `RoomOptions` | `agents/voice/room_io/room_io.py:60,117,303,385` · `room_io/types.py:125,171,241` · `agents/job.py:151` · `examples/telephony/amd.py:73` |

## The chapters — one file per thing a reader comes here for

| invariants | what it holds | file |
|---|---|---|
| #1 · #2 · #3 · #4 · #5 · #6 | the chat context on its way to the model: the formatter, the pinned instructions item, the two new history items | [livekit-context.md](livekit-context.md) |
| #7 · #8 · #9 · #10 · #11 · #12 · #15 · #16 · #17 · #20 | the session and the server: recording, interruption, preemptive generation, handoff, `AgentServer` and the forkserver preload, the plugin defaults, the conversation knobs | [livekit-session.md](livekit-session.md) |
| #13 · #14 · #18 | every metric the library measures, where each is produced, and the class-by-class table | [livekit-metrics.md](livekit-metrics.md) |
| #19 | where a word timing comes from, and why the session must ask for it | [livekit-words.md](livekit-words.md) |
| #22 | which seat in the room the session hears, and why it is pinned before start | [voice-bridge.md](voice-bridge.md) |
| #21 | livekit's own evaluation surfaces, what each ring takes from them, and what we add on top | [evals-rings-1-and-2.md](evals-rings-1-and-2.md) · [evals-judges.md](evals-judges.md) |
