"""Every block livekit measures, taken off the components it measured them on, under its names."""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from livekit.agents import inference
from livekit.agents import metrics as measured
from livekit.agents.llm.chat_context import MetricsReport
from livekit.agents.metrics.base import AvatarMetrics, Metadata
from livekit.agents.metrics.usage import AgentSessionUsage
from livekit.agents.voice import AgentSession

from pinecall.providers.usage import as_wire_rows
from pinecall.session.voice.writing import Writing
from pinecall_protocol import WireModel
from pinecall_protocol import metrics as wire

# One entry type per class the library declares. A block nobody maps here would reach the log as
# nothing at all, so the table is the contract and a test reads it against livekit's own union.
BLOCKS: dict[type[Any], tuple[str, type[WireModel]]] = {
    measured.LLMMetrics: ("metrics.llm", wire.LLMMetrics),
    measured.STTMetrics: ("metrics.stt", wire.STTMetrics),
    measured.TTSMetrics: ("metrics.tts", wire.TTSMetrics),
    measured.VADMetrics: ("metrics.vad", wire.VADMetrics),
    measured.EOUMetrics: ("metrics.eou", wire.EOUMetrics),
    measured.EOTInferenceMetrics: ("metrics.eot", wire.EOTInferenceMetrics),
    measured.InterruptionMetrics: ("metrics.interruption", wire.InterruptionMetrics),
    measured.RealtimeModelMetrics: ("metrics.realtime", wire.RealtimeModelMetrics),
    AvatarMetrics: ("metrics.avatar", wire.AvatarMetrics),
}

# What the library calls the event every component emits its own measurements on.
MEASURED = "metrics_collected"


# AgentSession.on("metrics_collected") is deprecated in 1.8 and warns on subscribe
# (agent_session.py:725). The blocks are the same objects on the components themselves, so this
# listens there: the llm, the ears, the voice, the VAD and the turn detector the session was
# built out of. See docs/decisions/voice-bridge.md for the one block that has no component.
class Meters:
    """What this call measured, block by block, written the moment the component reported it."""

    def __init__(self, writing: Writing) -> None:
        self._writing = writing
        self._watched: list[Any] = []
        self.usage: AgentSessionUsage | None = None

    def watch(self, live: AgentSession[None]) -> None:
        """Subscribe to every component of this session that measures anything at all."""
        session = cast(Any, live)  # the three model properties are generic over a plugin's events
        components: list[Any] = [
            session.llm,
            session.stt,
            session.tts,
            live.vad,
            live.turn_detection,
        ]
        for component in components:
            emitter = _an_emitter(component)
            if emitter is None:
                continue
            emitter.on(MEASURED, self.collected)
            self._watched.append(emitter)

    def stop(self) -> None:
        """Let go of every component: the call is over and its blocks are all written."""
        for emitter in self._watched:
            emitter.off(MEASURED, self.collected)
        self._watched.clear()

    # livekit's own listener stamps speech_id on the very same object (agent_activity.py:1971), and
    # an emitter's listeners are a set with no order (rtc/event_emitter.py:15): the write waits one
    # loop tick, so it reads the block after every listener has had it, whichever ran first.
    def collected(self, block: measured.AgentMetrics) -> None:
        """One block as a component reported it, queued for the log once everyone has seen it."""
        mapped = BLOCKS.get(type(block))
        if mapped is not None:
            asyncio.get_running_loop().call_soon(self._written, block, *mapped)

    # The whole model, every field, under livekit's own names: model_dump() and not a projection,
    # so the day the library adds a field it is in the log before anybody edits this file.
    def _written(self, block: measured.AgentMetrics, type_: str, model: type[WireModel]) -> None:
        """The block as the wire carries it, exactly as it came, stored unless it is a tick."""
        ephemeral = True if a_usage_tick(block) else None
        self._writing.later(type_, model.model_validate(block.model_dump()), ephemeral)

    def collected_usage(self, usage: AgentSessionUsage) -> None:
        """session_usage_updated: the rows so far, kept for the summary the call ends with."""
        self.usage = usage

    @property
    def rows(self) -> list[wire.ModelUsage]:
        """What the call consumed, as call.summary carries it: livekit's own rows, unchanged."""
        return as_wire_rows(self.usage.model_usage) if self.usage is not None else []


# ── the meter a streaming STT keeps running ─────────────────────────────────


# A streaming STT emits no per-turn block at all. Its STTMetrics is the RECOGNITION_USAGE meter
# (`agents/stt/stt.py:519-541`): `duration` is 0.0 by definition (`metrics/base.py:54-55`), there is
# no `speech_id` to join a turn by, and the cadence is the plugin's own — deepgram reports every 5 s
# of audio (`plugins/deepgram/stt.py:484`), soniox on every frame it receives
# (`plugins/soniox/stt.py:604`), which is ~120 ticks for one spoken sentence. The total is not lost
# by letting them go: livekit sums them into `STTModelUsage.audio_duration`, which `call.summary`
# carries whole. See docs/decisions/livekit-metrics.md #18 and voice-bridge.md.
def a_usage_tick(block: measured.AgentMetrics) -> bool:
    """Whether this block is a streaming STT's usage meter, measuring nothing else."""
    if not isinstance(block, measured.STTMetrics) or not block.streamed:
        return False
    return not block.acquire_time and not block.connection_reused


# ── the one block with no component behind it ───────────────────────────────────


# EOUMetrics is emitted straight onto the session (agent_activity.py:2699), so there is no
# component to hear it on. livekit's own deprecation notice names ChatMessage.metrics as the
# replacement, and the user turn's report carries exactly the three delays EOUMetrics carries —
# `end_of_turn_delay` is the field livekit itself reads to build `end_of_utterance_delay`
# (agent_activity.py:2690), and the metadata is the turn detector's, as livekit stamps it (:2683).
# So the block is built here, the way livekit builds it, from the library's own numbers.
def an_end_of_utterance(
    report: MetricsReport, speech_id: str | None, detector: Any
) -> measured.EOUMetrics | None:
    """The user turn's own delays as livekit's EOUMetrics, or None when it measured none."""
    if not any(key in report for key in _EOU_FIELDS):
        return None
    return measured.EOUMetrics(
        timestamp=time.time(),
        end_of_utterance_delay=report.get("end_of_turn_delay", 0.0),
        transcription_delay=report.get("transcription_delay", 0.0),
        on_user_turn_completed_delay=report.get("on_user_turn_completed_delay", 0.0),
        speech_id=speech_id,
        metadata=_who_called_the_turn(detector),
    )


_EOU_FIELDS = ("end_of_turn_delay", "transcription_delay", "on_user_turn_completed_delay")


def _who_called_the_turn(detector: Any) -> Metadata | None:
    """The turn detector as livekit names it in an EOU block: the model, or the mode's name."""
    if isinstance(detector, inference.TurnDetector):
        return Metadata(model_name=detector.model, model_provider=detector.provider)
    if isinstance(detector, str):
        return Metadata(model_name="unknown", model_provider=detector)
    return None


# livekit's EventEmitter is generic over an unbounded parameter, so a strict checker cannot read
# its .on/.off. The one cast in the bridge that says what they are lives here, at the one seam
# that listens to a component at all.
def _an_emitter(component: Any) -> Any:
    """The component as the thing that emits, or None when the session was built without one."""
    if component is None:
        return None
    return component if hasattr(component, "on") else None
