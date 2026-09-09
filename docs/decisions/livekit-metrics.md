# livekit-agents 1.8.0 — every metric it measures, and where each one is produced

Invariants 13, 14 and 18 of [livekit-1.8.md](livekit-1.8.md), plus the class-by-class table
`test_livekit_fields.py` holds the library to. Nothing here was reworded: it was moved.

## 13 · 14 — where the metrics are produced

Two independent channels, and we carry both.

**The typed blocks.** Every component (`stt`, `tts`, `llm`, `vad`, the interruption detector,
the turn detector, the keyterm detector, a realtime session) emits `metrics_collected` on
itself; `AgentActivity` subscribes to all of them (`agent_activity.py:1044-1066`) and forwards
through `_on_metrics_collected` (`:1967`), which stamps `speech_id` on `LLMMetrics` and
`TTSMetrics` from the speech-handle contextvar (`:1971`), feeds
`AgentSession.usage` (`:1981`) and re-emits both `metrics_collected` (`:1983`) and
`session_usage_updated` (`:1984`). `EOUMetrics` is emitted straight onto the session
(`:2699`) and so never reaches the usage collector.

> **`AgentSession.on("metrics_collected")` is deprecated in 1.8** — subscribing logs a warning
> naming `session_usage_updated` and `ChatMessage.metrics` as the replacements
> (`agent_session.py:725-731`). The event still carries every block. The bridge subscribes to
> the **components** we built in `providers/` instead, which is the same data with no warning,
> and takes the session's `session_usage_updated` for the rows. Two consequences, both in
> `worker/bridge/metrics.py`: an emitter's listeners are a **set** (`rtc/event_emitter.py:15`),
> so ours may run before livekit's own stamps `speech_id` on the block (`:1971`) — the write
> waits one loop tick; and `EOUMetrics` has no component to hear it on (`:2699`), so it is rebuilt
> from the user turn's own `MetricsReport`, the way livekit builds it (`:2683-2697`).

**The per-turn latencies.** New in 1.8: `ChatMessage.metrics` is a `MetricsReport`
(`agents/llm/chat_context.py:232,318`), a total-false `TypedDict` — a field it could not
measure is simply absent. The agent's report is assembled before the message exists
(`agent_activity.py:3568-3597`) and handed to `add_message(metrics=…)` (`:3620`); the user's is
built by `_init_metrics_from_end_of_turn` (`:4768`), attached at `:2554`, and
`on_user_turn_completed_delay` is written into that same dict at `:2615`.

**The timing, which is the whole reason the bridge can be simple:** a message reaches
`conversation_item_added` (`agent_session.py:2065`) with its `metrics` **already complete**.
The typed blocks arrive earlier — `LLMMetrics` when the stream closes, `TTSMetrics` per
segment — so in the log `metrics.llm` precedes the `turn.agent` it belongs to, joined by
`speech_id`.

## The metrics, class by class

Every class and every field the library declares, in declaration order. This table is the
contract the protocol schema and the bridge are built against, and
`test_livekit_fields.py::test_the_decision_doc_names_every_metrics_class_and_field_the_library_declares`
fails the day the library adds a field to it. `RealtimeModelMetrics.InputTokenDetails` and
`.OutputTokenDetails` are nested classes; the wire flattens their names to
`RealtimeInputTokenDetails` / `RealtimeOutputTokenDetails`.

