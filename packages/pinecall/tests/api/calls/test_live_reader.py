"""A reader hears a call as it happens, and sees only what its scope allows it to see."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pinecall.api.calls.log_sink import Project, projection_for
from pinecall.live.registry import Registry
from pinecall.log.entry import Entry
from pinecall.log.projection import MASK
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.tokens.scopes import KEY_PROJECTION, PROJECTION_OF, Reader
from tests.api.calls.test_state import THE_CALL, declare_the_golden_agent, load_the_golden

pytestmark = pytest.mark.unit

A_GUEST = Reader(projection=PROJECTION_OF["participate"], call=THE_CALL)
A_TENANT = Reader(projection=KEY_PROJECTION)

# A reader that never hears what it is waiting for must fail the test, not hang the suite.
A_MOMENT = 3.0

# Words that would mean the guest's stream carried the tenant's implementation or the trunk's
# headers, held to the same list log/test_projection.py holds the projection to.
NEVER_IN_PUBLIC = ("sip.", "attributes", "call_id", "audience", "cause", "toolu_")


# The SSE route is `sse(logs.reading(call).stream(...), project, reader)` and nothing else, so what
# is worth proving is the two halves it is made of: that a stream a READER opened carries what a
# WRITER appends afterwards, and that the projection at the sink is the one the scope names.
# Driving that through starlette's TestClient would mean reading a body that never ends on the very
# thread the writer needs, which deadlocks; the seam is the same seam in one event loop.
async def heard(
    stream: AsyncIterator[Entry], until: str, project: Project, reader: Reader
) -> list[dict[str, Any]]:
    """Everything the reader is handed, up to and including the entry type it is waiting for."""
    said: list[dict[str, Any]] = []

    async def drain() -> None:
        async for entry in stream:
            kept = project(entry, reader)
            if kept is not None:
                said.append(kept)
            if entry.type == until:
                return

    await asyncio.wait_for(drain(), timeout=A_MOMENT)
    return said


async def test_a_reader_hears_an_entry_appended_after_it_opened_the_stream(
    logs: Logs, registry: Registry
) -> None:
    """The whole point of the live half: one stream, opened first, and the turns land on it."""
    log = logs.writing(THE_CALL, "clara")
    await log.append("call.started", {"channel": "web", "direction": "inbound"})
    reading = logs.reading(THE_CALL).stream()

    async def say_two_turns() -> None:
        # After the reader opened, and with no reconnect between them.
        await asyncio.sleep(0.05)
        await log.append("turn.user", {"speech_id": "sp_1", "text": "hola"})
        await log.append("turn.agent", {"speech_id": "sp_1", "text": "Hola, soy Clara."})

    writing = asyncio.ensure_future(say_two_turns())
    said = await heard(reading, "turn.agent", projection_for(registry), A_TENANT)
    await writing

    types = [entry["type"] for entry in said]
    assert "turn.user" in types, f"the reader never heard the caller's turn: {types}"
    assert "turn.agent" in types, f"the reader never heard the agent's turn: {types}"
    # The backlog came first and the live half followed it on the very same stream.
    assert types.index("call.started") < types.index("turn.user")


async def test_a_participate_token_reads_its_own_call_and_none_of_the_tenants_fields(
    store: MemoryStore, logs: Logs, registry: Registry
) -> None:
    """The guest's stream, over the golden: the public whitelist, applied here at the sink."""
    await load_the_golden(store)
    await declare_the_golden_agent(registry)
    said = await heard(
        logs.reading(THE_CALL).stream(), "call.summary", projection_for(registry), A_GUEST
    )

    assert said, "the guest's stream carried nothing at all"
    whole = repr(said)
    for leak in NEVER_IN_PUBLIC:
        assert leak not in whole, f"the guest's stream carried {leak!r}"
    assert MASK not in whole, "a masked field is the tenant's view; a guest never sees the key"
    for entry in said:
        assert "agent" not in entry and "call" not in entry, "the envelope named the tenant"
    changes = [entry for entry in said if entry["type"] == "state.changed"]
    assert changes, "the golden changes state and the guest is entitled to the declared fields"
    for change in changes:
        assert set(change["data"]["state"]) == {"slots", "booking"}


async def test_the_tenant_reading_the_same_stream_sees_its_pii_masked_and_nothing_dropped(
    store: MemoryStore, logs: Logs, registry: Registry
) -> None:
    """One stream, two readers, two answers — and the difference is only who is asking."""
    await load_the_golden(store)
    await declare_the_golden_agent(registry)
    said = await heard(
        logs.reading(THE_CALL).stream(), "call.summary", projection_for(registry), A_TENANT
    )

    changes = [entry for entry in said if entry["type"] == "state.changed"]
    assert changes and all(change["data"]["state"]["patient"] == MASK for change in changes)
    assert all(entry["call"] == THE_CALL for entry in said), "the tenant's envelope names the call"


def test_an_entry_the_projection_drops_never_reaches_the_reader(registry: Registry) -> None:
    """None is the projection saying the entry does not leave at all, and the sink honours it."""
    entry = Entry(
        seq=1,
        ts=time.time(),
        call="CA_1",
        agent="clara",
        type="tool.call",
        ephemeral=False,
        data={},
    )
    project = projection_for(registry)
    assert project(entry, A_GUEST) is None
    assert project(entry, A_TENANT) is not None


# Found by running the gateway, not by a test: a console held the agent's stream open, the app
# connected afterwards, and agent.registered never arrived — the reader had been handed a log with
# a fanout of its own. The order below is the NORMAL order, and it is the one that was silent.
async def test_a_reader_that_opened_before_the_writer_existed_still_hears_it(
    logs: Logs, registry: Registry
) -> None:
    """The fanout is shared by id, whichever side asked for it first."""
    reading = logs.reading_agent("clara").stream()

    async def register_afterwards() -> None:
        await asyncio.sleep(0.05)
        await logs.writing_agent("clara").append("agent.registered", {"routes": [], "sdk": "x"})

    writing = asyncio.ensure_future(register_afterwards())
    said = await heard(reading, "agent.registered", projection_for(registry), A_TENANT)
    await writing
    assert [entry["type"] for entry in said][-1] == "agent.registered"


async def test_a_reader_that_opened_before_the_call_started_still_hears_it(
    logs: Logs, registry: Registry
) -> None:
    """The same rule for a call: a supervisor may attach before the first entry is written."""
    reading = logs.reading(THE_CALL).stream()

    async def start_afterwards() -> None:
        await asyncio.sleep(0.05)
        await logs.writing(THE_CALL, "clara").append("call.started", {"channel": "web"})

    writing = asyncio.ensure_future(start_afterwards())
    said = await heard(reading, "call.started", projection_for(registry), A_TENANT)
    await writing
    assert said and said[-1]["type"] == "call.started"


def test_an_id_nobody_reads_or_writes_any_more_is_not_kept(logs: Logs) -> None:
    """A scan of made-up ids leaves nothing behind: the fanout of an unread id is dropped."""
    logs.reading_agent("nobody-1")
    logs.reading_agent("nobody-2")
    assert "nobody-1" not in logs._agent_fanouts  # pyright: ignore[reportPrivateUsage]
    assert "nobody-2" in logs._agent_fanouts  # pyright: ignore[reportPrivateUsage]
