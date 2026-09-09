"""The session a call runs: every plugin the agent asked for, and the turns of its own channel."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest
from livekit.agents import inference
from livekit.agents.voice import AgentSession

from pinecall.providers.registry import NO_ORG_KEYS
from pinecall.session.voice.barge_in import MIN_WORDS
from pinecall.session.voice.session import a_session
from pinecall.types import AgentConfig, Turn
from pinecall.types.channel import Channel
from tests.session.fake_llm import FakeLLM
from tests.session.voice.silence import FakeKit

pytestmark = pytest.mark.unit

CLARA = AgentConfig(slug="clinica-norte", channels=frozenset({"phone", "web"}))


async def test_a_spoken_call_is_built_with_the_ears_and_the_voice_the_agent_asked_for() -> None:
    kit = _a_kit()
    live = a_call_on(CLARA, kit, "phone")
    assert kit.built == ["clinica-norte"]
    assert live.stt is kit.pipe.stt  # pyright: ignore[reportUnknownMemberType]
    assert live.tts is kit.pipe.tts  # pyright: ignore[reportUnknownMemberType]


async def test_the_turn_detector_is_the_local_model_and_never_the_hosted_one() -> None:
    """Left unset the library reads the environment for this (inference/eot/detector.py:55-59);
    a self-hosted box must not depend on which variables happen to be set."""
    detector = a_call_on(CLARA, _a_kit(), "phone").turn_detection
    assert isinstance(detector, inference.TurnDetector)
    assert detector.model == "turn-detector-v1-mini"


async def test_the_vad_is_livekits_own_native_one_and_it_waits_a_quarter_of_a_second() -> None:
    """Nothing of ours builds a VAD: undeclared, the session builds the native one at 0.25
    (agent_session.py:606-607, inference/vad.py:64), the number we would have asked for."""
    heard = a_call_on(CLARA, _a_kit(), "phone").vad
    assert isinstance(heard, inference.VAD)
    assert heard._opts.min_silence_duration == 0.25  # pyright: ignore[reportPrivateUsage]


async def test_a_written_call_builds_no_vad_at_all() -> None:
    assert a_call_on(CLARA, _a_kit(), "whatsapp").vad is None


async def test_a_written_call_hears_nothing_and_takes_its_turns_by_hand() -> None:
    """WhatsApp is text: no STT, no TTS, no VAD, and the channel says when a turn ended."""
    live = a_call_on(CLARA, _a_kit(), "whatsapp")
    assert live.turn_detection == "manual"


async def test_a_spoken_call_asks_the_voice_to_align_the_transcript_it_speaks() -> None:
    """Off, the TimedStrings the voice measured never reach transcription_node
    (agent_activity.py:588-594, 3014-3019) and no word timing can ever be logged."""
    spoken = a_call_on(CLARA, _a_kit(), "phone")
    assert spoken.options.use_tts_aligned_transcript is True


async def test_preemptive_generation_stays_on_where_the_caller_can_hear_it() -> None:
    """1.8 turned it on (voice/turn.py:223) and a spoken call keeps it; the doc argues why."""
    spoken = a_call_on(CLARA, _a_kit(), "phone")
    preemptive = _turns(spoken)["preemptive_generation"]
    assert (preemptive["enabled"], preemptive["preemptive_tts"]) == (True, False)


async def test_a_written_call_never_runs_the_model_before_the_turn_is_in() -> None:
    written = a_call_on(CLARA, _a_kit(), "whatsapp")
    assert _turns(written)["preemptive_generation"]["enabled"] is False


async def test_what_it_takes_to_cut_the_agent_off_is_the_agents_own_declaration() -> None:
    config = AgentConfig(
        slug="clinica-norte",
        channels=frozenset({"phone"}),
        turn=Turn(min_interruption_words=3, endpointing_ms=400),
    )
    handling = _turns(a_call_on(config, _a_kit(), "phone"))
    assert handling["interruption"]["min_words"] == 3
    # endpointing_ms is the ASR's, and providers/ already handed it to the STT: waiting for it
    # twice is what setting livekit's own delay from the same number would do.
    assert handling["endpointing"]["min_delay"] == 0.3


async def test_an_agent_that_declared_no_turn_settings_inherits_livekits_own_but_two_words() -> (
    None
):
    """Undeclared is livekit's own — a streaming detector brings its tighter endpointing, 0.3 —
    except min_words, where livekit's 0 lets a cough stop the agent and ours is two."""
    handling = _turns(a_call_on(CLARA, _a_kit(), "phone"))
    assert handling["endpointing"]["min_delay"] == 0.3
    assert handling["interruption"]["min_words"] == MIN_WORDS


