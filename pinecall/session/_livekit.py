"""What livekit hands the session, read as ours: usage, transcripts, blocks, names, switches."""

import contextlib
import time
from collections.abc import Callable, Iterable, Mapping

from livekit.agents import (
    NOT_GIVEN,
    APIStatusError,
    NotGivenOr,
    StopResponse,
    llm,
    metrics,
    stt,
    tts,
    utils,
)
from livekit.agents.metrics.base import AvatarMetrics
from livekit.agents.types import TimedString
from livekit.agents.voice import AgentSession
from pydantic import BaseModel

from pinecall.providers.build import Modality
from pinecall.session.call import Writing
from pinecall.wire import events as wire
from pinecall.wire import metrics as measured
from pinecall.wire.frames import WireModel

type Adapter = llm.FallbackAdapter | stt.FallbackAdapter | tts.FallbackAdapter


# Errors of the request itself; 408, 429 and 5xx are transient and livekit retries them.
FOREVER = frozenset({400, 401, 402, 403, 404, 422})


# The WebSocket close for a policy violation: a vendor that refuses a voice or a key sends it,
# and livekit's 4xx check misses it.
POLICY_VIOLATION = 1008


# Each block is read by the wire's fields, under livekit's own names.
BLOCKS: tuple[tuple[type[metrics.AgentMetrics], str, type[WireModel]], ...] = (
    (metrics.LLMMetrics, "metrics.llm", measured.LLMMetrics),
    (metrics.STTMetrics, "metrics.stt", measured.STTMetrics),
    (metrics.TTSMetrics, "metrics.tts", measured.TTSMetrics),
    (metrics.VADMetrics, "metrics.vad", measured.VADMetrics),
    (metrics.EOUMetrics, "metrics.eou", measured.EOUMetrics),
    (metrics.InterruptionMetrics, "metrics.interruption", measured.InterruptionMetrics),
    (metrics.RealtimeModelMetrics, "metrics.realtime", measured.RealtimeModelMetrics),
    (metrics.EOTInferenceMetrics, "metrics.eot", measured.EOTInferenceMetrics),
    (AvatarMetrics, "metrics.avatar", measured.AvatarMetrics),
)


def given_or_unset[T](value: T | None) -> NotGivenOr[T]:
    """The value, or livekit's NOT_GIVEN when it is None."""
    return NOT_GIVEN if value is None else value


def transcript_of(live: AgentSession[None], delta: str | TimedString) -> wire.AgentTranscript:
    """What the agent said, as the wire's transcript entry, with the word timings livekit gave."""
    speech = live.current_speech.id if live.current_speech else ""
    timed: dict[str, float] = {}
    if isinstance(delta, TimedString):
        if utils.is_given(delta.start_time):
            timed["start"] = delta.start_time
        if utils.is_given(delta.end_time):
            timed["end"] = delta.end_time
    return wire.AgentTranscript(speech_id=speech, text=str(delta), final=False, **timed)


# interrupt() raises when nothing is playing or the session has stopped.
async def silence(live: AgentSession[None]) -> None:
    """Stop the agent's speech now, and wait for livekit to say it stopped."""
    with contextlib.suppress(RuntimeError):
        await live.interrupt(force=True)
    live.output.set_audio_enabled(False)
    live.input.set_audio_enabled(False)


# The ears first, then the voice, so the agent never speaks before it can hear.
def hearing_again(live: AgentSession[None]) -> None:
    """Let the caller's audio reach the ears again after a pause."""
    live.input.set_audio_enabled(True)
    live.output.set_audio_enabled(True)


async def nothing_after(_: llm.Toolset.ToolCompletedEvent) -> None:
    """Speak nothing after a tool finished: the app speaks when it wants to."""
    raise StopResponse


def forever(error: APIStatusError) -> bool:
    """Whether a vendor's HTTP error will fail the same way every time it is retried."""
    return error.status_code == POLICY_VIOLATION or error.status_code in FOREVER


