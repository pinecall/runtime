"""One call's fills: the knowledge fixed at the start, the turn's under a budget, and the misses."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from pinecall.session.filling import (
    REMEMBER_FAILED,
    Filling,
    NoFiller,
    NoRememberer,
    Rememberer,
    remembered_within,
)
from pinecall.types import Blocks, KnowledgeFile, Marker, PromptBlock

pytestmark = pytest.mark.unit

CALL = "call_1"
A_FILE = KnowledgeFile("./knowledge/clinica.md", "The clinic opens at nine.")
KNOWLEDGE = "<!-- knowledge: ./knowledge/clinica.md -->"
MEMORY = '<!-- memory: {"kinds":["preference"],"limit":6} -->'
RETRIEVED = '<!-- retrieved: {"k":4} -->'


class Answering:
    """A filler that answers every marker with a sentence, and remembers what it was asked."""

    def __init__(self, after_s: float = 0.0, failing: Exception | None = None) -> None:
        self._after_s = after_s
        self._failing = failing
        self.asked: list[tuple[str, str, tuple[Marker, ...], str | None]] = []

    async def fill(
        self, call: str, query: str, markers: Sequence[Marker], speech_id: str | None
    ) -> Mapping[str, str]:
        self.asked.append((call, query, tuple(markers), speech_id))
        await asyncio.sleep(self._after_s)
        if self._failing is not None:
            raise self._failing
        return {marker.line: f"filled {marker.name} for {query!r}" for marker in markers}


def a_filling(filler: Answering | NoFiller, knowledge: KnowledgeFile | None = A_FILE) -> Filling:
    """The default layout, the knowledge marker in its block, memory and retrieval in the view."""
    blocks = Blocks()
    blocks.set("identity", "You are Clara.")
    blocks.set("knowledge", f"## What you know\n\n{KNOWLEDGE}")
    blocks.set("view", f"## You remember\n\n{MEMORY}\n\n## Relevant\n\n{RETRIEVED}")
    return Filling(filler, CALL, blocks, knowledge, budget_ms=50)


async def test_the_knowledge_is_filled_before_any_turn_and_stays_the_same_bytes_after() -> None:
    filling = a_filling(Answering())
    before = filling.fills
    assert before == {KNOWLEDGE: A_FILE.text}
    await filling.turn_ended("hola", "sp_1")
    assert filling.fills[KNOWLEDGE] == A_FILE.text


async def test_an_agent_with_no_knowledge_file_fills_the_marker_with_nothing() -> None:
    assert a_filling(Answering(), knowledge=None).fills == {}


async def test_a_turn_asks_for_the_dynamic_markers_with_the_callers_words_and_the_speech() -> None:
    filler = Answering()
    filling = a_filling(filler)
    assert await filling.turn_ended("quiero un turno", "sp_3") == ()
    [(call, query, markers, speech)] = filler.asked
    assert (call, query, speech) == (CALL, "quiero un turno", "sp_3")
    assert [marker.name for marker in markers] == ["memory", "retrieved"]
    assert filling.fills[MEMORY] == "filled memory for 'quiero un turno'"
    assert filling.fills[RETRIEVED] == "filled retrieved for 'quiero un turno'"


async def test_the_turns_fills_are_replaced_whole_and_hold_nothing_between_turns() -> None:
    filling = a_filling(Answering())
    await filling.turn_ended("uno", None)
    await filling.turn_ended("dos", None)
    assert filling.fills[MEMORY] == "filled memory for 'dos'"


async def test_past_the_budget_the_turn_goes_on_with_nothing_filled_and_says_which() -> None:
    filling = a_filling(Answering(after_s=0.5))
    skipped = await filling.turn_ended("hola", None)
    assert [error.code for error in skipped] == ["memory_skipped", "retrieval_skipped"]
    assert skipped[0].message == "memory was not filled: no answer within 50 ms"
    assert skipped[1].message == "retrieval was not filled: no answer within 50 ms"
    assert all(error.recoverable for error in skipped)
    assert filling.fills == {KNOWLEDGE: A_FILE.text}


async def test_a_filler_that_raises_is_a_skipped_fill_and_never_a_broken_turn() -> None:
    filling = a_filling(Answering(failing=RuntimeError("TEI is down")))
    skipped = await filling.turn_ended("hola", None)
    assert [error.message for error in skipped] == [
        "memory was not filled: TEI is down",
        "retrieval was not filled: TEI is down",
    ]


async def test_only_the_markers_that_were_asked_are_reported_when_they_go_unfilled() -> None:
    blocks = Blocks((PromptBlock("identity", "static"), PromptBlock("view", "dynamic")))
    blocks.set("view", RETRIEVED)
    filling = Filling(Answering(after_s=0.5), CALL, blocks, None, budget_ms=50)
    assert [error.code for error in await filling.turn_ended("hola", None)] == ["retrieval_skipped"]


async def test_a_view_with_no_marker_asks_nobody() -> None:
    filler = Answering()
    blocks = Blocks()
    blocks.set("view", "The caller is Ana.")
    filling = Filling(filler, CALL, blocks, None, budget_ms=50)
    assert await filling.turn_ended("hola", None) == ()
    assert filler.asked == []


async def test_the_no_filler_answers_nothing_for_every_marker() -> None:
    filling = a_filling(NoFiller())
    assert await filling.turn_ended("hola", None) == ()
    assert filling.fills == {KNOWLEDGE: A_FILE.text}


class Forgetting:
    """A rememberer that fails the way a test tells it to."""

    def __init__(self, after_s: float = 0.0, failing: Exception | None = None) -> None:
        self._after_s = after_s
        self._failing = failing
        self.remembered: list[str] = []

    async def remember(self, call: str) -> None:
        await asyncio.sleep(self._after_s)
        if self._failing is not None:
            raise self._failing
        self.remembered.append(call)


async def test_remembering_within_the_budget_writes_nothing_to_the_log() -> None:
    rememberer = Forgetting()
    assert await remembered_within(rememberer, CALL, 1.0) is None
    assert rememberer.remembered == [CALL]


async def test_a_rememberer_past_its_budget_is_a_recoverable_entry_naming_the_budget() -> None:
    failed = await remembered_within(Forgetting(after_s=0.5), CALL, 0.05)
    assert failed is not None
    assert (failed.code, failed.recoverable) == (REMEMBER_FAILED, True)
    assert failed.message == "memory was not written: no answer within 0.05 s"


async def test_a_rememberer_that_raises_is_a_recoverable_entry_with_its_words() -> None:
    failed = await remembered_within(Forgetting(failing=RuntimeError("no model")), CALL, 1.0)
    assert failed is not None
    assert failed.message == "memory was not written: no model"


async def test_the_no_rememberer_is_a_rememberer_that_writes_nothing() -> None:
    rememberer: Rememberer = NoRememberer()
    assert await remembered_within(rememberer, CALL, 1.0) is None
