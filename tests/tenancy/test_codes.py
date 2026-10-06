"""Tests for the codes a caller keys to tie their call to a page."""

import asyncio

import pytest

from pinecall.domain.errors import QuotaExhausted
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.process.signal import LocalSignal
from pinecall.tenancy.codes import CLAIMED, ISSUED, LIVE_PER_AGENT, TRIES_PER_CALL, Codes
from tests.conftest import postgres
from tests.tenancy.test_agents import AGENT, agent_log


@postgres
async def test_a_code_is_four_digits_written_on_the_agents_log(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    assert len(issued.code) == 4
    assert issued.code.isdigit()
    (entry,) = await agent_log(store)
    assert (entry.type, entry.data["code"]) == (ISSUED, issued.code)


@postgres
async def test_no_two_live_codes_of_one_agent_are_alike_and_fifty_is_the_most(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = [await codes.issue("sandbox", AGENT, 60, "public") for _ in range(LIVE_PER_AGENT)]
    assert len({issued_one.code for issued_one in issued}) == LIVE_PER_AGENT
    with pytest.raises(QuotaExhausted, match="50 codes waiting"):
        await codes.issue("sandbox", AGENT, 60, "public")


@postgres
async def test_a_code_stands_waiting_then_claimed_by_one_call_and_never_by_a_second(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    waiting = await codes.status_of("sandbox", AGENT, issued.code)
    assert waiting is not None
    assert waiting.claimed is None
    claimed = await codes.claim("sandbox", AGENT, issued.code, "call_1")
    assert claimed is not None
    assert claimed.claimed == "call_1"
    assert await codes.claim("sandbox", AGENT, issued.code, "call_2") is None


# Past its three, a call's fourth code is refused like one nobody issued, though it is live.
@postgres
async def test_a_call_keys_three_codes_and_its_fourth_ties_it_to_no_page(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    nobodys = "0000" if issued.code != "0000" else "0001"
    for _ in range(TRIES_PER_CALL):
        assert await codes.claim("sandbox", AGENT, nobodys, "call_1") is None
    assert await codes.claim("sandbox", AGENT, issued.code, "call_1") is None
    left = await codes.status_of("sandbox", AGENT, issued.code)
    assert left is not None
    assert left.claimed is None, "still the page's, for another call"
    claimed = await codes.claim("sandbox", AGENT, issued.code, "call_2")
    assert claimed is not None
    assert claimed.claimed == "call_2"


@postgres
async def test_a_code_nobody_issued_or_of_the_other_world_is_nothing(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    nobodys = "0000" if issued.code != "0000" else "0001"
    assert await codes.status_of("sandbox", AGENT, nobodys) is None
    assert await codes.status_of("production", AGENT, issued.code) is None
    assert await codes.claim("production", AGENT, issued.code, "call_1") is None


@postgres
async def test_an_expired_code_stands_once_as_it_was_then_is_closed_with_no_call(
    store: Store,
) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 0, "public")
    assert await codes.status_of("sandbox", AGENT, issued.code) == issued
    assert await codes.status_of("sandbox", AGENT, issued.code) is None
    closed = [entry.data["call"] for entry in await agent_log(store) if entry.type == CLAIMED]
    assert closed == [None]


@postgres
async def test_a_page_that_waited_its_while_is_answered_as_the_code_stands(store: Store) -> None:
    codes = Codes(Logs(store))
    issued = await codes.issue("sandbox", AGENT, 60, "public")
    assert (await codes.waited(issued, 0.01)).claimed is None


@postgres
async def test_a_code_issued_on_one_gateway_is_claimed_on_another_and_wakes_the_page(
    store: Store,
) -> None:
    signal = LocalSignal()
    first, second = Codes(Logs(store, signal)), Codes(Logs(store, signal))
    issued = await first.issue("sandbox", AGENT, 60, "tenant")
    waiting = asyncio.create_task(first.waited(issued, 5))
    await asyncio.sleep(0.05)
    claimed = await second.claim("sandbox", AGENT, issued.code, "call_1")
    assert claimed is not None
    woken = await asyncio.wait_for(waiting, 1)
    assert (woken.claimed, woken.log) == ("call_1", "tenant")
    still = await second.status_of("sandbox", AGENT, issued.code)
    assert still is not None
    assert still.claimed == "call_1"
