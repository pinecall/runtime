"""A call's state and its writer: entries in the order they happened, whatever the log refuses."""

import asyncio

from pinecall.domain.agent import AgentConfig, ToolSpec
from pinecall.domain.names import JsonObject
from pinecall.log.store import Store
from pinecall.session.call import Call, Writing, changed_by, with_app_fields
from pinecall.wire.commands import SessionConfigure, ToolsSet
from pinecall.wire.events import Custom
from pinecall.wire.frames import Entry
from pinecall.wire.parts import AgentConfig as Declared
from pinecall.wire.parts import Supervisor, VoiceConfig
from pinecall.wire.parts import ToolSpec as WiredTool
from tests.conftest import postgres
from tests.session.conftest import Box, context_of

A_TOOL = ToolSpec("book", "Book.", {"type": "object"})


def _custom(name: str) -> Custom:
    return Custom(name=name, data={})


@postgres
async def test_entries_queued_from_sync_callbacks_reach_the_log_in_that_order(
    box: Box, store: Store, call: str
) -> None:
    writing = Writing(box.log.append, call)
    writing.open()
    for name in ("uno", "dos", "tres"):
        writing.write("custom", _custom(name))
    await writing.close(5)
    assert [entry.data["name"] for entry in await store.whole(call)] == ["uno", "dos", "tres"]


@postgres
async def test_an_entry_waited_for_comes_back_with_the_seq_the_log_gave_it(
    box: Box, call: str
) -> None:
    writing = Writing(box.log.append, call)
    writing.open()
    entry = await writing.write("custom", _custom("uno"))
    await writing.close(5)
    assert entry.seq > 0


async def test_a_log_that_refuses_one_entry_is_remembered_and_the_rest_still_go() -> None:
    kept: list[str] = []

    async def picky(kind: str, data: JsonObject, *, ephemeral: bool | None = None) -> Entry:
        if data.get("name") == "no":
            raise ValueError("refused")
        kept.append(str(data["name"]))
        return Entry(
            seq=len(kept),
            ts=0,
            call="c",
            agent="a",
            type=kind,
            ephemeral=bool(ephemeral),
            data=data,
        )

    writing = Writing(picky, "c")
    writing.open()
    writing.write("custom", _custom("sí"))
    writing.write("custom", _custom("no"))
    writing.write("custom", _custom("también"))
    await writing.close(5)
    assert kept == ["sí", "también"]
    assert writing.refused == ["custom"]


async def test_a_close_past_its_budget_lets_the_queue_go_rather_than_wait_for_ever() -> None:
    async def stuck(kind: str, data: JsonObject, *, ephemeral: bool | None = None) -> Entry:
        del data, ephemeral
        await asyncio.sleep(60)
        raise AssertionError(kind)

    writing = Writing(stuck, "c")
    writing.open()
    writing.write("custom", _custom("uno"))
    writing.write("custom", _custom("dos"))
    await asyncio.wait_for(writing.close(0.05), 1)
    assert writing.draining is None


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


def test_what_the_org_sets_per_world_is_never_taken_from_a_declaration() -> None:
    current = AgentConfig(slug="a")
    patched = with_app_fields(current, Declared(voice=VoiceConfig(provider="acme", voice_id="v")))
    assert patched == current


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
