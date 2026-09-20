"""The registry across two worlds: a slug held in each, a door in one, and the key saying which."""

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, SANDBOX, DeclarationRefused
from pinecall.types.channel import Channel
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

A_SOCKET = "app_the_box"
ANOTHER_SOCKET = "app_the_laptop"
A_NUMBER = "+34910000000"


def a_door(channel: Channel, number: str | None = None) -> defs.Route:
    return defs.Route(channel=channel, number=number)


async def test_the_same_slug_is_held_once_in_each_world_and_neither_sees_the_other() -> None:
    """A laptop's `pinecall start` on a dev key and the box's on a production key are two agents."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("web")])
    deployed, written = (
        registry.of(PRODUCTION, "clinica-norte"),
        registry.of(SANDBOX, "clinica-norte"),
    )
    assert deployed is not None and deployed.owner == A_SOCKET
    assert written is not None and written.owner == ANOTHER_SOCKET
    assert [held.env for held in registry.holding("madrid", PRODUCTION)] == [PRODUCTION]
    assert [held.env for held in registry.holding("madrid", SANDBOX)] == [SANDBOX]
    assert registry.serving(SANDBOX, "clinica-norte", None) is written
    assert registry.on(SANDBOX, "clinica-norte", A_SOCKET) is None


async def test_a_configure_in_one_world_leaves_the_other_worlds_declaration_alone() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("web")])
    await registry.configure(
        ANOTHER_SOCKET, SANDBOX, "clinica-norte", defs.AgentConfig(language="es-UY")
    )
    deployed = registry.of(PRODUCTION, "clinica-norte")
    assert deployed is not None
    assert deployed.config.language is None


async def test_a_development_key_cannot_claim_a_production_door() -> None:
    """A number rings in one place: the world that holds it is named in the refusal."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(
        A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("phone", A_NUMBER)]
    )
    with pytest.raises(
        DeclarationRefused, match="already answers for agent clinica-norte in production"
    ):
        await registry.register(
            ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("phone", A_NUMBER)]
        )
    answering = registry.at("phone", A_NUMBER)
    assert answering is not None
    assert answering.env == PRODUCTION


async def test_a_socket_leaving_one_world_frees_nothing_in_the_other() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("web")])
    assert (await registry.release(ANOTHER_SOCKET)) == frozenset({"clinica-norte"})
    assert registry.of(SANDBOX, "clinica-norte") is None
    assert registry.of(PRODUCTION, "clinica-norte") is not None


async def test_the_register_says_which_world_and_the_org_counts_the_slug_once() -> None:
    """agent.registered carries env; an agent held in both worlds is one agent to the quota."""
    registry = Registry(Logs(MemoryStore()))
    deployed = await registry.register(
        A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")]
    )
    written = await registry.register(
        ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("web")]
    )
    assert (deployed.data["env"], written.data["env"]) == (PRODUCTION, SANDBOX)
    assert {held.slug for held in registry.holding("madrid")} == {"clinica-norte"}


async def test_the_declaration_a_slug_alone_names_is_productions_when_it_is_held_there() -> None:
    """The sink knows an entry's agent and no world: it reads the deployed declaration first."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(ANOTHER_SOCKET, "madrid", SANDBOX, "clinica-norte", [a_door("web")])
    await registry.configure(
        ANOTHER_SOCKET, SANDBOX, "clinica-norte", defs.AgentConfig(language="es-UY")
    )
    written = registry.declared("clinica-norte")
    assert written is not None and written.language == "es-UY"
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    deployed = registry.declared("clinica-norte")
    assert deployed is not None and deployed.language is None
    assert registry.declared("nobody") is None


async def test_a_socket_leaving_writes_agent_detached_saying_whether_anybody_is_left() -> None:
    """The other half of register: which socket, which world, whether the agent is still held."""
    store = MemoryStore()
    registry = Registry(Logs(store))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.release(ANOTHER_SOCKET)
    await registry.release(A_SOCKET)
    written = [
        entry
        for entry in await store.agent_since("clinica-norte")
        if entry.type == "agent.detached"
    ]
    assert [(entry.data["app"], entry.data["env"], entry.data["left"]) for entry in written] == [
        (ANOTHER_SOCKET, PRODUCTION, False),
        (A_SOCKET, PRODUCTION, True),
    ]
