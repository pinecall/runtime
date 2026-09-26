"""One tool from a worker to the app that declared it, and the app's answer to whoever waits."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import AppKeyDep, LogsDep
from pinecall.api._live import LiveDep
from pinecall.api.agents.handlers import Socket, asked, handles
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.worker_doors import NOT_OPEN, refuse_another_orgs_call
from pinecall.auth.corner import Corner, corner_of
from pinecall.auth.keys import is_the_fleets
from pinecall.log.entry import Entry
from pinecall.session.declaring import ToolUse
from pinecall_protocol import Command, WireModel, defs, encode
from pinecall_protocol.events import ToolCall

router = APIRouter()

# The worker asked for a tool of an agent nobody in this org ever held here: it never registered.
# The caller is on a line, so this is an answer and not a wait. An app that is only between two
# processes — a deploy — is waited for instead.
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
    key: AppKeyDep,
    registry: RegistryDep,
    logs: LogsDep,
    live: LiveDep,
) -> defs.ToolResult:
    """A worker's tool call through the app's own process and back, with both entries logged."""
    # A call this gateway does not serve is one it forgot — it restarted — and the worker, which
    # still holds the call, reopens it on this 404 and asks again.
    served = live.served(call)
    if served is None:
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))
    # The call's org against the key's, before anything of the call is read: the agent check
    # below only says the key's org holds THAT agent, which another org's call id does not need.
    refuse_another_orgs_call(live, key, call)
    # Whose app the tool goes out to: the key's own corner for a tenant's worker, and for the
    # fleet's the corner of the CALL — said once when it was opened, and kept by this process.
    whose = corner_of(key)
    if is_the_fleets(key):
        whose = Corner(served.org, served.context.env, served.holder)
    held = registry.of(whose.env, agent, whose.holder)
    if held is not None and held.org != whose.org:
        raise HTTPException(status_code=409, detail=NO_APP.format(agent=agent))
    # Nobody holds it right now, but somebody did: the process is being deployed. The tool.call is
    # written and waits its own timeout for the next socket, which is sent it with call.attached.
    if held is None and await logs.owner(None, agent) != whose.org:
        raise HTTPException(status_code=409, detail=NO_APP.format(agent=agent))
    config = served.config if held is None else held.config
    log = served.log

    # The codec's own encoding, and not exclude_none: a method that returned null sent `output:
    # null`, and that is a fact of the call. Dropping it left `tool.result` without an output at
    # all, indistinguishable from a method that returned nothing (2026-09-08, the first talk call).
    async def emit(type: str, event: WireModel) -> Entry:
        """Append the entry. The app hears it because the call is served (api/_live.py)."""
        return await log.append(type, encode(event))

    use = ToolUse(call_id=wanted.call_id, name=wanted.name, arguments=dict(wanted.arguments))
    return await live.waiting(call, config).ran(use, wanted.speech_id or "", emit)


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
