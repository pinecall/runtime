"""What a headless run answers a tool with, and the record of what the model asked for."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from livekit.agents.llm import ToolError

from pinecall.log import as_text
from pinecall.session.tool_declaration import ToolUse
from pinecall_protocol import defs


# One per run and never module-level: the calls belong to the turn that made them. This is the
# `RunTool` seam the bridge already cuts at (session/tool_declaration.py) — livekit's own
# `mock_tools` mocks a method on an Agent subclass, and none of our tools is one: they live in
# the tenant's process behind a socket, so the callable is where an eval stands in for the app.
class Answers:
    """The app's side of a tool, written down: what it returns, and what it was asked."""

    def __init__(self, by_name: Mapping[str, Any]) -> None:
        self._by_name = dict(by_name)
        self.calls: list[ToolUse] = []

    async def __call__(self, use: ToolUse) -> str:
        """One tool call: recorded, then answered the way the wire would have answered it."""
        self.calls.append(use)
        if use.name not in self._by_name:
            # ToolError is how livekit sets is_error on the output the model reads back — the
            # same door `session/voice/tools.py:54` puts a refusal from the app through.
            raise ToolError(f"{use.name}: this eval declares no answer for that tool")
        return as_text(
            defs.ToolResult(call_id=use.call_id, name=use.name, output=self._by_name[use.name])
        )

    def called(self, name: str) -> tuple[ToolUse, ...]:
        """Every call the model made to one tool, in the order it made them."""
        return tuple(use for use in self.calls if use.name == name)
