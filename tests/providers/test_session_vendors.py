"""One agent's declaration in, the three objects a session runs on out, with a line per default."""

import dataclasses
import logging

import pytest
from livekit.plugins import cartesia, deepgram, elevenlabs, openai

from pinecall._settings import Settings
from pinecall.providers.session_vendors import Pipeline, pipeline_for
from pinecall.types import NOTHING_BROUGHT, AgentConfig, Model, Turn, Voice

pytestmark = pytest.mark.unit

A_KEY = "nobody-will-ever-deploy-this"


def settings() -> Settings:
    """A process that read a key for every vendor this suite builds."""
    return Settings(
        world="production",
        anthropic_api_key=A_KEY,
        openai_api_key=A_KEY,
        soniox_api_key=A_KEY,
        deepgram_api_key=A_KEY,
        eleven_api_key=A_KEY,
        cartesia_api_key=A_KEY,
    )


def test_a_pipeline_is_the_three_vendors_livekit_does_not_bring_itself() -> None:
    """The VAD and the turn detector are the session's own (agent_session.py:541-542,606-607)."""
    assert [field.name for field in dataclasses.fields(Pipeline)] == ["llm", "stt", "tts"]


def test_an_agent_that_declares_nothing_still_gets_a_whole_pipeline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blank declaration once silenced a whole line of calls: it warns now, per modality."""
    with caplog.at_level(logging.WARNING, logger="pinecall.providers.session_vendors"):
        built = pipeline_for(AgentConfig(slug="clinica-norte"), settings(), NOTHING_BROUGHT)
    assert isinstance(built.stt, deepgram.STTv2)
    assert isinstance(built.tts, cartesia.TTS)
    assert built.llm.label == "livekit.plugins.anthropic.llm.LLM"
    assert [record.message.split(" declared no ")[1] for record in caplog.records] == [
        "llm vendor; running anthropic",
        "stt vendor; running deepgram",
        "tts vendor; running cartesia",
    ]


def test_every_vendor_an_agent_names_is_the_one_it_gets() -> None:
    declared = AgentConfig(
        slug="tienda-sur",
        language="es",
        llm=Model(provider="openai", model="gpt-5-mini"),
        stt=Model(provider="deepgram", model="flux-general-multi"),
        voice=Voice(provider="elevenlabs", model="eleven_v3_conversational", voice_id="a-voice"),
    )
    built = pipeline_for(declared, settings(), NOTHING_BROUGHT)
    assert isinstance(built.llm, openai.LLM)
    assert isinstance(built.stt, deepgram.STTv2)
    assert isinstance(built.tts, elevenlabs.TTS)
    options = built.tts._opts  # pyright: ignore[reportPrivateUsage]
    assert options.model == "eleven_v3_conversational"
    assert options.voice_id == "a-voice"


def test_the_agents_turn_declaration_reaches_the_ears_and_nothing_else() -> None:
    declared = AgentConfig(slug="clinica-norte", turn=Turn(endpointing_ms=650))
    built = pipeline_for(declared, settings(), NOTHING_BROUGHT)
    assert isinstance(built.stt, deepgram.STTv2)
    assert built.stt._opts.eot_timeout_ms == 650  # pyright: ignore[reportPrivateUsage]


def test_a_declared_language_reaches_the_voice_and_the_ears_as_its_primary_subtag() -> None:
    """`es-ES` is a language people write and no vendor lists: the plugins are handed `es`."""
    built = pipeline_for(
        AgentConfig(slug="clinica-norte", language="es-ES"), settings(), NOTHING_BROUGHT
    )
    assert isinstance(built.tts, cartesia.TTS)
    spoken = built.tts._opts.language  # pyright: ignore[reportPrivateUsage]
    assert spoken is not None and spoken.language == "es"
