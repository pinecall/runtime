"""One call's AgentSession, built from what the app declared: audio on a line, silence in chat."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from livekit.agents import NOT_GIVEN, NotGivenOr, inference
from livekit.agents.voice import AgentSession, STTContextOptions, text_transforms
from livekit.agents.voice.agent_session import DEFAULT_TTS_TEXT_TRANSFORMS
from livekit.agents.voice.transcription.text_transforms import TextTransforms
from livekit.agents.voice.turn import (
    InterruptionOptions,
    PreemptiveGenerationOptions,
    TurnHandlingOptions,
)

from pinecall.providers.registry import Ears
from pinecall.session.voice import hearing
from pinecall.session.voice.barge_in import MIN_WORDS
from pinecall.session.voice.kit import Kit
from pinecall.types import AgentConfig, ProviderKeys
from pinecall.types.channel import Channel

# The doors that carry audio. WhatsApp is written, and a session for it hears nothing: no STT, no
# TTS, no VAD, and every turn handed to the session by the channel that received it.
CHANNELS_THAT_LISTEN: frozenset[str] = frozenset({"phone", "web"})

# v1 is the hosted end-of-turn model and v1-mini the local one. Left unset the library picks
# between them by reading the environment (inference/eot/detector.py:55-59), and a self-hosted box
# must never be one environment variable away from sending a caller's transcript to a cloud.
LOCAL_TURN_VERSION: inference.TurnDetectorVersions = "v1-mini"

# livekit 1.8 runs the model before the turn is confirmed (voice/turn.py:223) and we keep it: on a
# line the caller hears the half second, and a discarded attempt is a Haiku prompt. Written turns
# arrive whole, so there is nothing to race and nothing to discard — see docs/decisions/worker.md.
SPOKEN_PREEMPTION: PreemptiveGenerationOptions = {"enabled": True, "preemptive_tts": False}
WRITTEN_PREEMPTION: PreemptiveGenerationOptions = {"enabled": False}

# A written turn is complete the moment it arrives, so the channel says when the caller is done.
WRITTEN_TURNS: TurnHandlingOptions = {
    "turn_detection": "manual",
    "preemptive_generation": WRITTEN_PREEMPTION,
}


# The word timings the console draws its karaoke with: left unset, livekit forwards the model's
# own text and the TimedStrings the voice measured never reach transcription_node
# (agent_activity.py:588-594, 3014-3019). A voice that aligns nothing is not harmed by asking —
# livekit falls back to the generated text (agent_activity.py:120-140). See livekit-words.md #19.
ALIGNED_TRANSCRIPT = True


# vad= is left to the session on a spoken call: undeclared, it builds livekit's own native
# inference.VAD at min_silence 0.25 (agent_session.py:606-607, inference/vad.py:64), which is the
# number we would have asked for. A written call passes None so that none is built at all.
def a_session(
    config: AgentConfig, kit: Kit, channel: Channel, keys: ProviderKeys
) -> AgentSession[None]:
    """The session livekit runs for this call: the vendors the agent asked for, and its turns."""
    built = kit(config, keys)
    if channel not in CHANNELS_THAT_LISTEN:
        written: AgentSession[None] = AgentSession(
            llm=built.llm, vad=None, turn_handling=WRITTEN_TURNS
        )
        return written
    spoken: AgentSession[None] = AgentSession(
        llm=built.llm,
        stt=built.stt,
        tts=built.tts,
        turn_handling=spoken_turns(config),
        use_tts_aligned_transcript=ALIGNED_TRANSCRIPT,
        tts_text_transforms=how_it_says_things(config),
        stt_context_options=what_it_listens_for(config, built.stt),
    )
    return spoken


# livekit already filters markdown and emoji out of every reply it speaks (agent_session.py:351),
# so the two names are here only because a tenant's own map is appended AFTER them: the parameter
# REPLACES the default list, and a map passed alone would take an agent's asterisks out loud with
# it. The order is the order a reader expects — clean the text, then say the names properly.
def how_it_says_things(config: AgentConfig) -> NotGivenOr[Sequence[TextTransforms]]:
    """What a reply passes through on its way to the voice: livekit's filters, then the tenant's."""
    if not config.says:
        return NOT_GIVEN
    return [*DEFAULT_TTS_TEXT_TRANSFORMS, text_transforms.replace(dict(config.says))]


# Only for ears that advertise a keyterms door. Soniox has none (its own is `context`, filled in
# providers/stt/soniox.py from the same declaration), and handing keyterms to ears that take none
# logs a warning on every call and changes nothing (stt/stt.py:293-298). What the state adds to
# this list as the call goes on is the bridge's, through the same door.
def what_it_listens_for(config: AgentConfig, ears: Ears) -> NotGivenOr[STTContextOptions]:
    """The words the agent declared it hears, on the vendor door livekit itself can reach."""
    if not config.hears or not hearing.takes_keyterms(ears):
        return NOT_GIVEN
    return {"keyterms": hearing.words(config)}


# Left to "auto", livekit picks the ADAPTIVE interruption detector whenever the process runs in
# dev mode or hosted (agent_activity.py:4842-4849) — and that detector is a WebSocket to
# agent-gateway.livekit.cloud carrying the caller's audio, which a self-hosted box must never open
# and which, with no cloud key, fails 401 every two seconds for the length of the call (found on
# the first job this runtime ever dispatched). "vad" is livekit's own local strategy, the
# same on `dev`, `start` and the console, and the one this box can honour.
INTERRUPTION_MODE: Literal["vad"] = "vad"


# How long a cut-off agent waits in silence before deciding nobody actually interrupted it and
# picking its sentence back up. Resuming is livekit's own default (turn.py:195) and is not written
# again here; the number is, because the library waits 2.0 s and its own example waits 1.0
# (basic_agent.py:93) — on a line where a cough stops the agent, two seconds of nothing is the
# caller wondering whether the call dropped. See docs/decisions/worker.md.
FALSE_INTERRUPTION_TIMEOUT_S = 1.0


# Endpointing is absent on purpose: `Turn.endpointing_ms` is already the ASR's own endpointing,
# which providers/ hands to the STT, and setting livekit's delay from the same number would make
# one knob wait twice. Everything else undeclared is livekit's default, which with a streaming
# detector is its tighter one (turn.py:142) — a number copied out of the library here would be a
# second place to change it. The one default that is ours is min_words: livekit's is 0.
def spoken_turns(config: AgentConfig) -> TurnHandlingOptions:
    """How a spoken call takes its turns: the detector, preemption, and what it takes to cut in."""
    min_words = MIN_WORDS
    if config.turn is not None and config.turn.min_interruption_words is not None:
        min_words = config.turn.min_interruption_words
    interruption: InterruptionOptions = {
        "min_words": min_words,
        "mode": INTERRUPTION_MODE,
        "false_interruption_timeout": FALSE_INTERRUPTION_TIMEOUT_S,
    }
    return {
        "turn_detection": inference.TurnDetector(version=LOCAL_TURN_VERSION),
        "preemptive_generation": SPOKEN_PREEMPTION,
        "interruption": interruption,
    }
