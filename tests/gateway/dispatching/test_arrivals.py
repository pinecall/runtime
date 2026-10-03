"""Tests for a person alone in a room: its newest dispatch, to a fleet, offered to a worker."""

from livekit import api
from livekit.protocol.agent_dispatch import AgentDispatch
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels import offers
from pinecall.channels.rooms import Dispatch, read_dispatch, written
from pinecall.gateway.dispatching.arrivals import FINISHED, JOINED, LEFT, arrived, settled
from pinecall.postgres.pool import Pool
from tests.conftest import an_offering, postgres
from tests.fakes.livekit import Server

pytestmark = postgres

ROOM = "call-_+34600111222_abc"

CARRIED = Dispatch(org="org_a", env="production")


def an_event(kind: str, who: str) -> WebhookEvent:
    """LiveKit's word that a participant of this kind joined or left the room."""
    return WebhookEvent(
        event=kind,
        room=api.Room(name=ROOM),
        participant=api.ParticipantInfo(identity="someone", kind=who),
    )


def a_room_rung(*dispatches: tuple[str, Dispatch]) -> Server:
    """A room with a caller in it and no agent, and these dispatches on it, oldest first."""
    server = Server()
    server.rooms.people = {ROOM}
    server.dispatcher.seeded = [
        AgentDispatch(room=ROOM, agent_name=name, metadata=written(carried))
        for name, carried in dispatches
    ]
    return server


async def test_a_caller_joining_is_offered_to_a_worker_with_the_rules_dispatch(pool: Pool) -> None:
    server = a_room_rung(("pinecall", CARRIED))
    joined = an_event(JOINED, "SIP")
    assert await arrived(an_offering(pool, server, "pinecall"), joined) == "pinecall/w1"
    (made,) = server.dispatcher.made
    assert (made.room, made.agent_name, read_dispatch(made.metadata)) == (
        ROOM,
        "pinecall/w1",
        CARRIED,
    )
    await server.aclose()


async def test_a_person_joining_a_room_an_agent_is_in_changes_nothing(pool: Pool) -> None:
    server = a_room_rung(("pinecall", CARRIED))
    server.rooms.existing = {ROOM: True}
    joined = an_event(JOINED, "STANDARD")
    assert await arrived(an_offering(pool, server, "pinecall"), joined) is None
    assert server.dispatcher.made == []
    await server.aclose()


async def test_an_agent_leaving_a_call_the_gateway_sent_changes_nothing(pool: Pool) -> None:
    server = a_room_rung(("pinecall", CARRIED), ("pinecall/w1", CARRIED))
    left = an_event(LEFT, "AGENT")
    assert await arrived(an_offering(pool, server, "pinecall"), left) is None
    assert server.dispatcher.made == []
    await server.aclose()


async def test_a_caller_handed_to_the_sandbox_is_offered_to_a_sandbox_worker(pool: Pool) -> None:
    handed = Dispatch(org="org_a", env="sandbox", diverted_from="production")
    server = a_room_rung(
        ("pinecall", CARRIED), ("pinecall/w9", CARRIED), ("pinecall-sandbox", handed)
    )
    left = an_event(LEFT, "AGENT")
    assert (
        await arrived(an_offering(pool, server, "pinecall-sandbox"), left) == "pinecall-sandbox/w1"
    )
    (made,) = server.dispatcher.made
    assert read_dispatch(made.metadata) == handed
    await server.aclose()


# The row outlives the room otherwise, and a dispatch on a room that is gone makes it again.
async def test_a_room_that_ended_before_a_worker_opened_its_call_is_let_go(pool: Pool) -> None:
    server = Server()
    offering = an_offering(pool, server, "pinecall")
    assert await offering.offer(ROOM, "pinecall", CARRIED) == "pinecall/w1"
    assert await offers.waiting(pool, since=0.0) == {"pinecall": 1}
    await settled(offering, WebhookEvent(event=FINISHED, room=api.Room(name=ROOM)))
    assert await offers.waiting(pool, since=0.0) == {}
    await server.aclose()


# The sentence's job opens no call: the agent in the room is what says the offer was taken.
async def test_a_room_an_agent_joined_is_let_go_and_a_person_joining_keeps_it(pool: Pool) -> None:
    server = Server()
    offering = an_offering(pool, server, "pinecall")
    assert await offering.offer(ROOM, "pinecall", CARRIED) == "pinecall/w1"
    await settled(offering, an_event(JOINED, "STANDARD"))
    assert await offers.waiting(pool, since=0.0) == {"pinecall": 1}
    await settled(offering, an_event(JOINED, "AGENT"))
    assert await offers.waiting(pool, since=0.0) == {}
    await server.aclose()
