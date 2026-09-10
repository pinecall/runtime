"""A reader of the pipeline door on the very app the worker knocks at: one gateway, two of them."""

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.worker.client import Gateway
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app

# The id an app socket would have been minted; the registry never looks inside it.
AN_OWNER = "app_the_console_conftest"


# It takes worker_gateway on purpose: that fixture answers every dependency of the app from the
# test, and this client speaks to the same one — which is what lets a test turn a knob here and
# read it back off the door the worker itself asks.
@pytest.fixture
async def fleet_http(worker_gateway: Gateway) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """An HTTP client on the gateway, knocking at an agent's doors with the org's key."""
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    yield http
    await http.aclose()


async def declared(registry: Registry, greeting: defs.GreetingConfig | None = None) -> None:
    """The clinic on air: an app socket holding it, with a voice and a model already declared."""
    await registry.register(AN_OWNER, A_RECORD.org, AGENT, [defs.Route(channel="web", number=None)])
    await registry.configure(
        AN_OWNER,
        AGENT,
        defs.AgentConfig(
            greeting=greeting or defs.GreetingConfig(say="Clínica Norte, buenas."),
            language="es",
            voice=defs.VoiceConfig(provider="elevenlabs", voice_id="a-declared-voice"),
            llm=defs.ModelConfig(provider="anthropic", model="claude-haiku-4-5"),
        ),
    )
