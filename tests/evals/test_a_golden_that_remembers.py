"""A golden that opens a call already knowing things: the facts are its own, the tools are real."""

from collections.abc import Mapping
from typing import Any

import pytest

from pinecall.evals.goldens import Golden
from pinecall.evals.remembering import A_GOLDEN, Remembering
from pinecall.types import PlatformTool

pytestmark = pytest.mark.unit

A_FACT = "Prefiere que le llamen por la mañana"


class Recording:
    """The gateway's own lookups, standing in: what it was asked, and a fixed answer."""

    def __init__(self) -> None:
        self.asked: list[PlatformTool] = []

    async def lookup(
        self,
        call: str,  # noqa: ARG002 — the protocol's shape
        tool: PlatformTool,
        input: Mapping[str, Any],  # noqa: ARG002 — the protocol's shape
        speech_id: str | None,  # noqa: ARG002 — the protocol's shape
    ) -> Mapping[str, Any]:
        self.asked.append(tool)
        return {"chunks": []}


async def test_a_recall_is_answered_with_the_goldens_own_facts() -> None:
    gateway = Recording()
    said = await Remembering(gateway, [A_FACT]).lookup("call_1", "recall", {}, None)
    assert said == {"facts": [{"text": A_FACT, "source": A_GOLDEN}]}


async def test_the_gateway_is_never_asked_to_recall_for_a_golden() -> None:
    gateway = Recording()
    await Remembering(gateway, [A_FACT]).lookup("call_1", "recall", {}, None)
    assert gateway.asked == []


async def test_a_search_is_the_real_index_because_that_is_what_a_golden_is_asking() -> None:
    gateway = Recording()
    said = await Remembering(gateway, [A_FACT]).lookup("call_1", "search", {"query": "x"}, None)
    assert said == {"chunks": []}
    assert gateway.asked == ["search"]


def test_a_golden_that_seeds_nothing_reads_as_seeding_nothing() -> None:
    assert Golden(name="sin memoria", input=["hola"]).memory == []


def test_a_golden_writes_the_facts_a_call_opens_knowing_in_its_own_words() -> None:
    golden = Golden(name="con memoria", input=["hola"], memory=[A_FACT])
    assert golden.memory == [A_FACT]
