"""The tool calls in flight over the app's socket: who is waiting for what, and how long."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pinecall.session.declaring import ToolUse
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import WireModel, defs
from pinecall_protocol.events import ToolCall

# The session's own hand on the log: whoever runs a tool writes its two entries with this.
type Emit = Callable[[str, WireModel], Awaitable[Any]]


# The app's process runs the tool and answers with tool.result over its own socket; between the two
# there is a turn suspended here, and a deadline, because a caller cannot wait for a backend nobody
# is watching.
class ToolCalls:
    """Every tool this call has asked the app for and not yet heard back about."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._waiting: dict[str, asyncio.Future[defs.ToolResult]] = {}

    async def ran(self, call: ToolUse, speech: str, emit: Emit) -> defs.ToolResult:
        """One tool out to the app and its result back, both entries written on the way."""
        await emit(
            "tool.call",
            ToolCall(
                call_id=call.call_id,
                name=call.name,
                arguments=dict(call.arguments),
                speech_id=speech,
            ),
        )
        result = await self.awaited(call.call_id, call.name)
        await emit("tool.result", result)
        return result

    def answered(self, result: defs.ToolResult) -> bool:
        """Hand a result to whoever is waiting for it. False when nobody was: it came too late."""
        waiting = self._waiting.pop(result.call_id, None)
        if waiting is None or waiting.done():
            return False
        waiting.set_result(result)
        return True

    # What the model reads on a timeout is an error in its own turn, not silence: it can apologise,
    # try another tool, or ask the caller something, which is what a person would do.
    async def awaited(self, call_id: str, name: str) -> defs.ToolResult:
        """Wait for the app's tool.result until the tool's declared timeout, then say it lapsed."""
        waiting: asyncio.Future[defs.ToolResult] = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = waiting
        timeout = self._timeout_of(name)
        try:
            return await asyncio.wait_for(waiting, timeout)
        except (TimeoutError, asyncio.CancelledError):
            return defs.ToolResult(
                call_id=call_id,
                name=name,
                error=f"{name} did not answer within {timeout:g}s",
            )
        finally:
            self._waiting.pop(call_id, None)

    def _timeout_of(self, name: str) -> float:
        """The tool's own deadline, or the contract's default for a tool nobody declared."""
        declared = self._config.tools_by_name.get(name)
        return declared.timeout_s if declared else ToolSpec.timeout_s
