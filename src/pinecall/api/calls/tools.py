"""One tool from a worker to the app that declared it, and the app's answer to whoever waits."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import KeyDep, LogsDep
from pinecall.api._live import LiveDep
from pinecall.api.agents.handlers import Socket, asked, handles
from pinecall.api.agents.registry import RegistryDep
from pinecall.session.declaring import ToolUse
from pinecall_protocol import Command, WireModel, defs, encode
from pinecall_protocol.events import ToolCall

router = APIRouter()

# The worker asked for a tool of an agent no socket speaks for: the app is gone, or it never
# registered here. The caller is on a line, so this is an answer and not a wait.
NO_APP = "no app is holding agent {agent}: register it before its calls run tools"

# A tool.result that answers nothing: it lapsed, it was answered once already, or the call it
# names runs somewhere else. The app hears which, in the protocol's own words.
NOTHING_WAITING = "no_session"


# ── the worker's door ───────────────────────────────────────────────────────────


# The two entries of a tool round trip are written HERE and not by the worker, exactly as the text
# session writes them from its own path: this is the one side that holds the app's socket, so it
# is the side that knows when the tool went out and when it came back. Written is all it takes for
# the app to hear them — the call is on that socket already, and there is one delivery, not two.
@router.post("/v1/calls/{call}/tools")
async def run_a_tool(
    call: str,
    agent: str,
    wanted: ToolCall,
    key: KeyDep,
    registry: RegistryDep,
    logs: LogsDep,
    live: LiveDep,
) -> dict[str, Any]:
    """A worker's tool call through the app's own process and back, with both entries logged."""
    held = registry.of(key.env, agent)
    if held is None or held.org != key.org:
        raise HTTPException(status_code=409, detail=NO_APP.format(agent=agent))
    log = logs.writing(call, agent)

    # The codec's own encoding, and not exclude_none: a method that returned null sent `output:
    # null`, and that is a fact of the call. Dropping it left `tool.result` without an output at
    # all, indistinguishable from a method that returned nothing (2026-09-08, the first talk call).
    async def emit(type: str, event: WireModel) -> None:
        """Append the entry. The app hears it because the call is served (api/_live.py)."""
        await log.append(type, encode(event))

    use = ToolUse(call_id=wanted.call_id, name=wanted.name, arguments=dict(wanted.arguments))
    result = await live.waiting(call, held.config).ran(use, wanted.speech_id or "", emit)
    return result.model_dump(mode="json")


# ── the app's answer ────────────────────────────────────────────────────────────


# The one handler for tool.result, whichever channel is running the call: a text session holds its
# own waiting room and a worker-run call holds one on the same Live, and both answer the same
# question. Two handlers for one command type would be two rules about who may answer a tool.
@handles("tool.result")
async def take_a_tool_result(socket: Socket, command: Command) -> None:
    """What the tool returned in the app's own process, handed to whoever is waiting for it."""
    result = asked(command, defs.ToolResult)
    session = socket.live.of(command.call)
    if session is not None and session.agent == command.agent and session.tool_answered(result):
        return
    if command.call is not None and socket.live.answered(command.call, result):
        return
    await socket.refuse(
        command.agent,
        NOTHING_WAITING,
        f"no tool call {result.call_id} is waiting on call {command.call!r}: it lapsed, it was "
        f"already answered, or the call is not running here",
        command.model_dump(),
    )
