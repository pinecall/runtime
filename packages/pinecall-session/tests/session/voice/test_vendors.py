"""The Kit this process holds: one call, one pipeline, and the settings read exactly once."""

import pytest
from livekit.plugins import cartesia

from pinecall.providers.session_vendors import Pipeline
from pinecall.session.voice.vendors import kit_for
from pinecall.types import NOTHING_BROUGHT, AgentConfig, Brought
from pinecall_testkit.keyed import keyed_for_every_vendor

pytestmark = pytest.mark.unit

CLARA = AgentConfig(slug="clinica-norte")


def test_every_call_gets_its_own_vendors_built_from_its_own_declaration() -> None:
    """The three depend on the agent, so nothing is shared between two calls of one process."""
    kit = kit_for(keyed_for_every_vendor())
    first, second = kit(CLARA, NOTHING_BROUGHT), kit(CLARA, NOTHING_BROUGHT)
    assert first.llm is not second.llm
    assert (first.llm.label, first.stt.label) == (second.llm.label, second.stt.label)


def test_one_process_serves_two_orgs_and_neither_call_is_built_with_the_others_key() -> None:
    """The org's keys are the CALL's and never the closure's: a Kit holds the box's alone."""
    kit = kit_for(keyed_for_every_vendor())
    theirs = kit(CLARA, Brought(keys={"cartesia": "the-tenants-own-key"}))
    ours = kit(CLARA, NOTHING_BROUGHT)
    assert _the_voices_key(theirs) == "the-tenants-own-key"
    assert _the_voices_key(ours) == "nobody-will-ever-deploy-this"


def _the_voices_key(built: Pipeline) -> str:
    """The key a built Cartesia TTS will actually speak with, read off the plugin's options."""
    assert isinstance(built.tts, cartesia.TTS)
    return str(built.tts._opts.api_key)  # pyright: ignore[reportPrivateUsage]
