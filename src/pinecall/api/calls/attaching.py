"""A live call bound to whichever socket now holds its agent: call.attached, then its tools."""

from __future__ import annotations

from pinecall.api.agents.handlers import Live
from pinecall.api.agents.holding import Held, SocketId
from pinecall.api.agents.registry import Registry
from pinecall.log.entry import Entry
from pinecall.types.json import JsonObject

# The entries a socket that takes a call over is rebuilt from. The prompt is not among them: the
# log keeps only its hash (prompt.changed), and the socket sends its whole prompt again anyway.
STARTED = "call.started"
STATE = "state.changed"
# The code a page showed that the call claimed (api/codes.py), carried so the next socket's view
# knows the caller is also on the site instead of reading "no" until the call ends.
CLAIMED = "call.claimed"


# A call is its agent's, never a socket's. When the socket serving it drains or dies, or the
# gateway that knew which socket it was restarts, the next socket holding the agent takes it here,
# and the log says so: call.attached, written before anything else reaches that socket.
async def attached(live: Live, call: str, app: SocketId) -> Entry | None:
    """This socket serves the call from now on: told how it started and where it stands."""
    served = live.attach(call, app)
    if served is None:
        return None
    entries = await served.log.whole()
    started: JsonObject = {}
    state: JsonObject = {}
    claimed: str | None = None
    for entry in entries:
        if entry.type == STARTED:
            started = entry.data
        elif entry.type == STATE:
            state = entry.data.get("state", {})
        elif entry.type == CLAIMED and isinstance(entry.data.get("code"), str):
            claimed = str(entry.data["code"])
    seq = await served.log.latest_seq()
    said = await served.log.append(
        "call.attached",
        {"app": app, "started": started, "state": state, "seq": seq, "claimed": claimed},
    )
    # A tool the model is still waiting on went down the socket that left: this one is asked
    # again, with the entry the log kept, so its tool.result lands on the call_id already waiting.
    # Into the same queue the call.attached went into, so the socket hears them in that order.
    for waiting in live.pending_tools(call):
        served.entries.offer(waiting)
    return said


async def parked_calls_of(live: Live, held: Held, app: SocketId) -> list[str]:
    """Every live call of that agent nobody serves, to the socket that just registered it."""
    return [call for call in live.parked(*held) if await attached(live, call, app) is not None]


# The rule a socket that leaves is held to, whether it closed or drained: each of its calls goes to
# the socket that would take a new call of that agent in that corner, or waits for one, parked.
async def handed_on(live: Live, registry: Registry, calls: list[str]) -> tuple[int, int]:
    """Those calls to whoever serves their agent now: how many were handed, how many wait."""
    handed = 0
    for call in calls:
        served = live.served(call)
        if served is None:
            continue
        env, holder = served.context.env, served.holder
        serving = registry.serving(env, served.agent, None, holder)
        if serving is not None and await attached(live, call, serving.owner) is not None:
            handed += 1
        else:
            live.attach(call, None)
    return handed, len(calls) - handed
