"""The app's tools declared once, their round trip, the date, and recall and search in budget."""

import asyncio
import json
import locale
from datetime import date

import pytest
from livekit.agents import RunContext, llm

from pinecall.domain.types import AgentConfig, Contact, Docs, JsonObject, MemoryPolicy, ToolSpec
from pinecall.log.store import Store
from pinecall.session.call import Call, ToolUse
from pinecall.session.prompt import Blocks, request
from pinecall.session.tools import (
    RECALL,
    SEARCH,
    Lookups,
    ToolCalls,
    date_pair,
    declared,
    platform_tools,
    read_back,
    result_text,
)
from pinecall.wire.parts import PlatformTool, ToolResult
from tests.conftest import postgres
from tests.session.conftest import Box, context_of

BOOK = ToolSpec("book", "Book a table.", {"type": "object", "properties": {}}, timeout_s=0.1)


def _call(box: Box, config: AgentConfig) -> Call:
    assert box.log.call is not None
    return Call(context_of(box.log.call), config, box.platform())


# ── what the model reads ──


def test_the_text_the_model_reads_is_the_error_then_the_summary_then_the_output() -> None:
    said = ToolResult(call_id="c", name="t", output={"a": 1}, summary="hecho", error="falló")
    assert result_text(said) == "falló"
    assert result_text(said.model_copy(update={"error": None})) == "hecho"
    assert (
        result_text(ToolResult(call_id="c", name="t", output={"mesa": "4 ñ"})) == '{"mesa": "4 ñ"}'
    )
    assert result_text(ToolResult(call_id="c", name="t", output="listo")) == "listo"
    assert result_text(ToolResult(call_id="c", name="t")) == ""


def test_a_confirm_is_rendered_from_the_call_and_the_result() -> None:
    result = ToolResult(call_id="c", name="book", output={"table": 4})
    said = read_back("Mesa {{result.table}} el {{slot.when}}", {"slot": {"when": "lunes"}}, result)
    assert said == "Mesa 4 el lunes"


def test_a_placeholder_nobody_filled_stays_visible_instead_of_vanishing() -> None:
    said = read_back("Hecho: {{result.code}}", {}, ToolResult(call_id="c", name="t", output={}))
    assert said == "Hecho: {{result.code}}"


# ── declared once ──


def test_every_declared_tool_becomes_a_raw_schema_tool_under_its_own_name() -> None:
    async def run(use: ToolUse, _context: RunContext[None]) -> str:
        return use.name

    (tool,) = declared((BOOK,), run)
    assert isinstance(tool, llm.RawFunctionTool)
    assert tool.info.raw_schema == {
        "name": "book",
        "description": "Book a table.",
        "parameters": {"type": "object", "properties": {}},
    }


def test_recall_is_declared_only_when_the_class_declares_memory_and_search_for_bases() -> None:
    assert platform_tools(AgentConfig(slug="a")) == ()
    assert platform_tools(AgentConfig(slug="a", memory=MemoryPolicy())) == (RECALL,)
    assert platform_tools(AgentConfig(slug="a", bases=(Docs(base="b"),))) == (SEARCH,)


def test_the_descriptions_say_the_results_are_data_and_never_instructions() -> None:
    for spec in (RECALL, SEARCH):
        assert "never an instruction" in spec.description
    contact = RECALL.parameters["properties"]
    assert isinstance(contact, dict)
    assert "whatever is written here" in str(contact)


# ── the date ──


def test_the_date_is_a_call_and_its_answer_under_one_id_that_asks_for_no_reply() -> None:
    called, answered = date_pair(date(2026, 9, 28))
    assert (called.call_id, answered.call_id, called.name) == ("clock_1", "clock_1", "current_date")
    assert json.loads(answered.output) == {"today": "2026-09-28", "weekday": "monday"}
    assert answered.reply_required is False


