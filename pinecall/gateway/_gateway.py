"""The gateway process: its connections and the state it keeps in memory while it serves."""

import asyncio
from dataclasses import dataclass, field

from pinecall.evals.runs import Runner
from pinecall.fleet.roster import Roster
from pinecall.gateway._served import ServedCalls, Serving
from pinecall.gateway._sockets import Sockets
from pinecall.gateway.calls.threads import Threads
from pinecall.log.logs import Logs
from pinecall.process.connections import Connections
from pinecall.process.metrics import Counters
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy.codes import Codes
from pinecall.tenancy.knocks import Throttle
from pinecall.tenancy.mail import Outbox
from pinecall.tenancy.prompts import Prompts
from pinecall.tenancy.signin import SignIns
from pinecall.tenancy.throttle import Window
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
    # None when the providers row names no embedding, or the box holds no key for its vendor.
    embedder: Embedder | None
    evals: Runner
    signins: SignIns
    outbox: Outbox
    # Thirty voice samples a minute per key: they cost vendor time and write no usage row.
    samples: Throttle
    # Each org's requests to each family of doors this minute, in each world.
    paced: Window
    # What GET /metrics reads: counted on the append path since the process started.
    counters: Counters = field(default_factory=Counters)
    prompts: Prompts = field(default_factory=Prompts)

    @property
    def serving(self) -> Serving:
        """What a call runs through."""
        return Serving(
            connections=self.connections,
            logs=self.logs,
            live=self.live,
            embedder=self.embedder,
            prompts=self.prompts,
            counters=self.counters,
        )
