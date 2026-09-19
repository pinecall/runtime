"""The apps connected right now, one process each: whose, where it runs, since when, its stop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from pinecall.api._deps import held
from pinecall.api.agents.holding import SocketId
from pinecall.types import Env

# What stopping a process is: the socket told why, in the protocol's own words (an `error` event
# with the code `stopped`), then closed. The app hears it and exits instead of reconnecting —
# a stop that the app answered by dialling back three seconds later would stop nothing.
type Stop = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Process:
    """One app socket as a person looking for what is running needs to see it."""

    app: SocketId
    org: str
    env: Env
    # Whose corner it holds agents in: the member in the sandbox, nobody in production.
    holder: str | None
    # The address the socket came from, as the gateway saw it through its proxy.
    address: str | None
    connected_at: float
    stop: Stop
    # The machine, as the app named itself at agent.register; None until it has.
    host: str | None = None


# None of this is durable, as the registry's own table is not: which processes are connected is a
# fact about sockets open right now, and a restart forgets it until each reconnects.
class Processes:
    """Every app socket this gateway holds open, by its id."""

    def __init__(self) -> None:
        self._open: dict[SocketId, Process] = {}

    def opened(self, process: Process) -> None:
        """A socket is open and its key checked."""
        self._open[process.app] = process

    def named(self, app: SocketId, host: str | None) -> None:
        """The machine the app says it runs on, from its agent.register."""
        if app in self._open and host is not None:
            self._open[app] = replace(self._open[app], host=host)

    def closed(self, app: SocketId) -> None:
        """The socket is gone."""
        self._open.pop(app, None)

    def of(self, app: SocketId) -> Process | None:
        """One open socket by its id, or None."""
        return self._open.get(app)

    def of_org(self, org: str, env: Env) -> tuple[Process, ...]:
        """Every open socket of this org in this world, oldest connection first."""
        mine = (one for one in self._open.values() if one.org == org and one.env == env)
        return tuple(sorted(mine, key=lambda one: one.connected_at))


def the_processes(connection: HTTPConnection) -> Processes:
    """The app sockets open on this gateway right now."""
    return held(connection, "processes", Processes)


ProcessesDep = Annotated[Processes, Depends(the_processes)]
