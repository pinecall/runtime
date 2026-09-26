"""The registry on its own: who owns what, which door resolves to whom, and what a hangup frees."""

import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import PRODUCTION, DeclarationRefused
from pinecall.types.channel import Channel
from pinecall_protocol import defs

pytestmark = pytest.mark.unit

A_SOCKET = "app_the_first"
ANOTHER_SOCKET = "app_the_second"
A_NUMBER = "+34910000000"


def a_door(channel: Channel, number: str | None = None) -> defs.Route:
    return defs.Route(channel=channel, number=number)


# A register brings no doors with it: a door is a row an operator typed (routes/table.py) and it
# outlives every socket, and the widget is not a door at all — every agent is on the web.
async def test_a_register_brings_no_doors_and_takes_nothing_from_anybody() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "tienda-sur")
    assert registry.of(PRODUCTION, "clinica-norte") is not None
    assert registry.of(PRODUCTION, "tienda-sur") is not None


async def test_a_re_register_on_the_same_socket_keeps_what_it_declared() -> None:
    """The app is correcting its class, not forgetting who it is."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.configure(
        A_SOCKET, PRODUCTION, "clinica-norte", defs.AgentConfig(language="es-ES")
    )
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.config.language == "es-ES"


async def test_a_configure_only_changes_the_fields_the_app_sent() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.configure(
        A_SOCKET,
        PRODUCTION,
        "clinica-norte",
        defs.AgentConfig(uses_knowledge=True, language="es-ES"),
    )
    entry = await registry.configure(
        A_SOCKET, PRODUCTION, "clinica-norte", defs.AgentConfig(language="es-UY")
    )
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert (held.config.uses_knowledge, held.config.language) == (True, "es-UY")
    assert entry.data["changed"] == ["language"]


async def test_the_agents_own_log_carries_both_the_register_and_the_configure() -> None:
    store = MemoryStore()
    registry = Registry(Logs(store))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.configure(A_SOCKET, PRODUCTION, "clinica-norte", defs.AgentConfig(language="es"))
    written = await store.agent_since("clinica-norte")
    assert [entry.type for entry in written] == ["agent.registered", "agent.configured"]
    assert [entry.seq for entry in written] == [1, 2]


async def test_the_wire_declares_a_tool_the_domain_reads_whole() -> None:
    """The wire carries side_effect and the read-back now, so the registry maps by name only."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.configure(
        A_SOCKET,
        PRODUCTION,
        "clinica-norte",
        defs.AgentConfig(
            tools=[
                defs.ToolSpec(
                    name="book_slot",
                    description="Book the slot the caller chose.",
                    parameters={"type": "object", "properties": {"at": {"type": "string"}}},
                    side_effect="irreversible",
                    confirm="Le reservo el {at}. ¿Confirmo?",
                )
            ]
        ),
    )
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    booking = held.config.tools_by_name["book_slot"]
    assert booking.side_effect == "irreversible"
    assert booking.confirm == "Le reservo el {at}. ¿Confirmo?"
    assert booking.confirm is not None
    assert booking.timeout_s == 30.0


# ── a web door is the agent, not (channel, null) (docs/decisions/routes.md) ─────


# ── many sockets on one agent (docs/decisions/dispatch.md) ──────────────────────


async def test_two_sockets_hold_one_agent_and_a_new_call_takes_the_newest() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.owner == ANOTHER_SOCKET
    assert registry.on(PRODUCTION, "clinica-norte", A_SOCKET) is not None
    assert registry.on(PRODUCTION, "clinica-norte", "app_nobody") is None


async def test_the_socket_that_joins_starts_from_what_the_agent_already_declared() -> None:
    """The window between its register and its configure is a round trip; a call can land in it."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.configure(
        A_SOCKET, PRODUCTION, "clinica-norte", defs.AgentConfig(language="es-ES")
    )
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.config.language == "es-ES"


async def test_a_socket_correcting_its_own_doors_keeps_its_place_among_the_holders() -> None:
    """It is the same process, not a newer one, so it does not jump the queue."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.owner == ANOTHER_SOCKET


async def test_the_agent_stands_while_one_of_its_two_sockets_leaves() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    assert (await registry.release(ANOTHER_SOCKET)) == frozenset({"clinica-norte"})
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.owner == A_SOCKET


async def test_an_agent_held_by_two_sockets_is_listed_once() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    assert [held.slug for held in registry.holding("madrid")] == ["clinica-norte"]


async def test_a_slug_another_fleet_is_holding_is_refused() -> None:
    """The one-socket rule was enforcing this silently; with it gone the rule is written down."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    with pytest.raises(DeclarationRefused, match="one org"):
        await registry.register(ANOTHER_SOCKET, "barcelona", PRODUCTION, "clinica-norte")


async def test_the_register_hands_the_socket_its_own_id_back() -> None:
    """`?app=` on the chat door is this id, and nothing else in the process knows it."""
    registry = Registry(Logs(MemoryStore()))
    entry = await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    assert entry.data["app"] == A_SOCKET


# ── a socket that takes no call it did not open (docs/decisions/dispatch.md) ────


async def test_a_call_that_named_no_app_skips_the_socket_that_takes_none() -> None:
    """The console registered last; the call is a stranger's, so it goes to the one before it."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(
        ANOTHER_SOCKET,
        "madrid",
        PRODUCTION,
        "clinica-norte",
        takes_unclaimed=False,
    )
    serving = registry.serving(PRODUCTION, "clinica-norte", None)
    assert serving is not None
    assert serving.owner == A_SOCKET


async def test_the_socket_that_takes_no_unclaimed_call_is_still_a_full_holder() -> None:
    """It registers, it declares, it is listed, and the call that names it runs on it."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", takes_unclaimed=False)
    held = registry.of(PRODUCTION, "clinica-norte")
    assert held is not None
    assert held.owner == A_SOCKET
    assert registry.on(PRODUCTION, "clinica-norte", A_SOCKET) is not None
    assert [one.slug for one in registry.holding("madrid")] == ["clinica-norte"]
    named = registry.serving(PRODUCTION, "clinica-norte", A_SOCKET)
    assert named is not None
    assert named.owner == A_SOCKET


async def test_nobody_serves_a_call_of_an_agent_only_such_sockets_hold() -> None:
    """Refused rather than dropped into somebody's terminal: the doors turn this None into words."""
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte", takes_unclaimed=False)
    assert registry.serving(PRODUCTION, "clinica-norte", None) is None


async def test_a_draining_socket_is_skipped_for_a_new_call_and_still_holds_its_agent() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    await registry.register(ANOTHER_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    registry.drain(ANOTHER_SOCKET, PRODUCTION, "clinica-norte")
    serving = registry.serving(PRODUCTION, "clinica-norte", None)
    assert serving is not None and serving.owner == A_SOCKET
    assert registry.on(PRODUCTION, "clinica-norte", ANOTHER_SOCKET) is not None


async def test_the_last_socket_draining_leaves_no_one_to_take_a_new_call() -> None:
    registry = Registry(Logs(MemoryStore()))
    await registry.register(A_SOCKET, "madrid", PRODUCTION, "clinica-norte")
    registry.drain(A_SOCKET, PRODUCTION, "clinica-norte")
    assert registry.serving(PRODUCTION, "clinica-norte", None) is None


async def test_draining_an_agent_this_socket_does_not_hold_is_refused() -> None:
    registry = Registry(Logs(MemoryStore()))
    with pytest.raises(DeclarationRefused, match="not registered on this socket"):
        registry.drain(A_SOCKET, PRODUCTION, "clinica-norte")
