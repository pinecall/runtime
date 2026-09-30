# The metrics

Every field livekit-agents 1.8 measures, block by block, with its unit and who measures it. Times are seconds unless the unit says otherwise; timestamps are unix seconds; a field the measuring side could not fill is absent, never zero. The story of how they join is in `README.md`; the models are `pinecall/wire/metrics.py`.

| block | field | unit | required | measured by | meaning |
|---|---|---|---|---|---|
| `Metadata` | `model_name` | name | no | the provider plugin | The model's name as the provider plugin reports it. |
| `Metadata` | `model_provider` | name | no | the provider plugin | The provider's name as the plugin reports it: anthropic, elevenlabs, soniox. |
| `LLMMetrics` | `type` | tag | yes | the session's llm node | livekit's tag for this block. |
| `LLMMetrics` | `label` | name | yes | the session's llm node | The node that measured: the plugin's label for its LLM. |
| `LLMMetrics` | `request_id` | id | yes | the session's llm node | The provider's id for this request, for correlating with their logs. |
| `LLMMetrics` | `timestamp` | unix s | yes | the session's llm node | When the request completed, unix seconds. |
| `LLMMetrics` | `duration` | s | yes | the session's llm node | The whole request, first byte sent to last token received, seconds. |
| `LLMMetrics` | `ttft` | s | yes | the session's llm node | Time to first token, seconds. -1 when the reply produced no token. |
| `LLMMetrics` | `cancelled` | flag | yes | the session's llm node | True when the request was cut short, usually because the caller interrupted. |
| `LLMMetrics` | `completion_tokens` | tokens | yes | the session's llm node | Tokens the model wrote, reasoning included. |
| `LLMMetrics` | `prompt_tokens` | tokens | yes | the session's llm node | Tokens the model read, cache hits included. |
| `LLMMetrics` | `prompt_cached_tokens` | tokens | yes | the session's llm node | Of the prompt tokens, how many were served from the provider's cache. |
| `LLMMetrics` | `cache_creation_tokens` | tokens | no | the session's llm node | Prompt tokens written into the cache on this request (Anthropic cache writes). 0 when the provider does not report it. |
| `LLMMetrics` | `reasoning_tokens` | tokens | no | the session's llm node | Completion tokens spent on hidden reasoning. Already inside completion_tokens; never add it again. |
| `LLMMetrics` | `total_tokens` | tokens | yes | the session's llm node | prompt_tokens plus completion_tokens, as the provider counts them. |
| `LLMMetrics` | `tokens_per_second` | tokens/s | yes | the session's llm node | completion_tokens over the streaming window. |
| `LLMMetrics` | `speech_id` | id | no | the session's llm node | The reply this request served, joining it to turn.agent. Null for a request outside a reply. |
| `LLMMetrics` | `metadata` | object | no | the session's llm node | The model and provider. |
| `STTMetrics` | `type` | tag | yes | the session's stt node | livekit's tag for this block. |
| `STTMetrics` | `label` | name | yes | the session's stt node | The node that measured: the plugin's label for its STT. |
| `STTMetrics` | `request_id` | id | yes | the session's stt node | The provider's id for this request. |
| `STTMetrics` | `timestamp` | unix s | yes | the session's stt node | When the request completed, unix seconds. |
| `STTMetrics` | `duration` | s | yes | the session's stt node | The request's length in seconds. 0 when the STT streams, since a stream has no single request. |
| `STTMetrics` | `audio_duration` | s | yes | the session's stt node | How much audio was pushed to the recognizer, seconds. |
| `STTMetrics` | `input_tokens` | tokens | no | the session's stt node | Audio tokens in, for providers that bill by token. 0 otherwise. |
| `STTMetrics` | `output_tokens` | tokens | no | the session's stt node | Text tokens out, for providers that bill by token. 0 otherwise. |
| `STTMetrics` | `streamed` | flag | yes | the session's stt node | True when the recognizer ran over a websocket stream rather than one request per utterance. |
| `STTMetrics` | `acquire_time` | s | no | the session's stt node | Seconds spent getting a connection before the audio could flow. Websocket providers only. |
| `STTMetrics` | `connection_reused` | flag | no | the session's stt node | True when the connection came from the pool rather than a fresh handshake. |
| `STTMetrics` | `metadata` | object | no | the session's stt node | The model and provider. |
| `TTSMetrics` | `type` | tag | yes | the session's tts node | livekit's tag for this block. |
| `TTSMetrics` | `label` | name | yes | the session's tts node | The node that measured: the plugin's label for its TTS. |
| `TTSMetrics` | `request_id` | id | yes | the session's tts node | The provider's id for this request. |
| `TTSMetrics` | `timestamp` | unix s | yes | the session's tts node | When the request completed, unix seconds. |
| `TTSMetrics` | `ttfb` | s | yes | the session's tts node | Time to first byte of audio after the text was sent, seconds. |
| `TTSMetrics` | `duration` | s | yes | the session's tts node | The whole request, seconds. |
| `TTSMetrics` | `audio_duration` | s | yes | the session's tts node | How much audio came back, seconds of playback. |
| `TTSMetrics` | `cancelled` | flag | yes | the session's tts node | True when synthesis was cut short, usually by an interruption. |
| `TTSMetrics` | `characters_count` | chars | yes | the session's tts node | Characters synthesized, what character-billed providers charge for. |
| `TTSMetrics` | `input_tokens` | tokens | no | the session's tts node | Text tokens in, for token-billed TTS. 0 otherwise. |
| `TTSMetrics` | `output_tokens` | tokens | no | the session's tts node | Audio tokens out, for token-billed TTS. 0 otherwise. |
| `TTSMetrics` | `streamed` | flag | yes | the session's tts node | True when the audio streamed back over a websocket. |
| `TTSMetrics` | `acquire_time` | s | no | the session's tts node | Seconds spent getting a connection first. Websocket providers only. |
| `TTSMetrics` | `connection_reused` | flag | no | the session's tts node | True when the connection came from the pool. |
| `TTSMetrics` | `segment_id` | id | no | the session's tts node | Which sentence segment of the reply this was. |
| `TTSMetrics` | `speech_id` | id | no | the session's tts node | The reply this audio belongs to, joining it to turn.agent. |
| `TTSMetrics` | `metadata` | object | no | the session's tts node | The model and provider. |
| `VADMetrics` | `type` | tag | yes | the VAD plugin | livekit's tag for this block. |
| `VADMetrics` | `label` | name | yes | the VAD plugin | The VAD plugin's label. |
| `VADMetrics` | `timestamp` | unix s | yes | the VAD plugin | When the report was cut, unix seconds. |
| `VADMetrics` | `idle_time` | s | yes | the VAD plugin | Seconds the detector spent waiting for audio in this window. |
| `VADMetrics` | `inference_duration_total` | s | yes | the VAD plugin | Seconds spent inside the model in this window. |
| `VADMetrics` | `inference_count` | count | yes | the VAD plugin | How many frames the model scored in this window. |
| `VADMetrics` | `metadata` | object | no | the VAD plugin | The model and provider. |
| `EOUMetrics` | `type` | tag | yes | the session's turn detection | livekit's tag for this block. |
| `EOUMetrics` | `timestamp` | unix s | yes | the session's turn detection | When the turn was closed, unix seconds. |
| `EOUMetrics` | `end_of_utterance_delay` | s | yes | the session's turn detection | Seconds from the VAD hearing silence to the decision that the turn is over. 0 when no end of speech was detected. |
| `EOUMetrics` | `transcription_delay` | s | yes | the session's turn detection | Seconds from the end of speech to the final transcript. 0 when no end of speech was detected. |
| `EOUMetrics` | `on_user_turn_completed_delay` | s | yes | the session's turn detection | Seconds the app's on_user_turn_completed hook took. |
| `EOUMetrics` | `speech_id` | id | no | the session's turn detection | The reply this turn triggered, joining it to turn.user and turn.agent. |
| `EOUMetrics` | `metadata` | object | no | the session's turn detection | The model and provider. |
| `EOTInferenceMetrics` | `type` | tag | yes | the end-of-turn model | livekit's tag for this block. |
| `EOTInferenceMetrics` | `timestamp` | unix s | yes | the end-of-turn model | When the prediction came back, unix seconds. |
| `EOTInferenceMetrics` | `total_duration` | s | yes | the end-of-turn model | Seconds from the earliest audio in the inference to the answer. |
| `EOTInferenceMetrics` | `detection_delay` | s | yes | the end-of-turn model | Seconds from the latest audio in the inference to the answer. |
| `EOTInferenceMetrics` | `prediction_duration` | s | yes | the end-of-turn model | Seconds the model itself took. |
| `EOTInferenceMetrics` | `num_requests` | count | no | the end-of-turn model | How many requests one inference needed. Usually 1. |
| `EOTInferenceMetrics` | `metadata` | object | no | the end-of-turn model | The model and provider. |
| `InterruptionMetrics` | `type` | tag | yes | the interruption detector | livekit's tag for this block. |
| `InterruptionMetrics` | `timestamp` | unix s | yes | the interruption detector | When the inference came back, unix seconds. |
| `InterruptionMetrics` | `total_duration` | s | yes | the interruption detector | Round trip of the latest inference, seconds. |
| `InterruptionMetrics` | `prediction_duration` | s | yes | the interruption detector | The model's own time on the latest inference, seconds. |
| `InterruptionMetrics` | `detection_delay` | s | yes | the interruption detector | Seconds from the onset of speech to the final prediction, latest inference. |
| `InterruptionMetrics` | `num_interruptions` | count | yes | the interruption detector | Interruptions detected so far in the session. |
| `InterruptionMetrics` | `num_backchannels` | count | yes | the interruption detector | Backchannels ('mm-hm', 'sí') detected so far and not treated as interruptions. |
| `InterruptionMetrics` | `num_requests` | count | yes | the interruption detector | Requests sent to the detector so far. |
| `InterruptionMetrics` | `metadata` | object | no | the interruption detector | The model and provider. |
| `RealtimeCachedTokenDetails` | `audio_tokens` | tokens | no | the realtime plugin | Cached audio tokens. |
| `RealtimeCachedTokenDetails` | `text_tokens` | tokens | no | the realtime plugin | Cached text tokens. |
| `RealtimeCachedTokenDetails` | `image_tokens` | tokens | no | the realtime plugin | Cached image tokens. |
| `RealtimeInputTokenDetails` | `audio_tokens` | tokens | no | the realtime plugin | Audio tokens read. |
| `RealtimeInputTokenDetails` | `text_tokens` | tokens | no | the realtime plugin | Text tokens read. |
| `RealtimeInputTokenDetails` | `image_tokens` | tokens | no | the realtime plugin | Image tokens read. |
| `RealtimeInputTokenDetails` | `cached_tokens` | tokens | no | the realtime plugin | Of all input tokens, how many came from the cache. |
| `RealtimeInputTokenDetails` | `cached_tokens_details` | object | no | the realtime plugin | The cached tokens by kind, when the provider breaks them out. |
| `RealtimeOutputTokenDetails` | `text_tokens` | tokens | no | the realtime plugin | Text tokens produced. |
| `RealtimeOutputTokenDetails` | `audio_tokens` | tokens | no | the realtime plugin | Audio tokens produced. |
| `RealtimeOutputTokenDetails` | `image_tokens` | tokens | no | the realtime plugin | Image tokens produced. Realtime models no longer emit these; kept because the library keeps it. |
| `RealtimeModelMetrics` | `type` | tag | yes | the realtime plugin | livekit's tag for this block. |
| `RealtimeModelMetrics` | `label` | name | no | the realtime plugin | The realtime plugin's label. |
| `RealtimeModelMetrics` | `request_id` | id | yes | the realtime plugin | The provider's id for the response. |
| `RealtimeModelMetrics` | `timestamp` | unix s | yes | the realtime plugin | When the response was created, unix seconds. |
| `RealtimeModelMetrics` | `duration` | s | no | the realtime plugin | Seconds from response created to response done. |
| `RealtimeModelMetrics` | `session_duration` | s | no | the realtime plugin | Seconds the session connection has been open, for providers that bill by session time. |
| `RealtimeModelMetrics` | `ttft` | s | no | the realtime plugin | Seconds to the first audio token. -1 when no audio token was sent. |
| `RealtimeModelMetrics` | `cancelled` | flag | no | the realtime plugin | True when the response was cut short. |
| `RealtimeModelMetrics` | `input_tokens` | tokens | no | the realtime plugin | Tokens read, text and audio together. |
| `RealtimeModelMetrics` | `output_tokens` | tokens | no | the realtime plugin | Tokens produced, text and audio together. |
| `RealtimeModelMetrics` | `total_tokens` | tokens | no | the realtime plugin | input_tokens plus output_tokens. |
| `RealtimeModelMetrics` | `tokens_per_second` | tokens/s | no | the realtime plugin | Output tokens over the response's duration. |
| `RealtimeModelMetrics` | `input_token_details` | object | yes | the realtime plugin | What a realtime model read, by kind, with the cached part broken out. |
| `RealtimeModelMetrics` | `output_token_details` | object | yes | the realtime plugin | What a realtime model produced, by kind. |
| `RealtimeModelMetrics` | `acquire_time` | s | no | the realtime plugin | Seconds spent getting a connection first. |
| `RealtimeModelMetrics` | `connection_reused` | flag | no | the realtime plugin | True when the connection came from the pool. |
| `RealtimeModelMetrics` | `metadata` | object | no | the realtime plugin | The model and provider. |
| `AvatarMetrics` | `type` | tag | yes | the avatar worker | livekit's tag for this block. |
| `AvatarMetrics` | `timestamp` | unix s | yes | the avatar worker | When the report was cut, unix seconds. |
| `AvatarMetrics` | `playback_latency` | s | no | the avatar worker | Seconds between forwarding the first audio frame to the avatar and the avatar reporting playback. |
| `AvatarMetrics` | `session_started_time` | unix s | no | the avatar worker | When the avatar session started, unix seconds. |
| `AvatarMetrics` | `avatar_joined_time` | unix s | no | the avatar worker | When the avatar participant joined the room with its video track, unix seconds. |
| `AvatarMetrics` | `metadata` | object | no | the avatar worker | The model and provider. |
| `TurnMetadata` | `model_name` | name | no | the session, on the ChatMessage | The model's name as the plugin reports it. |
| `TurnMetadata` | `model_provider` | name | no | the session, on the ChatMessage | The provider's name as the plugin reports it. |
| `UserTurnMetrics` | `started_speaking_at` | unix s | no | the session, on the user ChatMessage | When the caller began speaking, unix seconds. |
| `UserTurnMetrics` | `stopped_speaking_at` | unix s | no | the session, on the user ChatMessage | When the caller stopped speaking, unix seconds. |
| `UserTurnMetrics` | `transcription_delay` | s | no | the session, on the user ChatMessage | Seconds from the end of speech to the final transcript. |
| `UserTurnMetrics` | `end_of_turn_delay` | s | no | the session, on the user ChatMessage | Seconds from the end of speech to the decision that the turn was over. |
| `UserTurnMetrics` | `on_user_turn_completed_delay` | s | no | the session, on the user ChatMessage | Seconds the app's on_user_turn_completed hook took. |
| `UserTurnMetrics` | `stt_metadata` | object | no | the session, on the user ChatMessage | Which model handled one leg of a turn. |
| `AgentTurnMetrics` | `started_speaking_at` | unix s | no | the session, on the assistant ChatMessage | When the first audio frame of the reply went out, unix seconds. |
| `AgentTurnMetrics` | `stopped_speaking_at` | unix s | no | the session, on the assistant ChatMessage | When the last audio frame of the reply went out, unix seconds. |
| `AgentTurnMetrics` | `llm_node_ttft` | s | no | the session, on the assistant ChatMessage | Seconds until the llm node returned its first token. |
| `AgentTurnMetrics` | `llm_node_tps` | tokens/s | no | the session, on the assistant ChatMessage | Output tokens per second over the streaming window. Absent for a reply that arrived in one chunk. |
| `AgentTurnMetrics` | `llm_node_ttfs` | s | no | the session, on the assistant ChatMessage | Seconds from generation start until the first sentence reached the TTS. Absent when no audio came from a LiveKit TTS. |
| `AgentTurnMetrics` | `tts_node_ttfb` | s | no | the session, on the assistant ChatMessage | Seconds until the tts node returned its first audio chunk, counted from the first text token. |
| `AgentTurnMetrics` | `playback_latency` | s | no | the session, on the assistant ChatMessage | Seconds between forwarding the first audio frame and the output reporting playback. Near zero on a room; meaningful with an avatar. |
| `AgentTurnMetrics` | `e2e_latency` | s | no | the session, on the assistant ChatMessage | Seconds from the caller finishing to the agent starting to answer. The number a caller feels. |
| `AgentTurnMetrics` | `provider_request_ids` | ids | no | the session, on the assistant ChatMessage | The provider-side request ids behind this reply, for correlating with their logs. |
| `AgentTurnMetrics` | `llm_metadata` | object | no | the session, on the assistant ChatMessage | Which model handled one leg of a turn. |
| `AgentTurnMetrics` | `tts_metadata` | object | no | the session, on the assistant ChatMessage | Which model handled one leg of a turn. |
| `LLMModelUsage` | `type` | tag | yes | the session's usage collector | livekit's tag for this row. |
| `LLMModelUsage` | `provider` | name | yes | the session's usage collector | The provider: anthropic, openai. |
| `LLMModelUsage` | `model` | name | yes | the session's usage collector | The model name. |
| `LLMModelUsage` | `input_tokens` | tokens | no | the session's usage collector | All tokens read. |
| `LLMModelUsage` | `input_cached_tokens` | tokens | no | the session's usage collector | Of the input, tokens served from cache. |
| `LLMModelUsage` | `input_cache_creation_tokens` | tokens | no | the session's usage collector | Of the input, tokens written into the cache. |
| `LLMModelUsage` | `input_audio_tokens` | tokens | no | the session's usage collector | Audio tokens read, multimodal models only. |
| `LLMModelUsage` | `input_cached_audio_tokens` | tokens | no | the session's usage collector | Cached audio tokens read. |
| `LLMModelUsage` | `input_text_tokens` | tokens | no | the session's usage collector | Text tokens read, when the provider breaks input out by kind. |
| `LLMModelUsage` | `input_cached_text_tokens` | tokens | no | the session's usage collector | Cached text tokens read. |
| `LLMModelUsage` | `input_image_tokens` | tokens | no | the session's usage collector | Image tokens read, multimodal models only. |
| `LLMModelUsage` | `input_cached_image_tokens` | tokens | no | the session's usage collector | Cached image tokens read. |
| `LLMModelUsage` | `output_tokens` | tokens | no | the session's usage collector | All tokens written, reasoning included. |
| `LLMModelUsage` | `output_audio_tokens` | tokens | no | the session's usage collector | Audio tokens written, multimodal models only. |
| `LLMModelUsage` | `output_text_tokens` | tokens | no | the session's usage collector | Text tokens written, when the provider breaks output out by kind. |
| `LLMModelUsage` | `output_reasoning_tokens` | tokens | no | the session's usage collector | Tokens spent on hidden reasoning. Already inside output_tokens. |
| `LLMModelUsage` | `session_duration` | s | no | the session's usage collector | Seconds of session connection, for providers that bill by session time. |
| `TTSModelUsage` | `type` | tag | yes | the session's usage collector | livekit's tag for this row. |
| `TTSModelUsage` | `provider` | name | yes | the session's usage collector | The provider: elevenlabs. |
| `TTSModelUsage` | `model` | name | yes | the session's usage collector | The model name. |
| `TTSModelUsage` | `input_tokens` | tokens | no | the session's usage collector | Text tokens in, for token-billed TTS. |
| `TTSModelUsage` | `output_tokens` | tokens | no | the session's usage collector | Audio tokens out, for token-billed TTS. |
| `TTSModelUsage` | `characters_count` | chars | no | the session's usage collector | Characters synthesized, for character-billed TTS. |
| `TTSModelUsage` | `audio_duration` | s | no | the session's usage collector | Seconds of audio generated. |
| `STTModelUsage` | `type` | tag | yes | the session's usage collector | livekit's tag for this row. |
| `STTModelUsage` | `provider` | name | yes | the session's usage collector | The provider: soniox, deepgram. |
| `STTModelUsage` | `model` | name | yes | the session's usage collector | The model name. |
| `STTModelUsage` | `input_tokens` | tokens | no | the session's usage collector | Audio tokens in, for token-billed STT. |
| `STTModelUsage` | `output_tokens` | tokens | no | the session's usage collector | Text tokens out, for token-billed STT. |
| `STTModelUsage` | `audio_duration` | s | no | the session's usage collector | Seconds of audio recognized. |
| `InterruptionModelUsage` | `type` | tag | yes | the session's usage collector | livekit's tag for this row. |
| `InterruptionModelUsage` | `provider` | name | yes | the session's usage collector | The provider: livekit. |
| `InterruptionModelUsage` | `model` | name | yes | the session's usage collector | The model name. |
| `InterruptionModelUsage` | `total_requests` | count | no | the session's usage collector | Requests sent to the detector. |
| `EOTModelUsage` | `type` | tag | yes | the session's usage collector | livekit's tag for this row. |
| `EOTModelUsage` | `provider` | name | yes | the session's usage collector | The provider: livekit. |
| `EOTModelUsage` | `model` | name | yes | the session's usage collector | The model name. |
| `EOTModelUsage` | `total_requests` | count | no | the session's usage collector | Inference requests sent to the model. |