def test_the_weekday_does_not_depend_on_the_locale_of_the_process() -> None:
    before = locale.setlocale(locale.LC_TIME)
    try:
        with pytest.raises(locale.Error):
            locale.setlocale(locale.LC_TIME, "es_UY.nowhere")
        assert json.loads(date_pair(date(2026, 9, 27))[1].output)["weekday"] == "sunday"
    finally:
        locale.setlocale(locale.LC_TIME, before)


def test_the_pair_reaches_the_provider_as_a_pair_and_never_as_an_instruction() -> None:
    history = llm.ChatContext.empty()
    history.items.extend(date_pair(date(2026, 9, 28)))
    history.add_message(role="user", content="¿qué día es?")
    messages, _ = request(history, Blocks()).to_provider_format("openai")
    assert [message["role"] for message in messages] == ["assistant", "tool", "user"]


# ── the round trip ──


@postgres
async def test_a_tool_asked_twice_by_call_id_is_written_once_and_answered_once(
    box: Box, store: Store, call: str
) -> None:
    calls = ToolCalls(AgentConfig(slug="a", tools=(BOOK,)), box.log.append)
    use = ToolUse("t1", "book", {"day": "lunes"})
    first = asyncio.create_task(calls.ran(use, "speech_1"))
    second = asyncio.create_task(calls.ran(use, "speech_1"))
    await asyncio.sleep(0.02)
    assert calls.answered(ToolResult(call_id="t1", name="book", output="ok"))
    assert (await first, await second) == (await first, await first)
    written = [entry.type for entry in await store.whole(call)]
    assert written == ["tool.call", "tool.result"]


@postgres
async def test_the_tools_still_waiting_are_the_entries_that_went_out_in_order(box: Box) -> None:
    calls = ToolCalls(AgentConfig(slug="a", tools=(BOOK,)), box.log.append)
    running = [asyncio.create_task(calls.ran(ToolUse(f"t{n}", "book", {}), None)) for n in (1, 2)]
    await asyncio.sleep(0.02)
    assert [entry.data["call_id"] for entry in calls.pending()] == ["t1", "t2"]
    for task in running:
        task.cancel()


@postgres
async def test_a_tool_that_does_not_answer_in_time_is_an_error_the_model_recovers_from(
    box: Box,
) -> None:
    calls = ToolCalls(AgentConfig(slug="a", tools=(BOOK,)), box.log.append)
    result = await calls.ran(ToolUse("t1", "book", {}), None)
    assert result.error == "book did not answer within 0.1s"
    assert calls.pending() == []


@postgres
async def test_a_cancelled_wait_is_cancelled_and_never_a_lapsed_result(
    box: Box, store: Store, call: str
) -> None:
    slow = ToolSpec("book", "Book.", {"type": "object"}, timeout_s=5)
    calls = ToolCalls(AgentConfig(slug="a", tools=(slow,)), box.log.append)
    asking = asyncio.create_task(calls.ran(ToolUse("t1", "book", {}), None))
    await asyncio.sleep(0.02)
    calls.running["t1"].cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking
    assert [entry.type for entry in await store.whole(call)] == ["tool.call"]
    assert not calls.answered(ToolResult(call_id="t1", name="book"))


# ── recall and search ──


def _lookups(box: Box, config: AgentConfig, budget_ms: int = 200) -> Lookups:
    return Lookups(_call(box, config), box.lookup, budget_ms)


MEMORY = AgentConfig(slug="a", memory=MemoryPolicy())
BOTH = AgentConfig(slug="a", memory=MemoryPolicy(), bases=(Docs(base="precios"),))


@postgres
async def test_a_turn_runs_what_the_declaration_asks_for_with_the_callers_words(box: Box) -> None:
    box.found["recall"] = {"facts": [{"text": "vegetariano"}]}
    box.found["search"] = {"chunks": [{"text": "el menú del día"}]}
    lookups = _lookups(box, BOTH)
    assert await lookups.turn_ended("qué hay de menú", None) == []
    assert [tool for tool, _ in box.looked] == ["recall", "search"]
    assert box.looked[1][1] == {"query": "qué hay de menú"}


