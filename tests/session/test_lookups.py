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
from pinecall.session.lookups import NoLookup, TurnLookups
from pinecall.types import AgentConfig, Docs, MemoryPolicy, PlatformTool

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
    calls = [item for item in lookups.items if isinstance(item, agents.FunctionCall)]
    outputs = [item for item in lookups.items if isinstance(item, agents.FunctionCallOutput)]
    assert [call.name for call in calls] == ["recall", "search"]
    assert [call.call_id for call in calls] == [output.call_id for output in outputs]
    assert len(set(call.call_id for call in calls)) == 2
    assert all(not output.is_error for output in outputs)


async def test_a_tool_result_is_a_json_object_whose_key_is_facts_or_chunks() -> None:
    lookups = a_lookups(Answering())
    await lookups.turn_ended("quiero un turno", None)
    outputs = [item for item in lookups.items if isinstance(item, agents.FunctionCallOutput)]
    assert [list(json.loads(output.output)) for output in outputs] == [["facts"], ["chunks"]]


async def test_the_pair_is_replaced_whole_every_turn_and_holds_nothing_between_two() -> None:
    lookups = a_lookups(Answering())
    await lookups.turn_ended("uno", None)
    await lookups.turn_ended("dos", None)
    calls = [item for item in lookups.items if isinstance(item, agents.FunctionCall)]
    assert len(calls) == 2
    assert json.loads(calls[0].arguments)["query"] == "dos"


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
    calls = [item for item in lookups.items if isinstance(item, agents.FunctionCall)]
    assert [call.name for call in calls] == ["recall"]


async def test_a_class_that_declares_neither_asks_nobody_and_carries_no_pair() -> None:
    service = Answering()
    lookups = a_lookups(service, AgentConfig(slug="bare"))
    assert await lookups.turn_ended("hola", None) == ()
    assert (lookups.items, service.asked) == ((), [])


async def test_the_no_lookup_answers_each_tool_in_its_own_empty_shape() -> None:
    lookups = a_lookups(NoLookup())
    await lookups.turn_ended("hola", None)
    outputs = [item for item in lookups.items if isinstance(item, agents.FunctionCallOutput)]
    assert [json.loads(output.output) for output in outputs] == [{"facts": []}, {"chunks": []}]


# livekit's raw-tool helpers are generic over the wrapped function's ParamSpec, which a strict
# checker can only read as Unknown; the tool's own info carries the name as a plain string.
def _names(lookups: TurnLookups) -> list[str]:
    """The names of the tools this call declares to livekit, in the order they are sent."""
    told = [getattr(tool, "info", None) for tool in lookups.declared_tools]
    return [str(info.name) for info in told if info is not None]
