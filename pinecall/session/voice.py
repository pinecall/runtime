"""A session opened for a spoken call: the pipeline, the turn options, the ears' keyterms."""

from collections.abc import Sequence
from typing import Never

from livekit.agents import (
    NOT_GIVEN,
    NotGivenOr,
    inference,
    stt,
)
from livekit.agents.voice import AgentSession, STTContextOptions
from livekit.agents.voice import text_transforms as transforms
from livekit.agents.voice.agent_session import DEFAULT_TTS_TEXT_TRANSFORMS
from livekit.agents.voice.turn import InterruptionOptions, TurnHandlingOptions

from pinecall.domain.agent import AgentConfig
from pinecall.providers.build import llm_of, stt_of, tts_of
from pinecall.providers.credentials import Pipeline
from pinecall.session import tools
from pinecall.session._hearing import keyterms, policy_for
from pinecall.session.call import Call
from pinecall.session.session import LOCAL_TURN_VERSION, ONE_ANSWER_PER_TOOL, Session
from pinecall.session.tools import VOICE_LOOKUP_MS


def voice_session(call: Call, stages: Pipeline) -> Session:
    """A voice call: the three stages built for it, the turn taken as a phone line needs."""
    thinking, ears, voice = (
        llm_of(stages.llm),
        stt_of(stages.stt, call.config.turn),
        tts_of(stages.tts),
    )
    live: AgentSession[None] = AgentSession(
        llm=thinking,
        stt=ears,
        tts=voice,
        turn_handling=_spoken_turns(call.config, ends_the_turn=stages.stt.ends_the_turn),
        # Word timings reach transcription_node only from an aligned voice.
        use_tts_aligned_transcript=True,
        tts_text_transforms=_text_transforms_of(call.config),
        stt_context_options=context_of(call.config, ears),
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return Session(
        live, (thinking, ears, voice), tools.Lookups(call, call.platform.lookup, VOICE_LOOKUP_MS)
    )


def context_of(config: AgentConfig, ears: stt.STT[Never]) -> NotGivenOr[STTContextOptions]:
    """The keyterms the ears are told at start, when they take any."""
    if not config.hears or not ears.capabilities.keyterms:
        return NOT_GIVEN
    return {"keyterms": keyterms(config, {})}


# The parameter replaces livekit's defaults, so they come first and the tenant's words after.
def _text_transforms_of(config: AgentConfig) -> NotGivenOr[Sequence[transforms.TextTransforms]]:
    """Livekit's own transforms, then the tenant's pronunciations."""
    if not config.says:
        return NOT_GIVEN
    return [*DEFAULT_TTS_TEXT_TRANSFORMS, transforms.replace(dict(config.says))]


# Interruptions are judged by the local VAD: livekit's adaptive detector streams the caller's
# audio to its cloud. A false interruption is not resumed: livekit replays the whole sentence.
# No endpointing here: the agent's own already reaches the ears, and both would wait twice.
def _spoken_turns(config: AgentConfig, *, ends_the_turn: bool) -> TurnHandlingOptions:
    r"""How the caller takes the floor, from the agent\'s language and its own words."""
    declared = config.turn.min_interruption_words if config.turn else None
    policy = policy_for(config.language, min_words=declared)
    interruption: InterruptionOptions = {
        "min_words": policy.min_words,
        "mode": "vad",
        "false_interruption_timeout": policy.false_interruption_s,
        "resume_false_interruption": False,
    }
    return {
        # Ears that end the turn themselves decide it; the local detector stacked on them waits
        # its whole delay after a pause in the middle of a sentence.
        "turn_detection": "stt"
        if ends_the_turn
        else inference.TurnDetector(version=LOCAL_TURN_VERSION),
        # A reply started inside a tool's window, on a context without its result, answers its
        # own question.
        "preemptive_generation": {"enabled": False},
        "interruption": interruption,
    }
