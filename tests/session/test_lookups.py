"""One call's lookups: what the declaration runs before a turn, and the pair it leaves behind."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, override

import pytest
from livekit.agents import llm as agents

from pinecall.session.declaring import ToolUse
from pinecall.session.knowing import a_line_for_the_file_it_ships_with
from pinecall.session.lookups import NoLookup, TurnLookups
from pinecall.types import AgentConfig, Blocks, Docs, MemoryPolicy, PlatformTool

pytestmark = pytest.mark.unit

CALL = "call_1"
ANA = "+34600000001"

REMEMBERS = AgentConfig(
    slug="clinica-norte",
    channels=frozenset({"phone"}),
    memory=MemoryPolicy(remember=("preference",)),
)
SEARCHES = AgentConfig(
    slug="clinica-norte", channels=frozenset({"phone"}), docs=Docs(base="clinica", k=4)
)
BOTH = replace(REMEMBERS, docs=Docs(base="clinica", k=4))


class Answering:
    """A lookup service that answers from a script and remembers everything it was asked."""

    def __init__(self, after_s: float = 0.0, failing: Exception | None = None) -> None:
        self._after_s = after_s
        self._failing = failing
        self.asked: list[tuple[str, str, Mapping[str, Any], str | None]] = []

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        self.asked.append((call, tool, dict(input), speech_id))
        await asyncio.sleep(self._after_s)
        if self._failing is not None:
            raise self._failing
        if tool == "recall":
            return {
                "facts": [{"text": "prefiere la mañana", "source": "call_8", "since": "2026-09-01"}]
            }
        return {"chunks": [{"path": "tarifas.md", "heading": "Tarifas", "text": "Son 45 €."}]}


def a_lookups(
    service: Answering | NoLookup, config: AgentConfig = BOTH, budget_ms: int = 500
) -> TurnLookups:
    """The lookups of one phone call whose caller memory knows by number."""
    return TurnLookups(service, CALL, ANA, config, budget_ms)


def test_recall_is_declared_only_when_the_class_declares_memory() -> None:
    assert _names(a_lookups(NoLookup(), REMEMBERS)) == ["recall"]
    assert _names(a_lookups(NoLookup(), SEARCHES)) == ["search"]
    assert _names(a_lookups(NoLookup(), BOTH)) == ["recall", "search"]
    assert _names(a_lookups(NoLookup(), AgentConfig(slug="bare"))) == []


async def test_a_turn_runs_what_the_declaration_asks_for_with_the_callers_words() -> None:
    service = Answering()
    lookups = a_lookups(service)
    assert await lookups.turn_ended("quiero un turno", "sp_3") == ()
    assert service.asked == [
        (CALL, "recall", {"contact": ANA, "query": "quiero un turno"}, "sp_3"),
        (CALL, "search", {"query": "quiero un turno"}, "sp_3"),
    ]


async def test_a_caller_nobody_has_identified_is_left_out_and_never_named_as_nobody() -> None:
    service = Answering()
    await TurnLookups(service, CALL, None, REMEMBERS, 500).turn_ended("hola", None)
    assert service.asked == [(CALL, "recall", {"query": "hola"}, None)]


async def test_each_run_leaves_a_pair_the_formatter_can_match_by_call_id() -> None:
    lookups = a_lookups(Answering())
    await lookups.turn_ended("quiero un turno", None)
    calls, outputs = _calls(lookups), _outputs(lookups)
    assert [call.name for call in calls] == ["recall", "search"]
    assert [call.call_id for call in calls] == [output.call_id for output in outputs]
    assert len(set(call.call_id for call in calls)) == 2
    assert all(not output.is_error for output in outputs)


async def test_a_tool_result_is_a_json_object_whose_key_is_facts_or_chunks() -> None:
    lookups = a_lookups(Answering())
    await lookups.turn_ended("quiero un turno", None)
    assert [list(json.loads(output.output)) for output in _outputs(lookups)] == [
        ["facts"],
        ["chunks"],
    ]


async def test_the_pair_is_replaced_whole_every_turn_and_holds_nothing_between_two() -> None:
    lookups = a_lookups(Answering())
    await lookups.turn_ended("uno", None)
    await lookups.turn_ended("dos", None)
    assert _queries(lookups) == ["dos", "dos"]


async def test_docs_in_tool_mode_runs_nothing_before_the_turn_and_still_answers_the_model() -> None:
    service = Answering()
    lookups = a_lookups(service, replace(SEARCHES, docs=Docs(base="clinica", mode="tool")))
    assert await lookups.turn_ended("cuánto cuesta", "sp_1") == ()
    assert lookups.items == ()
    assert service.asked == []
    answered = await lookups.called(ToolUse(call_id="c1", name="search", arguments={"query": "x"}))
    assert list(json.loads(answered)) == ["chunks"]
    assert service.asked == [(CALL, "search", {"query": "x"}, "sp_1")]


async def test_past_the_budget_no_pair_is_added_and_the_log_says_which_tool_did_not_run() -> None:
    lookups = a_lookups(Answering(after_s=0.5), budget_ms=10)
    skipped = await lookups.turn_ended("hola", None)
    assert [error.code for error in skipped] == ["recall_skipped", "search_skipped"]
    assert skipped[0].message == "recall did not run: no answer within 10 ms"
    assert all(error.recoverable for error in skipped)
    assert lookups.items == ()


async def test_one_lookup_that_fails_leaves_the_other_ones_pair_standing() -> None:
    class OnlySearchFails(Answering):
        @override
        async def lookup(
            self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
        ) -> Mapping[str, Any]:
            if tool == "search":
                raise RuntimeError("TEI is down")
            return await super().lookup(call, tool, input, speech_id)

    lookups = a_lookups(OnlySearchFails())
    skipped = await lookups.turn_ended("hola", None)
    assert [error.message for error in skipped] == ["search did not run: TEI is down"]
    assert [call.name for call in _calls(lookups)] == ["recall"]


# ── started while the caller is still talking ───────────────────────────────────


async def test_an_interim_of_enough_words_starts_the_run_and_the_turn_waits_for_nothing() -> None:
    service = Answering()
    lookups = a_lookups(service, budget_ms=0)
    lookups.heard_so_far("cuánto cuesta una revisión")
    await _the_run_comes_back()
    assert _tools_asked(service) == ["recall", "search"]
    # A budget of zero would skip anything still out: both are already back, so nothing is.
    assert await lookups.turn_ended("¿cuánto cuesta una revisión?", "sp_1") == ()
    assert [call.name for call in _calls(lookups)] == ["recall", "search"]
    assert len(service.asked) == 2, "the end of the turn asks nobody a second time"


async def test_a_turn_too_short_to_have_started_a_run_still_gets_its_lookups_at_the_end() -> None:
    service = Answering()
    lookups = a_lookups(service)
    lookups.heard_so_far("buenos días")
    await _the_run_comes_back()
    assert service.asked == [], "a greeting is not a question, and an embed of one finds nothing"
    assert await lookups.turn_ended("buenos días", None) == ()
    assert _tools_asked(service) == ["recall", "search"]
    assert _queries(lookups) == ["buenos días", "buenos días"]


# The pair says the words the lookup was ACTUALLY asked with, which on an eager run is the caller's
# prefix and not their finished sentence: a reader of the log sees what was sent, and the model
# reads a tool_use it could itself have made.
async def test_the_pair_carries_the_prefix_the_run_was_asked_with_and_no_second_run() -> None:
    service = Answering()
    lookups = a_lookups(service)
    lookups.heard_so_far("cuánto cuesta una revisión")
    lookups.heard_so_far("cuánto cuesta una revisión dental")
    await _the_run_comes_back()
    await lookups.turn_ended("¿cuánto cuesta una revisión dental?", None)
    assert len(service.asked) == 2, "one run per turn: a second start is a second embed"
    assert _queries(lookups) == ["cuánto cuesta una revisión", "cuánto cuesta una revisión"]


async def test_a_run_started_for_one_turn_is_never_read_by_the_next() -> None:
    service = Answering()
    lookups = a_lookups(service)
    lookups.heard_so_far("quiero pedir una cita")
    await _the_run_comes_back()
    await lookups.turn_ended("quiero pedir una cita", None)
    await lookups.turn_ended("¿y cuánto cuesta?", None)
    assert _queries(lookups) == ["¿y cuánto cuesta?", "¿y cuánto cuesta?"]
    assert [dict(input)["query"] for _call, _tool, input, _speech in service.asked] == [
        "quiero pedir una cita",
        "quiero pedir una cita",
        "¿y cuánto cuesta?",
        "¿y cuánto cuesta?",
    ]


async def test_only_the_tool_still_out_when_the_budget_expires_is_the_one_skipped() -> None:
    class OnlySearchIsSlow(Answering):
        @override
        async def lookup(
            self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
        ) -> Mapping[str, Any]:
            if tool == "search":
                await asyncio.sleep(0.5)
            return await super().lookup(call, tool, input, speech_id)

    lookups = a_lookups(OnlySearchIsSlow(), budget_ms=50)
    skipped = await lookups.turn_ended("cuánto cuesta una revisión", None)
    assert [error.code for error in skipped] == ["search_skipped"]
    assert [call.name for call in _calls(lookups)] == ["recall"]


async def test_a_class_that_declares_neither_asks_nobody_and_carries_no_pair() -> None:
    service = Answering()
    lookups = a_lookups(service, AgentConfig(slug="bare"))
    assert await lookups.turn_ended("hola", None) == ()
    assert (lookups.items, service.asked) == ((), [])


async def test_the_no_lookup_answers_each_tool_in_its_own_empty_shape() -> None:
    lookups = a_lookups(NoLookup())
    await lookups.turn_ended("hola", None)
    assert [json.loads(output.output) for output in _outputs(lookups)] == [
        {"facts": []},
        {"chunks": []},
    ]


# An eager run is a task, so the loop has to get a turn before it can have answered. A fake
# service answers in one hop; this is the sleep that gives it one, and never a wait on wall clock.
async def _the_run_comes_back() -> None:
    """Let whatever `heard_so_far` started run to completion before the turn ends."""
    await asyncio.sleep(0.05)


def _calls(lookups: TurnLookups) -> list[agents.FunctionCall]:
    """The tool_use half of every pair this turn left, in the order the tools ran."""
    return [item for item in lookups.items if isinstance(item, agents.FunctionCall)]


def _outputs(lookups: TurnLookups) -> list[agents.FunctionCallOutput]:
    """The tool_result half of every pair this turn left, in the order the tools ran."""
    return [item for item in lookups.items if isinstance(item, agents.FunctionCallOutput)]


def _queries(lookups: TurnLookups) -> list[str]:
    """The words each pair says its lookup was actually asked with."""
    return [json.loads(call.arguments)["query"] for call in _calls(lookups)]


def _tools_asked(service: Answering) -> list[str]:
    """Which tools the service was asked to run, in the order it was asked."""
    return [tool for _call, tool, _input, _speech in service.asked]


# livekit's raw-tool helpers are generic over the wrapped function's ParamSpec, which a strict
# checker can only read as Unknown; the tool's own info carries the name as a plain string.
def _names(lookups: TurnLookups) -> list[str]:
    """The names of the tools this call declares to livekit, in the order they are sent."""
    told = [getattr(tool, "info", None) for tool in lookups.declared_tools]
    return [str(info.name) for info in told if info is not None]


# The knowledge block is the platform's to write, so the app sends no prompt.set for it. Without a
# line of its own the log lists identity, tools and the view, and a reader concludes the file
# reached nobody — the conclusion a live call led its own author to on 2026-09-10, wrongly.
@pytest.mark.asyncio
async def test_the_file_a_class_ships_with_gets_a_line_of_its_own_in_the_log() -> None:
    written: list[tuple[str, Any]] = []

    async def emit(type: str, data: Any) -> None:
        written.append((type, data))

    blocks = Blocks(knowledge="La revisión son cuarenta euros.")
    await a_line_for_the_file_it_ships_with(blocks, emit)
    ((type, said),) = written
    assert type == "prompt.changed"
    assert said.name == "knowledge"
    assert said.chars == len("La revisión son cuarenta euros.")


@pytest.mark.asyncio
async def test_a_class_that_ships_no_file_writes_no_line_about_one() -> None:
    written: list[tuple[str, Any]] = []

    async def emit(type: str, data: Any) -> None:
        written.append((type, data))

    await a_line_for_the_file_it_ships_with(Blocks(), emit)
    assert written == []
