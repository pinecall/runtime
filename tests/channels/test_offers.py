"""Tests for who takes a call: the rooms kept, a worker with a seat, the overflow, once each."""

import dataclasses
import logging

import pytest

from pinecall.channels import offers
from pinecall.channels.offers import Offering
from pinecall.channels.rooms import Dispatch, read_dispatch
from pinecall.fleet.roster import Roster
from pinecall.postgres.pool import Pool
from pinecall.process.connections import closed
from tests.conftest import a_worker_heard, an_offering, postgres
from tests.fakes.livekit import per_world

pytestmark = postgres


async def test_a_room_is_kept_once_and_due_until_offered(pool: Pool) -> None:
    assert await offers.opened(pool, "call-1", "pinecall", '{"org":"o"}', 100.0)
    assert not await offers.opened(pool, "call-1", "pinecall", "{}", 101.0)
    (waiting,) = await offers.due(pool, before=100.0, limit=10)
    assert (waiting.room, waiting.dispatch, waiting.offers, waiting.worker) == (
        "call-1",
        '{"org":"o"}',
        0,
        None,
    )


async def test_two_gateways_offering_one_room_offer_it_once(pool: Pool) -> None:
    await offers.opened(pool, "call-1", "pinecall", "{}", 100.0)
    (read,) = await offers.due(pool, before=100.0, limit=10)
    assert await offers.claimed(pool, read, "pinecall/a", 101.0)
    assert not await offers.claimed(pool, read, "pinecall/b", 101.0)
    assert await offers.due(pool, before=101.0, limit=10) == []
    (again,) = await offers.due(pool, before=120.0, limit=10)
    assert (again.worker, again.offers) == ("pinecall/a", 1)
    assert await offers.in_flight(pool, since=100.0) == {"pinecall/a": 1}


async def test_an_opened_room_and_an_old_one_are_forgotten(pool: Pool) -> None:
    await offers.opened(pool, "call-1", "pinecall", "{}", 100.0)
    await offers.opened(pool, "call-2", "pinecall-sandbox", "{}", 500.0)
    assert await offers.waiting(pool, since=0.0) == {"pinecall": 1, "pinecall-sandbox": 1}
    await offers.forgotten(pool, "call-1")
    await offers.aged_out(pool, before=600.0)
    assert await offers.waiting(pool, since=0.0) == {}


async def test_a_room_offered_goes_to_a_worker_with_a_seat_and_only_once(
    pool: Pool, caplog: pytest.LogCaptureFixture
) -> None:
    servers = per_world()
    offering = an_offering(pool, servers, "pinecall")
    carried = Dispatch(org="org_a", env="production")
    with caplog.at_level(logging.INFO, logger="pinecall.channels.offers"):
        assert await offering.offer("call-1", "pinecall", carried) == "pinecall/w1"
    assert "room call-1: offered to pinecall/w1: 4 seats free, heard 0 s ago" in caplog.messages
    assert await offering.offer("call-1", "pinecall", carried) is None
    (made,) = servers["production"].dispatcher.made
    assert (made.agent_name, read_dispatch(made.metadata)) == ("pinecall/w1", carried)
    await closed(servers)


# The fleets row says each fleet's world, and a world's rooms live on its own LiveKit.
async def test_a_room_is_dispatched_on_the_livekit_of_its_fleets_world(pool: Pool) -> None:
    servers = per_world()
    offering = an_offering(pool, servers, "pinecall-sandbox")
    carried = Dispatch(org="org_a", env="sandbox")
    assert await offering.offer("call-1", "pinecall-sandbox", carried) == "pinecall-sandbox/w1"
    (made,) = servers["sandbox"].dispatcher.made
    assert (made.room, made.agent_name) == ("call-1", "pinecall-sandbox/w1")
    assert servers["production"].dispatcher.made == []
    await closed(servers)


async def test_a_room_of_a_fleet_no_world_dispatches_to_is_offered_nowhere(
    pool: Pool, caplog: pytest.LogCaptureFixture
) -> None:
    servers = per_world()
    offering = an_offering(pool, servers, "pinecall-staging")
    with caplog.at_level(logging.WARNING, logger="pinecall.channels.offers"):
        assert await offering.offer("call-1", "pinecall-staging", Dispatch()) is None
    assert "room call-1: fleet pinecall-staging is no world's" in caplog.text
    assert all(server.dispatcher.made == [] for server in servers.values())
    await closed(servers)


# Every room is let go by its third offer: one kept past a minute means nobody sweeps.
async def test_the_doctor_names_rooms_kept_past_every_offer(pool: Pool) -> None:
    await offers.opened(pool, "call-1", "pinecall", "{}", 100.0)
    assert await offers.examined(pool, 100.0 + offers.UNSWEPT_AFTER_S) is None
    assert await offers.examined(pool, 101.0 + offers.UNSWEPT_AFTER_S) == offers.UNSWEPT.format(n=1)


async def test_a_room_of_a_fleet_whose_workers_are_full_goes_to_the_overflow(pool: Pool) -> None:
    servers = per_world()
    roster = Roster()
    a_worker_heard(roster, "pinecall", active=4)
    offering = Offering(pool=pool, servers=servers, roster=roster)
    assert await offering.offer("call-1", "pinecall", Dispatch()) == "pinecall/overflow"
    assert await offers.waiting(pool, since=0.0) == {}
    await closed(servers)


# A gateway that just started has heard nobody: the room waits for the sweep, not the sentence.
async def test_a_room_of_a_fleet_nobody_was_heard_from_waits_then_goes_to_the_overflow(
    pool: Pool,
) -> None:
    servers = per_world()
    offering = Offering(pool=pool, servers=servers, roster=Roster())
    assert await offering.offer("call-1", "pinecall", Dispatch()) is None
    assert servers["production"].dispatcher.made == []
    (waiting,) = await offers.due(pool, before=0.0, limit=1)
    waited = dataclasses.replace(waiting, seen_at=waiting.seen_at - offers.WAITS_FOR_A_WORKER_S)
    assert await offering.offered(waited) == "pinecall/overflow"
    await closed(servers)
