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
    TurnDetectionMode,
    TurnHandlingOptions,
)

from pinecall.providers.pipeline import DEFAULT_STT, vendor_running
from pinecall.providers.registry import Ears
from pinecall.session.voice import hearing
from pinecall.session.voice.barge_in import MIN_WORDS
from pinecall.session.voice.kit import Kit
from pinecall.session.written import ONE_ANSWER_PER_TOOL, a_written_session
from pinecall.types import AgentConfig, Brought
from pinecall.types.channel import Channel

# The doors that carry audio. WhatsApp is written, and a session for it hears nothing: no STT, no
# TTS, no VAD, and every turn handed to the session by the channel that received it.
CHANNELS_THAT_LISTEN: frozenset[str] = frozenset({"phone", "web"})

# v1 is the hosted end-of-turn model and v1-mini the local one. Left unset the library picks
# between them by reading the environment (inference/eot/detector.py:55-59), and a self-hosted box
# must never be one environment variable away from sending a caller's transcript to a cloud.
LOCAL_TURN_VERSION: inference.TurnDetectorVersions = "v1-mini"

# livekit 1.8 runs the model before the turn is confirmed (voice/turn.py:223). We kept it for the
# half second a caller hears, and it was buying that half second with the agent answering its own
# questions.
#
# What it does when a caller's end-of-turn lands INSIDE a tool's execution window: the preemptive
# path starts a whole new reply before the tool has answered, on a context that is missing it.
# call_5cc362bf11a3ed9419763383, seq 250 — the agent asked "Is this a house?" and then said, in its
# own voice and under the same speech id, "Yes, a house.". The caller answered it for real
# twenty-seven entries later. The metrics name the mechanism outright: every round of that call
# was built on 4,300 to 4,900 prompt tokens, and the one that invented the caller's line was built
# on 2,923 — fourteen hundred tokens short of the conversation it was supposed to be continuing.
# Beside it, seq 271: a round with `cancelled: true` and no tokens at all, a preemptive generation
# thrown away. livekit has both halves on file (agents#1365 for the tool window, agents#4219 for
# the doubled requests).
#
# The cost of turning it off is the half second. The cost of leaving it on is an agent that
# sometimes plays both parts, which is not a latency problem and cannot be prompted away.
SPOKEN_PREEMPTION: PreemptiveGenerationOptions = {"enabled": False}


# The word timings the console draws its karaoke with: left unset, livekit forwards the model's
# own text and the TimedStrings the voice measured never reach transcription_node
# (agent_activity.py:588-594, 3014-3019). A voice that aligns nothing is not harmed by asking —
# livekit falls back to the generated text (agent_activity.py:120-140). See livekit-words.md #19.
ALIGNED_TRANSCRIPT = True


def a_session(
    config: AgentConfig, kit: Kit, channel: Channel, brought: Brought, *, spoken: bool = True
) -> AgentSession[None]:
    """The session livekit runs for this call: the vendors the agent asked for, and its turns."""
    built = kit(config, brought)
    # A written call is one nobody speaks on: a channel that never listens, or a web visit whose
    # token said `chat`. The spoken session ran for those too, and the page read the agent's words
    # at the pace a voice nobody heard was saying them, two seconds behind and billed as speech
    # (2026-09-16, the first chat from a tenant's page). No ears and no voice: the model's text
    # reaches the room as it is written.
    if channel not in CHANNELS_THAT_LISTEN or not spoken:
        return a_written_session(built.llm)
    voiced: AgentSession[None] = AgentSession(
        llm=built.llm,
        stt=built.stt,
        tts=built.tts,
        turn_handling=spoken_turns(config),
        use_tts_aligned_transcript=ALIGNED_TRANSCRIPT,
        tts_text_transforms=how_it_says_things(config),
        stt_context_options=what_it_listens_for(config, built.stt),
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return voiced


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

# And what livekit does when that second is up: it plays the cut sentence AGAIN, from the start.
# Off, and this is a decision and not a workaround.
#
# It was read as one once — the read-back was cutting the reply, and fixing that (reading_back.py)
# looked like it would be enough. It was not. With the read-back waiting its turn and landing
# last, a booking's reply was still heard twice: same speech_id, same metrics to the millisecond,
# seq 688 and seq 768 of one call. A read-back is one thing that cuts a sentence; a caller
# breathing into a telephone is another, and there is no end to that list.
#
# The behaviour is wrong for what we build regardless of who does the cutting. Resuming means
# replaying the WHOLE utterance — livekit offers no "carry on from where it stopped" — so a
# thirty-word goodbye that got clipped is a thirty-word goodbye said twice. On a line, a caller
# hearing the same sentence twice is the agent sounding broken. The cost of turning it off is
# that a cough leaves the agent silent instead of starting over, and silence is a thing a caller
# talks into.
DO_NOT_SAY_IT_TWICE = False


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
        "resume_false_interruption": DO_NOT_SAY_IT_TWICE,
    }
    return {
        "turn_detection": the_turn_detector(config),
        "preemptive_generation": SPOKEN_PREEMPTION,
        "interruption": interruption,
    }


# A recogniser that decides the end of the turn itself (Deepgram Flux) is asked, not second-guessed:
# livekit's "stt" mode commits the turn on the recogniser's own end-of-turn event, where the local
# detector on top of it waited its whole max delay on a caller who paused mid-sentence — 2.5 s of
# silence after "My toilet won't flush." on 2026-09-16, against 0.3 s on every other turn.
STT_DECIDES: frozenset[str] = frozenset({"deepgram"})


# Read off the vendor that RUNS, not the one declared: an agent that names no ears runs the
# default, and the default is Flux — asking the declaration alone put the local detector on top of
# it and waited the 2.5 s this mode exists to avoid.
def the_turn_detector(config: AgentConfig) -> TurnDetectionMode:
    """The recogniser's own end of turn when it has one, livekit's local model otherwise."""
    if vendor_running(config.stt, DEFAULT_STT) in STT_DECIDES:
        return "stt"
    return inference.TurnDetector(version=LOCAL_TURN_VERSION)
