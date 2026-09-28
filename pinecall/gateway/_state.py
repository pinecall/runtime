"""The gateway process: its connections and the state it keeps in memory while it serves."""

import asyncio
from dataclasses import dataclass

from pinecall.fleet.roster import Roster
from pinecall.gateway._served import ServedCalls, Serving
from pinecall.gateway._sockets import Sockets
from pinecall.gateway._threads import Threads
from pinecall.log.logs import Logs
from pinecall.process.connections import Connections
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.tokens import Signer


@dataclass(frozen=True)
class Gateway:
    """The gateway process: its connections, and the state it keeps in memory while it serves."""

    connections: Connections
    logs: Logs
    sockets: Sockets
    live: ServedCalls
    roster: Roster
    codes: Codes
    signer: Signer
    threads: Threads
    # Set when the process is told to stop: every stream ends on it.
    closing: asyncio.Event

    @property
    def serving(self) -> Serving:
        """What a call runs through."""
        return Serving(connections=self.connections, logs=self.logs, live=self.live)
