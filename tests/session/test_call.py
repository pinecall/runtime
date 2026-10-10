"""A call's state and its writer: entries in the order they happened, whatever the log refuses."""

import asyncio
import gc
from dataclasses import dataclass, field

import pytest

from pinecall.domain.agent import AgentConfig, ToolSpec, Voice
from pinecall.domain.errors import Conflict, NotAvailable
from pinecall.domain.names import JsonObject
from pinecall.log.logs import Log
from pinecall.log.store import Store
from pinecall.session.call import (
    MOST_A_BATCH,
    MOST_QUEUED,
    Call,
    Writing,
    changed_by,
    with_app_fields,
)
from pinecall.wire.commands import SessionConfigure, ToolsSet
from pinecall.wire.events import Custom, UserTranscript
from pinecall.wire.frames import Entry
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import Supervisor, VoiceConfig
from pinecall.wire.parts import ToolSpec as WiredTool
from pinecall.wire.rest.calls import BatchedEntry
from tests.conftest import postgres
from tests.session.conftest import Box, context_of

A_TOOL = ToolSpec("book", "Book.", {"type": "object"})


def _custom(name: str) -> Custom:
    return Custom(name=name, data={})


@postgres
async def test_entries_queued_from_sync_callbacks_reach_the_log_in_that_order(
    box: Box, store: Store, call: str
) -> None:
    writing = Writing(box.log.append_many, call)
    writing.open()
    for name in ("uno", "dos", "tres"):
        writing.write("custom", _custom(name))
    await writing.close(5)
    assert [entry.data["name"] for entry in await store.whole(call)] == ["uno", "dos", "tres"]


@postgres
async def test_an_entry_waited_for_comes_back_with_the_seq_the_log_gave_it(
    box: Box, call: str
) -> None:
    writing = Writing(box.log.append_many, call)
    writing.open()
    entry = await writing.write("custom", _custom("uno"))
    await writing.close(5)
    assert entry.seq > 0


