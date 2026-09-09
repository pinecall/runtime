"""The tool gate both sessions share: tools.set narrows what is open, a closed tool is refused."""

from typing import Any

import pytest
from livekit.agents.llm import ToolError

from pinecall.session.visibility import Visibility
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import WireModel, defs, encode

pytestmark = pytest.mark.unit

FIND = ToolSpec(name="find_slot", description="Free slots", parameters={"type": "object"})
BOOK = ToolSpec(name="book", description="Book a slot", parameters={"type": "object"})
CLARA = AgentConfig(slug="clinica-norte", tools=(FIND, BOOK))


def wanted(*names: str) -> list[defs.ToolSpec]:
    """A tools.set as the app sends it: the names it opens, with whatever schema it repeats."""
    return [defs.ToolSpec(name=name, description="", parameters={}) for name in names]


class Emitted:
    """A session's hand on the log: every entry the gate wrote, in order."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, type: str, event: WireModel) -> None:
        self.entries.append((type, encode(event)))


async def test_everything_declared_is_open_until_a_tools_set_narrows_it() -> None:
    visibility = Visibility(CLARA)
    await visibility.admitted("book", Emitted())
    assert visibility.narrow(wanted("book", "never_declared", "find_slot")) == (
        "book",
        "find_slot",
    )
    await visibility.admitted("find_slot", Emitted())


async def test_a_closed_tool_is_an_error_the_model_reads_and_the_log_says_who_refused_it() -> None:
    visibility = Visibility(CLARA)
    visibility.narrow(wanted("find_slot"))
    emitted = Emitted()
    with pytest.raises(ToolError, match="book is not available now"):
        await visibility.admitted("book", emitted)
    assert emitted.entries == [
        ("error", {"code": "refused", "message": "book is not available now", "recoverable": True})
    ]


async def test_a_tools_set_with_nothing_in_it_closes_every_tool() -> None:
    visibility = Visibility(CLARA)
    assert visibility.narrow([]) == ()
    with pytest.raises(ToolError, match="find_slot is not available now"):
        await visibility.admitted("find_slot", Emitted())
