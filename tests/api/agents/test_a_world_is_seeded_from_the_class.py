"""A world with nothing set is seeded from the class on agent.configure, once, in its own corner."""

import asyncio
from collections.abc import Mapping

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.socket import SEEDED
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import PRODUCTION, Greeting, Tuning
from tests.api.conftest import A_RECORD, AGENT
from tests.api.talking import a_door, a_frame, a_register, an_app

pytestmark = pytest.mark.unit

DECLARED = {
    "llm": {"provider": "anthropic", "model": "claude-haiku-4-5"},
    "stt": {"provider": "deepgram", "model": ""},
    "voice": {"name": "mateo"},
    "greeting": {"say": "Clínica Norte, buenas."},
    "memory": {"remember": ["allergies"], "forget": ["card numbers"]},
    "docs": {"base": "clinica", "k": 4},
}


def configured(gateway: TestClient, config: Mapping[str, object]) -> None:
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        app_socket.send_json(a_frame("agent.configure", AGENT, {"config": config}))
        app_socket.receive_json()


def test_the_first_configure_seeds_the_orgs_own_corner_once(
    gateway: TestClient, tuning: MemoryTuning
) -> None:
    configured(gateway, DECLARED)
    seeded = asyncio.run(tuning.own(A_RECORD.org, PRODUCTION, "", AGENT))
    assert seeded is not None
    assert (seeded.version, seeded.author, seeded.note) == (1, A_RECORD.key_id, SEEDED)
    assert seeded.value == Tuning(
        voice="mateo",
        stt="deepgram",
        llm="anthropic/claude-haiku-4-5",
        greeting=Greeting(say="Clínica Norte, buenas."),
        memory=seeded.value.memory,
        knowledge=seeded.value.knowledge,
    )
    assert seeded.value.memory is not None and seeded.value.memory.remember == ("allergies",)
    assert [one.base for one in seeded.value.knowledge] == ["clinica"]
    # The second configure — a reconnect, a colleague's process — finds the row and leaves it.
    configured(
        gateway, {**DECLARED, "llm": {"provider": "anthropic", "model": "claude-sonnet-4-5"}}
    )
    again = asyncio.run(tuning.own(A_RECORD.org, PRODUCTION, "", AGENT))
    assert (
        again is not None and again.version == 1 and again.value.llm == "anthropic/claude-haiku-4-5"
    )


def test_a_class_that_declares_none_of_the_environment_seeds_nothing(
    gateway: TestClient, tuning: MemoryTuning
) -> None:
    configured(gateway, {"language": "es"})
    assert asyncio.run(tuning.own(A_RECORD.org, PRODUCTION, "", AGENT)) is None
