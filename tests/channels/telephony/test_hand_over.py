"""Tests for a ring handed to the sandbox by SIP: its pair, headers, dispatch and bridge."""

from livekit import api
from livekit.protocol.webhook import WebhookEvent

from pinecall.channels.rooms import Dispatch
from pinecall.channels.telephony.hand_over import (
    AGENT,
    ATTRIBUTE_OF,
    HOLDER,
    LEFT,
    LEG_PREFIX,
    ORG,
    handed_in,
    headers_of,
    password_of,
    unbridged,
)
from tests.fakes.livekit import Server

ROOM = "call-_+59899000001_abc"

MOVED = Dispatch(agent="recepcion", org="org_a", env="sandbox", holder="m_ana")

# What the hand-over rule dispatches with every ring.
RULED = Dispatch(env="sandbox", diverted_from="production")

LEG = f"{LEG_PREFIX}m_ana"


def a_leaving(identity: str, kind: str) -> WebhookEvent:
    """LiveKit's word that a participant left production's room."""
    return WebhookEvent(
        event=LEFT,
        room=api.Room(name=ROOM),
        participant=api.ParticipantInfo(identity=identity, kind=kind),
    )


def a_bridge() -> Server:
    """Production's LiveKit with the caller and the hand-over leg in the room, and no agent."""
    server = Server()
    server.rooms.people = {ROOM}
    server.rooms.seated = {
        ROOM: [api.ParticipantInfo(identity=LEG, kind=api.ParticipantInfo.Kind.SIP)]
    }
    return server


def test_the_pair_is_drawn_from_livekits_secret_and_changes_with_it() -> None:
    assert password_of("a secret") == password_of("a secret")
    assert password_of("a secret") != password_of("another secret")
    assert "a secret" not in password_of("a secret")


def test_the_leg_carries_whose_ring_it_is_and_the_trunk_reads_each_header_back() -> None:
    headers = headers_of(MOVED)
    carried = {ATTRIBUTE_OF[header]: value for header, value in headers.items()}
    assert carried == {ORG: "org_a", AGENT: "recepcion", HOLDER: "m_ana"}


def test_the_rules_dispatch_is_made_the_developers_by_what_the_leg_carries() -> None:
    carried = {ORG: "org_a", AGENT: "recepcion", HOLDER: "m_ana", "sip.phoneNumber": "+5989"}
    assert handed_in(RULED, carried) == Dispatch(
        agent="recepcion", org="org_a", env="sandbox", holder="m_ana", diverted_from="production"
    )


def test_a_dispatch_naming_its_agent_never_diverted_or_carried_nothing_is_left_as_it_is() -> None:
    carried = {ORG: "org_b", AGENT: "sales", HOLDER: "m_ben"}
    in_the_room = MOVED.model_copy(update={"diverted_from": "production"})
    assert handed_in(in_the_room, carried) == in_the_room
    assert handed_in(RULED, {}) == RULED
    assert handed_in(Dispatch(org="org_a", env="sandbox"), carried) == Dispatch(
        org="org_a", env="sandbox"
    )


async def test_the_sandbox_hanging_up_closes_the_room_and_with_it_the_callers_leg() -> None:
    server = a_bridge()
    assert await unbridged(server, a_leaving(LEG, "SIP"))
    assert server.rooms.people == set()
    await server.aclose()


async def test_the_caller_hanging_up_closes_the_room_and_with_it_the_leg_to_the_sandbox() -> None:
    server = a_bridge()
    server.rooms.people = set()
    assert await unbridged(server, a_leaving("sip_+59899000001", "SIP"))
    assert server.rooms.seated == {}
    await server.aclose()


async def test_a_room_no_hand_over_bridges_is_left_to_its_agent() -> None:
    server = Server()
    server.rooms.existing = {ROOM: True}
    assert not await unbridged(server, a_leaving("sip_+59899000001", "SIP"))
    assert not await unbridged(server, a_leaving("agent", "AGENT"))
    assert server.rooms.existing == {ROOM: True}
    await server.aclose()
