"""A reader of the pipeline door on the very app the worker knocks at: one gateway, two of them."""

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.types import PRODUCTION, Greeting, Tuning
from pinecall.worker.gateway_client import Gateway
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


async def declared(
    registry: Registry, tuning: MemoryTuning, greeting: Greeting | None = None
) -> None:
    """The clinic on air: a socket holding it; its voice, model and opening set in production."""
    await registry.register(AN_OWNER, A_RECORD.org, PRODUCTION, AGENT)
    await registry.configure(AN_OWNER, PRODUCTION, AGENT, defs.AgentConfig(language="es"))
    await tuning.put(
        A_RECORD.org,
        PRODUCTION,
        "",
        AGENT,
        Tuning(
            tts="elevenlabs",
            voice="mateo",
            llm="anthropic/claude-haiku-4-5",
            greeting=greeting or Greeting(say="Clínica Norte, buenas."),
        ),
        author="k_1",
        note=None,
        if_version=None,
    )