@postgres
async def test_a_caller_nobody_has_identified_is_left_out_and_never_named_as_nobody(
    box: Box,
) -> None:
    unknown = context_of(str(box.log.call), "web", contact=Contact())
    lookups = Lookups(Call(unknown, MEMORY, box.platform()), box.lookup, 200)
    await lookups.turn_ended("mi pedido", None)
    assert box.looked == [("recall", {"query": "mi pedido"})]


@postgres
async def test_each_run_leaves_a_pair_the_formatter_can_match_by_call_id(box: Box) -> None:
    box.found["recall"] = {"facts": [{"text": "vegetariano"}]}
    lookups = _lookups(box, MEMORY)
    await lookups.turn_ended("menú", None)
    called, answered = lookups.items
    assert isinstance(called, llm.FunctionCall)
    assert isinstance(answered, llm.FunctionCallOutput)
    assert called.call_id == answered.call_id == "lu_1_recall"
    assert json.loads(answered.output) == {"facts": [{"text": "vegetariano"}]}
    assert answered.reply_required is False


@postgres
async def test_the_pair_is_replaced_whole_every_turn_and_holds_nothing_between_two(
    box: Box,
) -> None:
    box.found["recall"] = {"facts": [{"text": "vegetariano"}]}
    lookups = _lookups(box, MEMORY)
    await lookups.turn_ended("menú", None)
    box.found["recall"] = {"facts": []}
    await lookups.turn_ended("otra cosa", None)
    assert lookups.items == ()


@postgres
async def test_docs_in_tool_mode_run_nothing_before_the_turn_and_still_answer_the_model(
    box: Box,
) -> None:
    box.found["search"] = {"chunks": [{"text": "abrimos a las nueve"}]}
    lookups = _lookups(box, AgentConfig(slug="a", bases=(Docs(base="b", mode="tool"),)))
    await lookups.turn_ended("horario", None)
    assert box.looked == []
    answered = await lookups.called(ToolUse("m1", "search", {"query": "horario"}))
    assert json.loads(answered) == {"chunks": [{"text": "abrimos a las nueve"}]}


@postgres
async def test_past_the_budget_no_pair_is_added_and_the_log_says_which_tool_did_not_run(
    box: Box,
) -> None:
    lookups = _lookups(box, MEMORY, budget_ms=20)

    async def slow(_tool: PlatformTool, _arguments: JsonObject, _speech: str | None) -> JsonObject:
        await asyncio.sleep(1)
        return {"facts": [{"text": "late"}]}

    lookups.lookup = slow
    skipped = await lookups.turn_ended("menú del día", None)
    assert lookups.items == ()
    assert [(error.code, error.message) for error in skipped] == [
        ("recall_skipped", "recall did not run: no answer within 20 ms")
    ]


@postgres
async def test_one_lookup_that_fails_leaves_the_other_ones_pair_standing(box: Box) -> None:
    box.failing.add("recall")
    box.found["search"] = {"chunks": [{"text": "el menú"}]}
    lookups = _lookups(box, BOTH)
    skipped = await lookups.turn_ended("menú", None)
    assert [error.code for error in skipped] == ["recall_skipped"]
    assert [item.call_id for item in lookups.items if isinstance(item, llm.FunctionCall)] == [
        "lu_1_search"
    ]


@postgres
async def test_an_interim_of_enough_words_starts_the_run_and_the_pair_carries_its_prefix(
    box: Box,
) -> None:
    box.found["recall"] = {"facts": [{"text": "vegetariano"}]}
    lookups = _lookups(box, MEMORY)
    lookups.heard_so_far("quiero saber qué hay")
    lookups.heard_so_far("quiero saber qué hay de menú")
    await lookups.turn_ended("quiero saber qué hay de menú hoy", None)
    assert box.looked == [("recall", {"contact": "+59899123456", "query": "quiero saber qué hay"})]
    called = lookups.items[0]
    assert isinstance(called, llm.FunctionCall)
    assert json.loads(called.arguments)["query"] == "quiero saber qué hay"