def usage_of(used: metrics.ModelUsage) -> measured.ModelUsage:
    """The usage of one model livekit measured, as the wire writes it."""
    match used:
        case metrics.LLMModelUsage():
            return _read_as(measured.LLMModelUsage, used)
        case metrics.TTSModelUsage():
            return _read_as(measured.TTSModelUsage, used)
        case metrics.STTModelUsage():
            return _read_as(measured.STTModelUsage, used)
        case metrics.InterruptionModelUsage():
            return _read_as(measured.InterruptionModelUsage, used)
        case _:
            return _read_as(measured.EOTModelUsage, used)


def block_of(block: metrics.AgentMetrics) -> tuple[str, WireModel] | None:
    """The metrics livekit measured for one step, as the entry type and model the log keeps."""
    for livekits, kind, ours in BLOCKS:
        if isinstance(block, livekits):
            return kind, _read_as(ours, block)
    return None


# livekit emits the end-of-utterance block on the session only, so it is built again from the
# user turn's report, as livekit builds it.
def end_of_utterance(report: Mapping[str, object], speech: str) -> measured.EOUMetrics | None:
    """The end-of-turn metrics livekit reports, as the wire's; None when a field is missing."""
    delays = ("end_of_turn_delay", "transcription_delay", "on_user_turn_completed_delay")
    if not any(key in report for key in delays):
        return None
    # Said, or written() drops the default and the SDKs refuse the entry.
    return measured.EOUMetrics.model_validate(
        {
            "type": "eou_metrics",
            "timestamp": time.time(),
            "end_of_utterance_delay": report.get("end_of_turn_delay", 0.0),
            "transcription_delay": report.get("transcription_delay", 0.0),
            "on_user_turn_completed_delay": report.get("on_user_turn_completed_delay", 0.0),
            "speech_id": speech,
        }
    )


# livekit's adapter says when a vendor goes down or comes back (`<stage>_availability_changed`,
# the vendor under the stage's own name), and names the next to serve. Its emitter is untyped:
# it is read as an object.
def switches_written(writing: Writing, built: Iterable[object]) -> None:
    """Write vendor.switched each time a stage's fallback adapter loses or regains a vendor."""
    for component in built:
        match component:
            case llm.FallbackAdapter():
                stage: Modality = "llm"
            case stt.FallbackAdapter():
                stage = "stt"
            case tts.FallbackAdapter():
                stage = "tts"
            case _:
                continue
        listen: object = getattr(component, "on", None)
        if callable(listen):
            listen(f"{stage}_availability_changed", _switch(writing, stage, component))


def switch_of(stage: Modality, event: object, adapter: Adapter) -> wire.VendorSwitched | None:
    """The entry for one vendor of a stage lost or back, and the one serving from now."""
    changed: object = getattr(event, stage, None)
    available: object = getattr(event, "available", None)
    if not isinstance(changed, (llm.LLM, stt.STT, tts.TTS)) or not isinstance(available, bool):
        return None
    return wire.VendorSwitched(
        stage=stage,
        vendor=changed.provider,
        model=changed.model,
        available=available,
        serving=adapter.provider,
        serving_model=adapter.model,
    )


# livekit's rows carry the wire's names, and livekit adds fields the wire does not know
# (`input_audio_tokens` on the ears' usage in 1.8.3). The wire refuses an unknown key, and a
# listener's exception is swallowed by livekit's emitter, so the call would lose its usage in
# silence: each row is read by the fields its wire model declares.
def _read_as[T: WireModel](model: type[T], livekits: BaseModel) -> T:
    """The wire model read from livekit's, field by field, so a field livekit grew is left out."""
    dumped = livekits.model_dump()
    return model.model_validate(
        {name: dumped[name] for name in model.model_fields if name in dumped}
    )


def _switch(writing: Writing, stage: Modality, adapter: Adapter) -> Callable[[object], None]:
    def written(event: object) -> None:
        switched = switch_of(stage, event, adapter)
        if switched is not None:
            writing.write("vendor.switched", switched)

    return written
