"""The registry across two worlds: a slug held in each, a door in one, and the key saying which."""

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import DEVELOPMENT, PRODUCTION, DeclarationRefused
from pinecall.types.channel import Channel
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

A_SOCKET = "app_the_box"
ANOTHER_SOCKET = "app_the_laptop"
A_NUMBER = "+34910000000"


def a_door(channel: Channel, number: str | None = None) -> defs.Route:
    return defs.Route(channel=channel, number=number)


async def test_the_same_slug_is_held_once_in_each_world_and_neither_sees_the_other() -> None:
    """A laptop's `pinecall run` on a dev key and the box's on a production key are two agents."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("web")])
    deployed, written = (
        registry.of(PRODUCTION, "clinica-norte"),
        registry.of(DEVELOPMENT, "clinica-norte"),
    )
    assert deployed is not None and deployed.owner == A_SOCKET
    assert written is not None and written.owner == ANOTHER_SOCKET
    assert [held.env for held in registry.holding("madrid", PRODUCTION)] == [PRODUCTION]
    assert [held.env for held in registry.holding("madrid", DEVELOPMENT)] == [DEVELOPMENT]
    assert registry.serving(DEVELOPMENT, "clinica-norte", None) is written
    assert registry.on(DEVELOPMENT, "clinica-norte", A_SOCKET) is None


async def test_a_configure_in_one_world_leaves_the_other_worlds_declaration_alone() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("web")])
    await registry.configure(
        ANOTHER_SOCKET, DEVELOPMENT, "clinica-norte", defs.AgentConfig(language="es-UY")
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
            ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("phone", A_NUMBER)]
        )
    answering = registry.at("phone", A_NUMBER)
    assert answering is not None
    assert answering.env == PRODUCTION


async def test_a_socket_leaving_one_world_frees_nothing_in_the_other() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    await registry.register(ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("web")])
    assert registry.release(ANOTHER_SOCKET) == frozenset({"clinica-norte"})
    assert registry.of(DEVELOPMENT, "clinica-norte") is None
    assert registry.of(PRODUCTION, "clinica-norte") is not None


async def test_the_register_says_which_world_and_the_org_counts_the_slug_once() -> None:
    """agent.registered carries env; an agent held in both worlds is one agent to the quota."""
    registry = Registry(Logs(MemoryStore()))
    deployed = await registry.register(
        A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")]
    )
    written = await registry.register(
        ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("web")]
    )
    assert (deployed.data["env"], written.data["env"]) == (PRODUCTION, DEVELOPMENT)
    assert {held.slug for held in registry.holding("madrid")} == {"clinica-norte"}


async def test_the_declaration_a_slug_alone_names_is_productions_when_it_is_held_there() -> None:
    """The sink knows an entry's agent and no world: it reads the deployed declaration first."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(ANOTHER_SOCKET, "madrid", DEVELOPMENT, "clinica-norte", [a_door("web")])
    await registry.configure(
        ANOTHER_SOCKET, DEVELOPMENT, "clinica-norte", defs.AgentConfig(language="es-UY")
    )
    written = registry.declared("clinica-norte")
    assert written is not None and written.language == "es-UY"
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", [a_door("web")])
    deployed = registry.declared("clinica-norte")
    assert deployed is not None and deployed.language is None
    assert registry.declared("nobody") is None
