"""Tests for the sweep: a room nobody opened in time is offered again, then to the overflow."""

import time

from pinecall.channels import offers
from pinecall.channels.offers import OFFERED_AGAIN_AFTER_S, OFFERS, Offering
from pinecall.domain.names import Env
from pinecall.fleet.roster import Roster
from pinecall.gateway.dispatching.sweep import swept
from pinecall.postgres.pool import Pool
from pinecall.process.connections import closed
from tests.conftest import a_worker_heard, postgres
from tests.fakes.livekit import Server, per_world

pytestmark = postgres

DISPATCH = '{"org":"org_a","env":"production"}'


def an_offering_of(pool: Pool, servers: dict[Env, Server], *workers: str) -> Offering:
    """The gateway's dispatcher with these workers of production's fleet heard, seats free."""
    roster = Roster()
    for worker in workers:
        a_worker_heard(roster, "pinecall", worker)
    return Offering(pool=pool, servers=servers, roster=roster)


async def a_room_offered(pool: Pool, to: str, times: int, ago: float) -> None:
    """call-1 kept, and offered `times` times, the last to `to`, `ago` seconds back."""
    await offers.opened(pool, "call-1", "pinecall", DISPATCH, time.time() - ago - 1)
    for _ in range(times):
        (due,) = await offers.due(pool, before=time.time(), limit=1)
        await offers.claimed(pool, due, to, time.time() - ago)


async def test_a_room_nobody_opened_in_time_is_offered_to_another_worker(pool: Pool) -> None:
    await a_room_offered(pool, "pinecall/a", times=1, ago=OFFERED_AGAIN_AFTER_S + 1)
    servers = per_world()
    server = servers["production"]
    assert await swept(an_offering_of(pool, servers, "a", "b")) == ["call-1"]
    (made,) = server.dispatcher.made
    assert (made.room, made.agent_name, made.metadata) == ("call-1", "pinecall/b", DISPATCH)
    assert await offers.in_flight(pool, since=time.time() - 1) == {"pinecall/b": 1}
    await closed(servers)


async def test_a_room_offered_lately_is_left_to_its_worker(pool: Pool) -> None:
    await a_room_offered(pool, "pinecall/a", times=1, ago=1.0)
    servers = per_world()
    server = servers["production"]
    assert await swept(an_offering_of(pool, servers, "a", "b")) == []
    assert server.dispatcher.made == []
    await closed(servers)


async def test_after_its_last_offer_a_room_goes_to_the_overflow_and_is_forgotten(
    pool: Pool,
) -> None:
    await a_room_offered(pool, "pinecall/a", times=OFFERS, ago=OFFERED_AGAIN_AFTER_S + 1)
    servers = per_world()
    server = servers["production"]
    await swept(an_offering_of(pool, servers, "a", "b"))
    (made,) = server.dispatcher.made
    assert made.agent_name == "pinecall/overflow"
    assert await offers.waiting(pool, since=0.0) == {}
    await closed(servers)