async def test_interruptions_are_judged_by_the_local_vad_and_never_by_livekits_cloud_detector() -> (
    None
):
    """Auto mode picks the adaptive detector in dev mode (agent_activity.py:4842), and that is a
    WebSocket to agent-gateway.livekit.cloud carrying the caller's audio."""
    handling = _turns(a_call_on(CLARA, _a_kit(), "phone"))
    assert handling["interruption"]["mode"] == "vad"


async def test_a_reply_is_cleaned_of_markdown_and_emoji_before_the_voice_ever_sees_it() -> None:
    """The prompt asks the model for no asterisks; livekit guarantees it (agent_session.py:351)."""
    spoken = a_call_on(CLARA, _a_kit(), "phone")
    assert list(spoken.options.tts_text_transforms or ()) == ["filter_markdown", "filter_emoji"]


async def test_the_tenants_own_pronunciations_are_said_after_the_two_filters() -> None:
    """`says` replaces a word on its way to the voice and never in the log: the reader of a call
    sees what the model wrote, and the caller hears the name said properly."""
    config = replace(CLARA, says={"Vidal": "bidál"})
    applied = list(a_call_on(config, _a_kit(), "phone").options.tts_text_transforms or ())
    assert applied[:2] == ["filter_markdown", "filter_emoji"]
    assert callable(applied[2])


async def test_the_words_the_agent_declared_it_hears_reach_the_ears_that_take_them() -> None:
    config = replace(CLARA, hears=("Clínica Norte", "doctora Vidal"))
    spoken = a_call_on(config, _a_kit(keyterms=True), "phone")
    assert _stt_context(spoken)["keyterms"] == ["Clínica Norte", "doctora Vidal"]


async def test_ears_with_no_keyterms_door_are_never_handed_any() -> None:
    """Soniox has none — it takes `context` instead, filled in providers/stt/soniox.py — and
    handing keyterms to ears that take none logs a warning per call and changes nothing."""
    config = replace(CLARA, hears=("Clínica Norte",))
    spoken = a_call_on(config, _a_kit(), "phone")
    assert _stt_context(spoken)["keyterms"] == []


async def test_no_llm_is_ever_spent_guessing_keyterms_the_agent_did_not_declare() -> None:
    """livekit's own detector is a model call per turn_interval (keyterm_detection.py:88-96);
    the agent already knows its own words, and the state tells us the rest for nothing."""
    spoken = a_call_on(CLARA, _a_kit(keyterms=True), "phone")
    assert _stt_context(spoken)["keyterm_detection"]["enabled"] is False


async def test_an_agent_cut_off_by_a_cough_picks_its_sentence_back_up() -> None:
    """Resuming is livekit's default (turn.py:195) and stays it; the wait is ours, because two
    seconds of silence on a phone line is a caller wondering whether the call dropped."""
    interruption = _turns(a_call_on(CLARA, _a_kit(), "phone"))["interruption"]
    assert interruption["resume_false_interruption"] is True
    assert interruption["false_interruption_timeout"] == 1.0


async def test_the_orgs_own_keys_are_handed_to_the_kit_and_go_nowhere_else() -> None:
    """The one thing this file says about provider keys: the session passes them straight on."""
    kit = _a_kit()
    a_session(CLARA, kit, "phone", {"elevenlabs": "the-orgs-own"})
    assert kit.keys == [{"elevenlabs": "the-orgs-own"}]


# The session resolves every absent key of livekit's own TypedDicts, so a test reads the result as
# the plain mapping it is rather than asking a total-false TypedDict for a key it need not have.
def _turns(live: AgentSession[None]) -> dict[str, Any]:
    """Everything the session decided about turns, keys and all."""
    return cast("dict[str, Any]", live.options.turn_handling)


def _stt_context(live: AgentSession[None]) -> dict[str, Any]:
    """Everything the session decided about what the ears expect, keys and all."""
    return cast("dict[str, Any]", live.options.stt_context_options)


# Whose provider keys a session runs on is providers/registry.py's question and not this file's:
# every test here is about turns, ears and voices, so they all run as an org that brought none.
def a_call_on(config: AgentConfig, kit: FakeKit, channel: Channel) -> AgentSession[None]:
    """One session of this suite, for an org running on the box's own keys."""
    return a_session(config, kit, channel, NO_ORG_KEYS)


def _a_kit(*, keyterms: bool = False) -> FakeKit:
    """A kit with no vendor behind it, so the session is built and nothing is ever dialled."""
    return FakeKit(FakeLLM(), keyterms=keyterms)