## Per day, by vendor and by version

`GET /v1/insights` reads three of the turn's numbers across a day of the scope's calls (the day a
call started, in UTC), grouped by stage and by the vendor and model the turn's own report names:
`stt` is `UserTurnMetrics.transcription_delay` under `stt_metadata`, with the average of the turn's
`transcript_confidence`; `llm` is `AgentTurnMetrics.llm_node_ttft` under `llm_metadata`; `tts` is
`AgentTurnMetrics.tts_node_ttfb` under `tts_metadata`. Each row has how many turns reported the
stage, the median and the 95th percentile in seconds, so "the ears are 500 ms slower than
yesterday" is two rows of two days, and "the ears are wrong" is the confidence falling. The vendor
is `model_provider` as the plugin reports it, and absent when the report named none.

They are counted once per call, when it is sealed, from the call's own log (`log/drift.py`), never
read off the log at the door: a day holds the calls sealed in it, not those still going. Each
stage's seconds are kept as a fixed histogram (`log/_histogram.py`: under 10 ms, 92 buckets each
10 % wider than the one below it up to 63 s, and one for anything slower), so the calls of a day,
of an agent and of a version add bucket by bucket, and a median or p95 read off the buckets is
within 5 % of the exact one. The same fold counts each judge's settled verdicts of the call, by
the version of the agent's settings it ran on and the hash of the judge's question, which is what
`GET /v1/insights/drift` compares ([console-api.md](../protocol/console-api.md)); a call judged
again (`POST /v1/evals/judge/{call}?again=true`) replaces its verdicts rather than adding them.
`pinecall-runtime drift rebuild` counts them again from the log ([the-runtime-cli.md](../the-runtime-cli.md)).
