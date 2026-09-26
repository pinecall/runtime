"""The feeds a gateway taps off its logs: an org's own, and the box's, which is every org's."""

import asyncio
from typing import override

import pytest

from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types.json import JsonObject

pytestmark = pytest.mark.unit

RINGING: JsonObject = {"channel": "phone", "from": "+34600000000", "to": "+34900000000"}


async def test_every_orgs_floor_reaches_the_box_and_a_turn_does_not() -> None:
    logs = Logs(MemoryStore())
    box = logs.box().subscribe()
    await logs.owned("CA_a", "clinica-norte", "org_a")
    await logs.owned("CA_b", "tienda-sur", "org_b")
    await logs.writing("CA_a", "clinica-norte").append("call.ringing", dict(RINGING))
    await logs.writing("CA_a", "clinica-norte").append(
        "turn.user", {"text": "hola", "speech_id": "u1"}
    )
    await logs.writing("CA_b", "tienda-sur").append("call.ringing", dict(RINGING))
    heard = [await asyncio.wait_for(box.__anext__(), 1) for _ in range(2)]
    assert [(entry.call, entry.type) for entry in heard] == [
        ("CA_a", "call.ringing"),
        ("CA_b", "call.ringing"),
    ]
    box.close()


async def test_a_log_nobody_owns_never_reaches_the_box() -> None:
    logs = Logs(MemoryStore())
    box = logs.box().subscribe()
    await logs.writing("CA_nobodys", "clinica-norte").append("call.ringing", dict(RINGING))
    await logs.owned("CA_a", "clinica-norte", "org_a")
    await logs.writing("CA_a", "clinica-norte").append("call.ringing", dict(RINGING))
    heard = await asyncio.wait_for(box.__anext__(), 1)
    assert heard.call == "CA_a"
    box.close()


async def test_the_orgs_own_feed_still_hears_its_own_while_the_box_listens() -> None:
    logs = Logs(MemoryStore())
    box = logs.box().subscribe()
    feed = logs.feed("org_a").subscribe()
    await logs.owned("CA_a", "clinica-norte", "org_a")
    await logs.writing("CA_a", "clinica-norte").append("call.ringing", dict(RINGING))
    assert (await asyncio.wait_for(feed.__anext__(), 1)).call == "CA_a"
    assert (await asyncio.wait_for(box.__anext__(), 1)).call == "CA_a"
    feed.close()
    box.close()


class _Counting(MemoryStore):
    """The store, counting how often it was asked whose a log is."""

    def __init__(self) -> None:
        super().__init__()
        self.asked = 0

    @override
    async def owner(self, call: str | None, agent: str) -> str | None:
        self.asked += 1
        return await super().owner(call, agent)


async def test_whose_a_log_is_is_asked_of_the_store_once_and_not_per_entry() -> None:
    """A claim never moves, and the question sat on the append path of every call.started."""
    store = _Counting()
    logs = Logs(store)
    await logs.owned("CA_1", "clinica", "org-1")
    log = logs.writing("CA_1", "clinica")
    for _ in range(3):
        await log.append("call.started", {})
        await log.append("call.ended", {})
    assert store.asked == 1
    logs.forget("CA_1")
    await logs.writing("CA_1", "clinica").append("call.started", {})
    assert store.asked == 2, "forgotten with the call, asked again for the next"
