"""Tests for the rooms kept for offering: kept once, offered once per count, forgotten, aged out."""

from pinecall.gateway.dispatching import offers
from pinecall.postgres.pool import Pool
from tests.conftest import postgres

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
    assert await offers.offered(pool, read, "pinecall/a", 101.0)
    assert not await offers.offered(pool, read, "pinecall/b", 101.0)
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
