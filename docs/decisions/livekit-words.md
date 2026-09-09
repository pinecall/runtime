# livekit-agents 1.8.0 — where a word timing comes from

Invariant 19 of [livekit-1.8.md](livekit-1.8.md), which holds the verdict table, the version it
was read in and the rest of the chapters. Nothing here was reworded: it was moved.

## 19 — where a word timing comes from, and why the session must ask for it

The console draws agent karaoke, and ms-3's first criterion asks the log for word timings. The
first complete real call (room `smoke-voice-5`) carried none. Nothing was broken: the timings
existed and the session was never told to use them.

**The voice measures them, and hands them up on the audio frames.** ElevenLabs is asked for
alignment by the `sync_alignment=true` query parameter, whose plugin default is `True` and which
also sets `TTSCapabilities.aligned_transcript` (`plugins/elevenlabs/tts.py:124,166`). Each
alignment message becomes one `TimedString` per word — `text[start:end]` with `start_time` and
`end_time` in seconds of the reply's own audio (`_to_timed_words`, `plugins/elevenlabs/tts.py:1332-1360`)
— pushed through `AudioEmitter.push_timed_transcript` (`tts/tts.py:986`), which rides them on the
frames' `USERDATA_TIMED_TRANSCRIPT` and so up into `_TTSGenerationData.timed_texts_fut`
(`voice/generation.py:437,514-516`).

**The session decides whether anything reads that channel.** `AgentActivity.use_tts_aligned_transcript`
resolves the agent's flag, else the session's, and is `False` unless one of them is `True`
(`voice/agent_activity.py:588-594`). Only when it is true does the activity swap the text source
for `_aligned_transcript_or_text(timed_texts, text)` before calling `transcription_node`
(`:3014-3019` for one segment, `:3542-3546` for the serial path). With it off, `transcription_node`
receives the model's plain text chunks — which is exactly what our bridge was logging.

**Asking costs nothing when the voice cannot answer.** The swap is guarded by
`tts.capabilities.aligned_transcript or not tts.capabilities.streaming`, and
`_aligned_transcript_or_text` falls back to the generated text, with a warning, when the aligned
stream turns out empty (`voice/agent_activity.py:120-140`). A voice that aligns nothing still
speaks, and still leaves a transcript.

So the timings are livekit's, measured by the voice, and the one thing that was ours to write is
`use_tts_aligned_transcript=True` on the spoken session (`session/voice/session.py`). What the bridge
then does with a `TimedString` is `docs/decisions/voice-bridge.md`, "The words, timed".
