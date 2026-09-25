"""The codes a page shows: issued once per agent, claimed once, closed when they expire, kept."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.orgs.codes import CLAIMED, ISSUED, LIVE_PER_AGENT, Codes, TooManyCodes
from pinecall.types import PRODUCTION, SANDBOX

pytestmark = pytest.mark.unit

AGENT = "clinica-norte"
TEN_MINUTES = 600.0


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def codes(store: MemoryStore) -> Codes:
    return Codes(Logs(store))


async def written(store: MemoryStore, type: str) -> list[dict[str, Any]]:
    """The agent's own log, kept to one type."""
    return [entry.data for entry in await store.agent_since(AGENT, after=0) if entry.type == type]


async def test_a_code_is_four_digits_written_on_the_agents_log(
    codes: Codes, store: MemoryStore
) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public")
    assert len(issued.code) == 4 and issued.code.isdigit()
    assert issued.claimed is None
    [said] = await written(store, ISSUED)
    assert said == {
        "code": issued.code,
        "env": PRODUCTION,
        "expires_at": issued.expires_at,
        "log": "public",
    }


async def test_no_two_live_codes_of_one_agent_are_alike_and_fifty_is_the_most(
    codes: Codes,
) -> None:
    issued = [await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public") for _ in range(50)]
    assert len({one.code for one in issued}) == LIVE_PER_AGENT
    with pytest.raises(TooManyCodes, match=f"agent {AGENT} already has 50 codes"):
        await codes.issue(SANDBOX, AGENT, TEN_MINUTES, "public")
    assert await codes.issue(PRODUCTION, "another-agent", TEN_MINUTES, "public")


async def test_a_code_stands_waiting_then_claimed_by_one_call_and_never_by_a_second(
    codes: Codes, store: MemoryStore
) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "tenant")
    standing = await codes.standing(PRODUCTION, AGENT, issued.code)
    assert standing is not None and standing.claimed is None

    claimed = await codes.claim(PRODUCTION, AGENT, issued.code, "call_1")
    assert claimed is not None and claimed.claimed == "call_1"
    assert await codes.claim(PRODUCTION, AGENT, issued.code, "call_2") is None
    standing = await codes.standing(PRODUCTION, AGENT, issued.code)
    assert standing is not None and standing.claimed == "call_1"
    assert await written(store, CLAIMED) == [{"code": issued.code, "call": "call_1"}]


async def test_a_code_nobody_issued_or_of_the_other_world_is_nothing(codes: Codes) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public")
    unissued = "0000" if issued.code != "0000" else "0001"
    assert await codes.standing(PRODUCTION, AGENT, unissued) is None
    assert await codes.claim(PRODUCTION, AGENT, unissued, "call_1") is None
    assert await codes.standing(SANDBOX, AGENT, issued.code) is None
    assert await codes.claim(SANDBOX, AGENT, issued.code, "call_1") is None


async def test_an_expired_code_stands_once_as_it_was_then_is_closed_with_no_call(
    codes: Codes, store: MemoryStore
) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, -1.0, "public")
    standing = await codes.standing(PRODUCTION, AGENT, issued.code)
    assert standing is not None and standing.expired(issued.expires_at)
    assert await codes.claim(PRODUCTION, AGENT, issued.code, "call_1") is None
    assert await codes.standing(PRODUCTION, AGENT, issued.code) is None
    assert await written(store, CLAIMED) == [{"code": issued.code, "call": None}]


async def test_the_page_waiting_is_answered_the_moment_a_call_claims_it(codes: Codes) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public")
    waiting = asyncio.ensure_future(codes.waited(issued, 25.0))
    await asyncio.sleep(0)
    assert not waiting.done()
    await codes.claim(PRODUCTION, AGENT, issued.code, "call_1")
    answered = await asyncio.wait_for(waiting, 1.0)
    assert answered.claimed == "call_1"


async def test_a_page_that_waited_its_while_is_answered_as_the_code_stands(codes: Codes) -> None:
    issued = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public")
    answered = await codes.waited(issued, 0.01)
    assert answered.claimed is None


async def test_a_gateway_that_starts_reads_every_open_code_back_off_the_log(
    codes: Codes, store: MemoryStore
) -> None:
    waiting = await codes.issue(PRODUCTION, AGENT, TEN_MINUTES, "public")
    taken = await codes.issue(SANDBOX, AGENT, TEN_MINUTES, "tenant")
    await codes.claim(SANDBOX, AGENT, taken.code, "call_1")
    lapsed = await codes.issue(PRODUCTION, AGENT, -1.0, "public")
    await codes.standing(PRODUCTION, AGENT, lapsed.code)

    after = Codes(Logs(store))
    await after.loaded(store)
    assert await after.standing(PRODUCTION, AGENT, waiting.code) == waiting
    again = await after.standing(SANDBOX, AGENT, taken.code)
    assert again is not None and again.claimed == "call_1" and again.log == "tenant"
    assert await after.standing(PRODUCTION, AGENT, lapsed.code) is None