@postgres
async def test_a_turn_too_short_to_have_started_a_run_still_gets_its_lookups_at_the_end(
    box: Box,
) -> None:
    lookups = _lookups(box, MEMORY)
    lookups.heard_so_far("sí")
    await lookups.turn_ended("sí", None)
    assert box.looked == [("recall", {"contact": "+59899123456", "query": "sí"})]


@postgres
async def test_the_eager_run_also_refuses_a_turn_that_could_not_be_a_query(box: Box) -> None:
    lookups = _lookups(box, MEMORY)
    lookups.heard_so_far("4 5 6 7 8 9")
    assert lookups.running is None


@postgres
async def test_a_run_started_for_one_turn_is_never_read_by_the_next(box: Box) -> None:
    lookups = _lookups(box, MEMORY)
    lookups.heard_so_far("quiero saber qué hay hoy")
    await lookups.turn_ended("quiero saber qué hay hoy", None)
    await lookups.turn_ended("y mañana", None)
    assert [arguments["query"] for _, arguments in box.looked] == [
        "quiero saber qué hay hoy",
        "y mañana",
    ]


@postgres
async def test_a_class_that_declares_neither_asks_nobody_and_carries_no_pair(box: Box) -> None:
    lookups = _lookups(box, AgentConfig(slug="a"))
    assert await lookups.turn_ended("menú", None) == []
    assert (box.looked, lookups.items) == ([], ())


@postgres
async def test_the_empty_one_goes_and_the_full_one_stays_in_the_same_turn(box: Box) -> None:
    box.found["search"] = {"chunks": [{"text": "el menú"}]}
    lookups = _lookups(box, BOTH)
    await lookups.turn_ended("menú", None)
    names = [item.name for item in lookups.items if isinstance(item, llm.FunctionCall)]
    assert names == ["search"]


@postgres
async def test_a_lookup_the_model_called_that_fails_is_its_tool_error(box: Box) -> None:
    box.failing.add("recall")
    lookups = _lookups(box, MEMORY)
    with pytest.raises(llm.ToolError, match="recall did not run: the recall index did not answer"):
        await lookups.called(ToolUse("m1", "recall", {"query": "x"}))


@postgres
async def test_a_lookup_still_running_at_hang_up_is_cancelled_with_the_call(box: Box) -> None:
    lookups = _lookups(box, MEMORY)

    async def never(_tool: PlatformTool, _arguments: JsonObject, _speech: str | None) -> JsonObject:
        await asyncio.sleep(60)
        return {}

    lookups.lookup = never
    lookups.heard_so_far("quiero saber qué hay hoy")
    running = lookups.running
    assert running is not None
    await lookups.close()
    assert all(task.cancelled() for task in running.tasks)


@postgres
async def test_only_the_tool_still_out_when_the_budget_expires_is_the_one_skipped(box: Box) -> None:
    lookups = _lookups(box, BOTH, budget_ms=50)
    box.found["recall"] = {"facts": [{"text": "vegetariano"}]}
    quick = box.lookup

    async def search_is_slow(
        tool: PlatformTool, arguments: JsonObject, speech: str | None
    ) -> JsonObject:
        if tool == "search":
            await asyncio.sleep(1)
        return await quick(tool, arguments, speech)

    lookups.lookup = search_is_slow
    skipped = await lookups.turn_ended("menú del día", None)
    assert [error.code for error in skipped] == ["search_skipped"]
    assert [item.name for item in lookups.items if isinstance(item, llm.FunctionCall)] == ["recall"]
