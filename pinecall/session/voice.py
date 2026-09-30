"""A session opened for a spoken call: the pipeline, the turn options, the ears' keyterms."""

from collections.abc import Sequence
from functools import cache

from livekit.agents import (
    NOT_GIVEN,
    NotGivenOr,
    inference,
)
from livekit.agents.voice import AgentSession, STTContextOptions
from livekit.agents.voice import text_transforms as transforms
from livekit.agents.voice.agent_session import DEFAULT_TTS_TEXT_TRANSFORMS
from livekit.agents.voice.turn import InterruptionOptions, TurnDetectionMode, TurnHandlingOptions
from smart_turn_livekit import SmartTurnDetector

from pinecall.domain.agent import AgentConfig
from pinecall.providers.build import (
    Ears,
    Running,
    TurnModel,
    ears_of,
    speaking_of,
    thinking_of,
)
from pinecall.providers.credentials import Pipeline
from pinecall.session import tools
from pinecall.session._hearing import keyterms, policy_for
from pinecall.session._livekit import switches_written
from pinecall.session.call import Call
from pinecall.session.session import ONE_ANSWER_PER_TOOL, Session
from pinecall.session.tools import VOICE_LOOKUP_MS

# The local end-of-turn model: left unset, livekit may pick the hosted one and send the
# caller's words to a cloud.
LOCAL_TURN_VERSION: inference.TurnDetectorVersions = "v1-mini"

# The checkpoint in smart-turn-livekit's registry: upstream's, benchmarked on 23 languages.
SMART_TURN: TurnModel = "smart-turn-v3"


def voice_session(call: Call, stages: Pipeline) -> Session:
    """A voice call: the three stages built for it, the turn taken as a phone line needs."""
    thinking, ears, voice = (
        thinking_of(stages.llm),
        ears_of(stages.stt, call.config.turn),
        speaking_of(stages.tts),
    )
    switches_written(call.writing, (thinking, ears, voice))
    live: AgentSession[None] = AgentSession(
        llm=thinking,
        stt=ears,
        tts=voice,
        turn_handling=_spoken_turns(call.config, stages.stt),
        # Word timings reach transcription_node only from an aligned voice.
        use_tts_aligned_transcript=True,
        tts_text_transforms=_text_transforms_of(call.config),
        stt_context_options=context_of(call.config, ears),
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return Session(
        live, (thinking, ears, voice), tools.Lookups(call, call.platform.lookup, VOICE_LOOKUP_MS)
    )


def context_of(config: AgentConfig, ears: Ears) -> NotGivenOr[STTContextOptions]:
    """The keyterms the ears are told at start, when they take any."""
    if not config.hears or not ears.capabilities.keyterms:
        return NOT_GIVEN
    return {"keyterms": keyterms(config, {})}


def end_of_turn(model: TurnModel) -> TurnDetectionMode:
    """The local model that reads the end of the caller's turn off the audio, no transcript."""
    if model == SMART_TURN:
        return smart_turn()
    return inference.TurnDetector(version=LOCAL_TURN_VERSION)


# One Smart Turn per process: its weights load once and its first inference runs when it is made,
# so only the first call that asks for it pays. Each session streams to it on its own.
@cache
def smart_turn() -> SmartTurnDetector:
    """Smart Turn v3, the row's alternative to livekit's own detector."""
    return SmartTurnDetector(SMART_TURN)


# The parameter replaces livekit's defaults, so they come first and the tenant's words after.
def _text_transforms_of(config: AgentConfig) -> NotGivenOr[Sequence[transforms.TextTransforms]]:
    """Livekit's own transforms, then the tenant's pronunciations."""
    if not config.says:
        return NOT_GIVEN
    return [*DEFAULT_TTS_TEXT_TRANSFORMS, transforms.replace(dict(config.says))]


# Interruptions are judged by the local VAD: livekit's adaptive detector streams the caller's
# audio to its cloud. A false interruption is not resumed: livekit replays the whole sentence.
# No endpointing here: the agent's own already reaches the ears, and both would wait twice.
def _spoken_turns(config: AgentConfig, ears: Running) -> TurnHandlingOptions:
    r"""How the caller takes the floor, from the agent\'s language and its own turn knobs."""
    policy = policy_for(config.language, config.turn)
    interruption: InterruptionOptions = {
        "min_words": policy.min_words,
        "mode": "vad",
        "false_interruption_timeout": policy.false_interruption_s,
        "resume_false_interruption": False,
    }
    if policy.min_speech_s is not None:
        interruption["min_duration"] = policy.min_speech_s
    return {
        # Ears that end the turn themselves decide it; the local detector stacked on them waits
        # its whole delay after a pause in the middle of a sentence.
        "turn_detection": "stt" if ears.ends_the_turn else end_of_turn(ears.turn_model),
        # A reply started inside a tool's window, on a context without its result, answers its
        # own question.
        "preemptive_generation": {"enabled": False},
        "interruption": interruption,
    }