| class | where | fields |
|---|---|---|
| `Metadata` | `agents/metrics/base.py:8` | `model_name` `model_provider` |
| `STTMetrics` | `agents/metrics/base.py:49` | `type` `label` `request_id` `timestamp` `duration` `audio_duration` `input_tokens` `output_tokens` `streamed` `acquire_time` `connection_reused` `metadata` |
| `LLMMetrics` | `agents/metrics/base.py:20` | `type` `label` `request_id` `timestamp` `duration` `ttft` `cancelled` `completion_tokens` `prompt_tokens` `prompt_cached_tokens` `cache_creation_tokens` `reasoning_tokens` `total_tokens` `tokens_per_second` `speech_id` `metadata` |
| `TTSMetrics` | `agents/metrics/base.py:71` | `type` `label` `request_id` `timestamp` `ttfb` `duration` `audio_duration` `cancelled` `characters_count` `input_tokens` `output_tokens` `streamed` `acquire_time` `connection_reused` `segment_id` `speech_id` `metadata` |
| `VADMetrics` | `agents/metrics/base.py:96` | `type` `label` `timestamp` `idle_time` `inference_duration_total` `inference_count` `metadata` |
| `EOUMetrics` | `agents/metrics/base.py:106` | `type` `timestamp` `end_of_utterance_delay` `transcription_delay` `on_user_turn_completed_delay` `speech_id` `metadata` |
| `EOTInferenceMetrics` | `agents/metrics/base.py:127` | `type` `timestamp` `total_duration` `detection_delay` `prediction_duration` `num_requests` `metadata` |
| `RealtimeModelMetrics` | `agents/metrics/base.py:143` | `type` `label` `request_id` `timestamp` `duration` `session_duration` `ttft` `cancelled` `input_tokens` `output_tokens` `total_tokens` `tokens_per_second` `input_token_details` `output_token_details` `acquire_time` `connection_reused` `metadata` |
| `RealtimeModelMetrics.InputTokenDetails` | `agents/metrics/base.py:143` | `audio_tokens` `text_tokens` `image_tokens` `cached_tokens` `cached_tokens_details` |
| `RealtimeModelMetrics.OutputTokenDetails` | `agents/metrics/base.py:143` | `text_tokens` `audio_tokens` `image_tokens` |
| `InterruptionMetrics` | `agents/metrics/base.py:194` | `type` `timestamp` `total_duration` `prediction_duration` `detection_delay` `num_interruptions` `num_backchannels` `num_requests` `metadata` |
| `AvatarMetrics` | `agents/metrics/base.py:212` | `type` `timestamp` `playback_latency` `session_started_time` `avatar_joined_time` `metadata` |
| `LLMModelUsage` | `agents/metrics/usage.py:27` | `type` `provider` `model` `input_tokens` `input_cached_tokens` `input_cache_creation_tokens` `input_audio_tokens` `input_cached_audio_tokens` `input_text_tokens` `input_cached_text_tokens` `input_image_tokens` `input_cached_image_tokens` `output_tokens` `output_audio_tokens` `output_text_tokens` `output_reasoning_tokens` `session_duration` |
| `TTSModelUsage` | `agents/metrics/usage.py:68` | `type` `provider` `model` `input_tokens` `output_tokens` `characters_count` `audio_duration` |
| `STTModelUsage` | `agents/metrics/usage.py:87` | `type` `provider` `model` `input_tokens` `output_tokens` `audio_duration` |
| `InterruptionModelUsage` | `agents/metrics/usage.py:104` | `type` `provider` `model` `total_requests` |
| `EOTModelUsage` | `agents/metrics/usage.py:116` | `type` `provider` `model` `total_requests` |
| `MetricsReport` | `agents/llm/chat_context.py:232` | `started_speaking_at` `stopped_speaking_at` `transcription_delay` `end_of_turn_delay` `on_user_turn_completed_delay` `llm_node_ttft` `llm_node_tps` `llm_node_ttfs` `tts_node_ttfb` `playback_latency` `e2e_latency` `provider_request_ids` `llm_metadata` `tts_metadata` `stt_metadata` |
| `MetricsMetadata` | `agents/llm/chat_context.py:227` | `model_name` `model_provider` |

`AgentSessionUsage` (`agents/metrics/usage.py:133`) is one field, `model_usage`, a list of the
five `ModelUsage` rows above; `ModelUsageCollector` (`:137`) sums them. `call.summary` carries
that list whole.

## 18 — a streaming STT's `STTMetrics` is a usage meter, not a turn's block

Seen on the first real voice call (room `smoke-voice-1`): 73 stored `metrics.stt` entries for a
42-second call, one every ~120 ms of speech, every field identical except `timestamp` and
`audio_duration`. Read from the library, none of that is an anomaly.

**A streamed STT emits `STTMetrics` from exactly two places, and neither is a turn.** The first is
`STTStream._report_connection_acquired` (`agents/stt/stt.py:445-459`), once per websocket, with
zero usage and the only two numbers a stream ever measures about itself — `acquire_time` and
`connection_reused`. The second is `_metrics_monitor_task` (`agents/stt/stt.py:519-541`), which
turns **every** `RECOGNITION_USAGE` speech event into a block whose `audio_duration` is the
*incremental* seconds of audio processed since the last report (`stt.py:110-111`) and whose
`duration` is hard-coded `0.0` — as the field's own docstring says, "the request duration in
seconds, **0.0 if the STT is streaming**" (`agents/metrics/base.py:54-55`). The zero is the
contract, not a plugin bug.

**The cadence is the plugin's choice, and the two we ship differ by a factor of forty.** Deepgram
collects usage through a `PeriodicCollector(callback=self._on_audio_duration_report, duration=5.0)`
(`plugins/deepgram/stt.py:484-486`, emitted at `:801-809`): one block per five seconds of audio.
Soniox calls `_report_processed_audio_duration(total_audio_proc_ms)` for every frame it receives
off the websocket (`plugins/soniox/stt.py:604`, the report itself at `:312-326`) — hence one block
per partial, ~120 for a spoken sentence.

**No `STTMetrics` can be joined to a turn.** `AgentActivity._on_metrics_collected` stamps
`speech_id` from the speech-handle contextvar onto `LLMMetrics` and `TTSMetrics` **only**
(`agents/voice/agent_activity.py:1969-1972`), and `STTMetrics` declares no such field
(`agents/metrics/base.py:49-68`). There is therefore no "the `metrics.stt` of this turn" to keep,
and no last-one-of-the-turn to distinguish from the rest: the blocks are indistinguishable by
construction.

**The turn's STT numbers live elsewhere, and we already store them whole.** `transcription_delay`
and `end_of_turn_delay` ride the `ChatMessage.metrics` report (verdict 14) — the log writes them as
`turn.user.metrics` and, rebuilt, as `metrics.eou`. And the call's STT total is not lost by
dropping the ticks: livekit sums exactly these blocks into `STTModelUsage.audio_duration`
(`agents/metrics/usage.py:87-101`, summed by `ModelUsageCollector`, `:137`), which `call.summary`
carries whole and `providers/prices.py` bills from. On the smoke call the 73 ticks summed to the
42.48 s the summary row already carried.

What the bridge does with that is `docs/decisions/voice-bridge.md`, "The ticks a streaming STT
keeps emitting".