@dataclass
class Batches:
    """The call's real log, the size of each batch it was sent, and a gate on the first."""

    log: Log
    sizes: list[int] = field(default_factory=list[int])
    first_out: asyncio.Event = field(default_factory=asyncio.Event)
    let_go: asyncio.Event = field(default_factory=asyncio.Event)

    async def append_many(self, entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        """Count the batch, hold the first until let go, then write it on the log."""
        self.sizes.append(len(entries))
        if len(self.sizes) == 1:
            self.first_out.set()
            await self.let_go.wait()
        return await self.log.append_many(entries, after=after)


@postgres
async def test_what_is_queued_while_a_batch_is_out_goes_in_the_next_one_in_order(
    box: Box, store: Store, call: str
) -> None:
    batches = Batches(box.log)
    writing = Writing(batches.append_many, call)
    writing.open()
    written = [writing.write("custom", _custom("uno"))]
    await batches.first_out.wait()
    written += [writing.write("custom", _custom(name)) for name in ("dos", "tres", "cuatro")]
    batches.let_go.set()
    await writing.close(5)
    assert batches.sizes == [1, 3]
    assert [entry.data["name"] for entry in await store.whole(call)] == [
        "uno",
        "dos",
        "tres",
        "cuatro",
    ]
    assert [future.result().seq for future in written] == [1, 2, 3, 4]
    assert writing.after == 4


@postgres
async def test_a_batch_carries_at_most_its_ceiling_and_the_rest_follow(
    box: Box, store: Store, call: str
) -> None:
    batches = Batches(box.log)
    batches.let_go.set()
    writing = Writing(batches.append_many, call)
    for n in range(MOST_A_BATCH * 2 + 3):
        writing.write("custom", _custom(str(n)))
    writing.open()
    await writing.close(5)
    assert batches.sizes == [MOST_A_BATCH, MOST_A_BATCH, 3]
    names = [entry.data["name"] for entry in await store.whole(call)]
    assert names == [str(n) for n in range(MOST_A_BATCH * 2 + 3)]


@postgres
async def test_an_entry_of_an_idle_call_goes_alone(box: Box, call: str) -> None:
    batches = Batches(box.log)
    batches.let_go.set()
    writing = Writing(batches.append_many, call)
    writing.open()
    await writing.write("custom", _custom("uno"))
    await writing.write("custom", _custom("dos"))
    await writing.close(5)
    assert batches.sizes == [1, 1]


async def test_a_refused_batch_refuses_each_of_its_entries_and_the_next_still_goes() -> None:
    kept: list[str] = []

    async def picky(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        if any(entry.data.get("name") == "no" for entry in entries):
            raise Conflict("refused")
        kept.extend(str(entry.data["name"]) for entry in entries)
        return [
            Entry(
                seq=after + place,
                ts=entry.ts,
                call="c",
                agent="a",
                type=entry.type,
                ephemeral=bool(entry.ephemeral),
                data=entry.data,
            )
            for place, entry in enumerate(entries, 1)
        ]

    writing = Writing(picky, "c")
    refused = [writing.write("custom", _custom("no")), writing.write("custom", _custom("tampoco"))]
    writing.open()
    await asyncio.gather(*refused, return_exceptions=True)
    taken = await writing.write("custom", _custom("también"))
    await writing.close(5)
    assert kept == ["también"]
    assert writing.refused == ["custom", "custom"]
    assert all(isinstance(future.exception(), Conflict) for future in refused)
    assert (taken.seq, writing.after) == (1, 1)


async def test_at_the_ceiling_an_ephemeral_entry_is_shed_and_a_durable_one_is_queued() -> None:
    async def stuck(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        del entries, after
        await asyncio.sleep(60)
        raise AssertionError

    writing = Writing(stuck, "c")
    for n in range(MOST_QUEUED):
        writing.write("custom", _custom(str(n)))
    heard = writing.write("user.transcript", UserTranscript(text="ho", final=False))
    kept = writing.write("custom", _custom("durable"))
    assert isinstance(heard.exception(), NotAvailable)
    assert writing.shed == ["user.transcript"]
    assert not kept.done()
    assert writing.queued.qsize() == MOST_QUEUED + 1
    await writing.close(0.01)


# asyncio logs an exception nobody retrieved when its future is collected: once per shed entry.
async def test_a_shed_entry_nobody_waits_for_is_not_logged_as_an_exception_never_retrieved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def stuck(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        del entries, after
        await asyncio.sleep(60)
        raise AssertionError

    writing = Writing(stuck, "c")
    for n in range(MOST_QUEUED):
        writing.write("custom", _custom(str(n)))
    writing.write("user.transcript", UserTranscript(text="ho", final=False))
    gc.collect()
    await asyncio.sleep(0)
    assert [record.getMessage() for record in caplog.records if record.name == "asyncio"] == []
    assert writing.shed == ["user.transcript"]
    await writing.close(0.01)


async def test_a_close_past_its_budget_lets_the_queue_go_rather_than_wait_for_ever() -> None:
    async def stuck(entries: list[BatchedEntry], *, after: int) -> list[Entry]:
        del entries, after
        await asyncio.sleep(60)
        raise AssertionError

    writing = Writing(stuck, "c")
    writing.open()
    writing.write("custom", _custom("uno"))
    writing.write("custom", _custom("dos"))
    await asyncio.wait_for(writing.close(0.05), 1)
    assert writing.draining is None
    assert writing.refused == ["custom", "custom"]


@postgres
async def test_speech_ids_are_numbered_from_one(box: Box, call: str) -> None:
    ongoing = Call(context_of(call), AgentConfig(slug="a"), box.platform())
    assert (ongoing.speech(), ongoing.speech()) == ("sp_1", "sp_2")


@postgres
async def test_the_model_keeps_quiet_while_a_person_has_the_line_or_is_being_waited_for(
    box: Box, call: str
) -> None:
    ongoing = Call(context_of(call), AgentConfig(slug="a"), box.platform())
    assert not ongoing.a_person_has_the_line
    ongoing.waiting_for_a_person = True
    assert ongoing.a_person_has_the_line
    ongoing.waiting_for_a_person = False
    ongoing.taken_by = Supervisor(id="mem_1")
    assert ongoing.a_person_has_the_line


@postgres
async def test_everything_declared_is_open_until_a_tools_set_narrows_it(
    box: Box, store: Store, call: str
) -> None:
    ongoing = Call(context_of(call), AgentConfig(slug="a", tools=(A_TOOL,)), box.platform())
    ongoing.writing.open()
    assert ongoing.open_tools == {"book"}
    undeclared = WiredTool(name="ghost", description="d", parameters={"type": "object"})
    await ongoing.set_tools(ToolsSet(tools=[undeclared]))
    assert ongoing.open_tools == frozenset()
    await ongoing.writing.close(5)
    assert [entry.data for entry in await store.whole(call)] == [{"visible": []}]


@postgres
async def test_an_event_from_a_widget_names_the_seat_it_came_from(
    box: Box, store: Store, call: str
) -> None:
    config = AgentConfig(slug="a", events={"clicked": frozenset({"participant"})})
    ongoing = Call(context_of(call), config, box.platform())
    ongoing.writing.open()
    await ongoing.receives("clicked", {"x": 1}, source="participant", identity="visitor_1")
    await ongoing.writing.close(5)
    (received,) = await store.whole(call)
    assert received.data == {
        "name": "clicked",
        "data": {"x": 1},
        "source": "participant",
        "identity": "visitor_1",
    }


def test_a_declaration_changes_only_the_fields_the_app_sent() -> None:
    current = AgentConfig(slug="a", language="es-ES", tools=(A_TOOL,))
    parameters: JsonObject = {"type": "object", "properties": {"phone": {"type": "string"}}}
    wired = WiredTool(name="cancel", description="d", parameters=parameters, pii=["phone"])
    data = Declared(tools=[wired], uses_knowledge=True)
    patched = with_app_fields(current, data)
    assert patched.language == "es-ES"
    assert patched.uses_knowledge
    assert [(tool.name, tool.pii) for tool in patched.tools] == [("cancel", frozenset({"phone"}))]
    assert patched.tools[0].timeout_s == A_TOOL.timeout_s


def test_what_the_class_declares_of_its_environment_is_taken_and_fixed() -> None:
    current = AgentConfig(slug="a")
    voice = VoiceConfig(provider="cartesia", voice_id="v")
    patched = with_app_fields(current, Declared(voice=voice))
    assert patched.voice == Voice("cartesia", None, "v")
    assert patched.fixed == frozenset({"voice"})


def test_the_fields_a_declaration_changed_are_named_sorted() -> None:
    assert changed_by(Declared(view=None, language="en")) == ["language", "view"]


@postgres
async def test_a_configure_declares_for_this_call_and_sets_its_state(
    box: Box, store: Store, call: str
) -> None:
    ongoing = Call(context_of(call), AgentConfig(slug="a"), box.platform())
    ongoing.writing.open()
    await ongoing.configure(
        SessionConfigure(config=Declared(language="en-US"), state={"step": "greeted"})
    )
    await ongoing.writing.close(5)
    assert ongoing.config.language == "en-US"
    written = await store.whole(call)
    assert [(entry.type, entry.data.get("changed")) for entry in written] == [
        ("agent.configured", ["language"]),
        ("state.changed", ["step"]),
    ]
