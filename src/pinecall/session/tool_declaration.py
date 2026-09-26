"""Our ToolSpec as livekit declares a tool: the raw schema, and the callable the session owns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from livekit.agents import llm as agents
from livekit.agents.voice import RunContext

from pinecall.types import ToolSpec


# livekit carries a call's arguments as the JSON string the vendor sent; everything downstream of
# the session — the gate's audience, the log, the app's socket — reads a mapping. Parsed once,
# here, and the session holds these two rather than the library's wire shapes.
@dataclass(frozen=True)
class ToolUse:
    """The model asked for a tool: the id the result must come back under, and the arguments."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]


# What livekit's tool loop hands the model back for one call: our callable returns this string.
type RunTool = Callable[["ToolUse"], Awaitable[str]]


# Our ToolSpec is already JSON Schema and the app's own process runs every tool, so there is no
# Python function to introspect: function_tool(raw_schema=...) is the door that takes a schema.
# The callable IS ours, though, and livekit calls it: that is where the gate stands and where the
# two entries of a tool round trip are written, in the order request < granted < call.
def declared(specs: Sequence[ToolSpec], run: RunTool) -> list[agents.Tool]:
    """The agent's visible tools as livekit declares them: the schema, and our own callable."""
    return [_raw(spec, run) for spec in specs]


def _raw(spec: ToolSpec, run: RunTool) -> agents.Tool:
    """One ToolSpec as a livekit raw-schema tool, wired to the session that owns the call."""

    async def call(raw_arguments: dict[str, Any], context: RunContext[Any]) -> str:
        return await run(
            ToolUse(
                call_id=context.function_call.call_id,
                name=spec.name,
                arguments=dict(raw_arguments),
            )
        )

    schema: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "parameters": dict(spec.parameters),
    }
    return agents.function_tool(call, raw_schema=schema)
