"""One call's lookups: recall and search as declared tools, and the pair each run leaves behind."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from livekit.agents import llm as agents
from livekit.agents.llm import ToolError

from pinecall.session.declaring import ToolUse, declared
from pinecall.types import AgentConfig, PlatformTool, platform_tools
from pinecall.types.lookup import NOT_LOOKED_UP, PLATFORM_TOOLS, arguments_for, skipped_code
from pinecall_protocol.events import ErrorEvent


class Lookup(Protocol):
    """Who runs a platform tool: the gateway in-process on text, over HTTP on voice."""

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        """What the tool found, as the JSON object the model reads inside a tool result."""
        ...


class NoLookup:
    """A process with nothing to look anything up in: both tools answer with nothing found."""

    async def lookup(
        self,
        call: str,  # noqa: ARG002 — the protocol's shape
        tool: PlatformTool,
        input: Mapping[str, Any],  # noqa: ARG002 — the protocol's shape
        speech_id: str | None,  # noqa: ARG002 — the protocol's shape
    ) -> Mapping[str, Any]:
        """The empty answer in the tool's own shape: nothing found, and nothing pretended."""
        return {"facts": []} if tool == "recall" else {"chunks": []}


# Everything that arrived from outside the conversation reaches the model JSON-encoded inside a
# tool_result, which is the one place both vendors name for it: docs/security/prompt-injection.md.
def as_tool_result(output: Mapping[str, Any]) -> str:
    """What livekit puts in the tool_result block: the lookup's object, as a JSON string."""
    return json.dumps(dict(output), ensure_ascii=False)


# One per call. A lookup the platform runs itself leaves a real tool_use / tool_result pair in the
# request and nothing in the history: the pair is rebuilt when the caller's turn ends and never
# piles up, exactly as the view is. A lookup the MODEL calls reaches the same service through the
# same callable, and livekit writes that pair into the history itself.
class TurnLookups:
    """One call's platform tools: what they declare, what the turn ran, and the pair it left."""

    def __init__(
        self,
        lookup: Lookup,
        call: str,
        contact: str | None,
        config: AgentConfig,
        budget_ms: int,
    ) -> None:
        self._lookup = lookup
        self._call = call
        self._contact = contact
        self._config = config
        self._budget_ms = budget_ms
        self._items: tuple[agents.ChatItem, ...] = ()
        self._speech: str | None = None
        self._runs = 0

    @property
    def declared_tools(self) -> list[agents.Tool]:
        """recall and search as livekit declares a tool, when the class declared what they read."""
        return declared(platform_tools(self._config), self.called)

    @property
    def items(self) -> tuple[agents.ChatItem, ...]:
        """This turn's pairs, in the order they ran: the request carries them and holds none."""
        return self._items

    # The whole caller turn is the query; a later card may ask on the eager partial transcript.
    # A lookup never delays a reply past the budget: past it, or on any failure, no pair is added
    # and the log says which tool did not run and why.
    async def turn_ended(self, query: str, speech_id: str | None) -> tuple[ErrorEvent, ...]:
        """The lookups this declaration asks for before a turn; what did not run, as entries."""
        self._items = ()
        self._speech = speech_id
        tools = self._what_the_platform_runs
        if not tools:
            return ()
        try:
            answered = await asyncio.wait_for(
                asyncio.gather(
                    *(self._ran(tool, query, speech_id) for tool in tools), return_exceptions=True
                ),
                self._budget_ms / 1000,
            )
        except TimeoutError:
            return tuple(_skipped(tool, f"no answer within {self._budget_ms} ms") for tool in tools)
        return self._what_came_back(tools, query, answered)

    # livekit's own callable for a tool the MODEL chose: the same service, the same encoding, and
    # a failure the model reads in its own turn rather than a turn that never comes.
    async def called(self, use: ToolUse) -> str:
        """One lookup the model asked for, as the JSON string it reads back."""
        tool = _a_platform_tool(use.name)
        try:
            output = await self._lookup.lookup(self._call, tool, use.arguments, self._speech)
        except Exception as failed:  # noqa: BLE001 — a lookup must never break a turn
            raise ToolError(
                NOT_LOOKED_UP.format(tool=tool, why=str(failed) or type(failed).__name__)
            ) from failed
        return as_tool_result(output)

    @property
    def _what_the_platform_runs(self) -> tuple[PlatformTool, ...]:
        """What runs before a turn: memory whenever it is declared, docs unless the model has it."""
        tools: list[PlatformTool] = []
        if self._config.memory is not None:
            tools.append("recall")
        if self._config.docs is not None and self._config.docs.mode == "retrieved":
            tools.append("search")
        return tuple(tools)

    async def _ran(
        self, tool: PlatformTool, query: str, speech_id: str | None
    ) -> Mapping[str, Any]:
        """One lookup the platform runs on the caller's words, straight from the service."""
        return await self._lookup.lookup(
            self._call, tool, arguments_for(tool, query, self._contact), speech_id
        )

    def _what_came_back(
        self,
        tools: Sequence[PlatformTool],
        query: str,
        answered: Sequence[Mapping[str, Any] | BaseException],
    ) -> tuple[ErrorEvent, ...]:
        """The pairs this turn carries, and one entry per lookup that came back a failure."""
        items: list[agents.ChatItem] = []
        skipped: list[ErrorEvent] = []
        for tool, answer in zip(tools, answered, strict=True):
            if isinstance(answer, BaseException):
                skipped.append(_skipped(tool, str(answer) or type(answer).__name__))
                continue
            items.extend(self._a_pair(tool, arguments_for(tool, query, self._contact), answer))
        self._items = tuple(items)
        return tuple(skipped)

    # A pair livekit's formatter can group: it matches a call to its output by call_id
    # (llm/_provider_format/utils.py:group_tool_calls), and a half it cannot match it drops with a
    # warning — which would leave the model a tool_use no result ever answered. The same shape
    # session/clock.py puts today's date in, and for the same reason: a pair is the only way text
    # from outside the conversation travels without being read as somebody's words.
    def _a_pair(
        self, tool: PlatformTool, input: Mapping[str, Any], output: Mapping[str, Any]
    ) -> tuple[agents.FunctionCall, agents.FunctionCallOutput]:
        """One run of one tool as the model sees it: the call it could have made, and the answer."""
        self._runs += 1
        call_id = f"lu_{self._runs}_{tool}"
        return (
            agents.FunctionCall(
                call_id=call_id, name=tool, arguments=json.dumps(dict(input), ensure_ascii=False)
            ),
            # reply_required is False because nothing was asked: the output is context, not a
            # turn owed an answer, and a realtime model would otherwise speak on reading it.
            agents.FunctionCallOutput(
                call_id=call_id,
                name=tool,
                output=as_tool_result(output),
                is_error=False,
                reply_required=False,
            ),
        )


def _skipped(tool: PlatformTool, why: str) -> ErrorEvent:
    """The error entry of a lookup the platform could not run: recoverable, and it names why."""
    return ErrorEvent(
        code=skipped_code(tool), message=NOT_LOOKED_UP.format(tool=tool, why=why), recoverable=True
    )


def _a_platform_tool(name: str) -> PlatformTool:
    """The name livekit called our callable with, narrowed to the tool it was declared as."""
    if name not in PLATFORM_TOOLS:
        raise ToolError(f"{name} is not a tool the platform runs")
    return cast("PlatformTool", name)
