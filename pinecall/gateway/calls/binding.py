"""A call handed to a socket: call.attached, then its pump; parked calls given to the next one."""

from collections.abc import Iterable

from pinecall.domain.scope import Scope
from pinecall.gateway._served import CLAIMED, STARTED, ServedCalls
from pinecall.gateway._sockets import SocketId, Sockets
from pinecall.log.reduce import reduce
from pinecall.session.tools import unanswered
from pinecall.wire.events import CallAttached, CallStarted
from pinecall.wire.frames import Entry


async def attach(live: ServedCalls, call: str, app: SocketId) -> Entry | None:
    """Give the call to this socket: call.attached first, then the tools still waiting."""
    served = live.attach(call, app)
    if served is None:
        return None
    entries = await served.log.whole()
    started = next((entry for entry in entries if entry.type == STARTED), None)
    if started is None:
        # Not started yet: the socket hears it from what comes next.
        live.pumped(call, after=entries[-1].seq if entries else 0)
        return None
    claimed = next(
        (str(entry.data["code"]) for entry in reversed(entries) if entry.type == CLAIMED), None
    )
    data = CallAttached(
        app=app,
        started=CallStarted.model_validate(started.data),
        state=reduce(entries).app_state,
        seq=entries[-1].seq,
        claimed=claimed,
    )
    entry = await served.log.append("call.attached", data.written())
    # After call.attached, so the result lands on the call id still awaited: read off the log, since
    # the worker may have asked them of another gateway.
    live.pumped(call, after=entry.seq - 1, then=unanswered(entries))
    return entry


async def parked_calls_of(live: ServedCalls, scope: Scope, slug: str, app: SocketId) -> None:
    """Give every parked call of the agent in the scope to this socket."""
    for call in live.parked(scope, slug):
        await attach(live, call, app)


# Each call of a leaving socket goes where a new call would, or waits parked.
async def handed_on(live: ServedCalls, sockets: Sockets, calls: Iterable[str]) -> tuple[int, int]:
    """Hand the calls on; how many were handed and how many parked."""
    handed = parked = 0
    for call in calls:
        served = live.calls.get(call)
        if served is None:
            continue
        taking = sockets.serving(served.scope, served.agent, None)
        if taking is not None and await attach(live, call, taking.owner) is not None:
            handed += 1
        else:
            live.attach(call, None)
            parked += 1
    return handed, parked
