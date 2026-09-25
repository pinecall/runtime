"""The tool calls in flight over the app's socket: who is waiting for what, and how long."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pinecall.log.entry import Entry
from pinecall.session.declaring import ToolUse
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import WireModel, defs
from pinecall_protocol.events import ToolCall

# The session's own hand on the log: whoever runs a tool writes its two entries with this.
type Emit = Callable[[str, WireModel], Awaitable[Any]]

# A tool's round trip is written by the side that holds the log, and it keeps the tool.call entry
# the log handed back: that entry is what a socket taking the call over is sent again.
type Writes = Callable[[str, WireModel], Awaitable[Entry]]


# The app's process runs the tool and answers with tool.result over its own socket; between the two
# there is a turn suspended here, and a deadline, because a caller cannot wait for a backend nobody
# is watching.
class ToolCalls:
    """Every tool this call has asked the app for and not yet heard back about."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._waiting: dict[str, asyncio.Future[defs.ToolResult]] = {}
        # One round trip per call_id, whoever asks: a worker that asks again because the gateway
        # went away mid-request awaits the round trip already running, and writes nothing twice.
        self._running: dict[str, asyncio.Future[defs.ToolResult]] = {}
        # The tool.call entries that went out and are still unanswered, by call_id: what a socket
        # that takes the call over is sent again, with the seq the log gave them.
        self._sent: dict[str, Entry] = {}

    async def ran(self, call: ToolUse, speech: str, emit: Writes) -> defs.ToolResult:
        """One tool out to the app and its result back, both entries written on the way."""
        running = self._running.get(call.call_id)
        if running is None:
            running = asyncio.ensure_future(self._round_trip(call, speech, emit))
            self._running[call.call_id] = running
            running.add_done_callback(lambda _: self._running.pop(call.call_id, None))
        # Shielded: the request that started it may be the one that went away, and the tool.call
        # it wrote is still waiting for the app's answer.
        return await asyncio.shield(running)

    def pending(self) -> tuple[Entry, ...]:
        """The tool.call entries still waiting for the app, in the order the log wrote them."""
        return tuple(sorted(self._sent.values(), key=lambda entry: entry.seq))

    async def _round_trip(self, call: ToolUse, speech: str, emit: Writes) -> defs.ToolResult:
        """The tool.call written, the app's answer awaited, the tool.result written."""
        sent = await emit(
            "tool.call",
            ToolCall(
                call_id=call.call_id,
                name=call.name,
                arguments=dict(call.arguments),
                speech_id=speech,
            ),
        )
        self._sent[call.call_id] = sent
        try:
            result = await self.awaited(call.call_id, call.name)
        finally:
            self._sent.pop(call.call_id, None)
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
        # A cancellation is the turn being cancelled, and it propagates: answering it with a
        # lapsed result wrote a tool.result after the turn was gone (2026-09-26).
        try:
            return await asyncio.wait_for(waiting, timeout)
        except TimeoutError:
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
