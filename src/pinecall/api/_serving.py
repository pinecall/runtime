"""What a door that opens a call needs of the process's live memory: serve it, count, close it."""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import Depends

from pinecall.api._deps import what_is_live
from pinecall.api.agents.registry import SocketId
from pinecall.log.logs import CallLog


# Asked for by the type a door needs (see deps.py), so the doors that open a call — the worker's
# POST /v1/calls, the token door — never import _live.py, which imports the text session,
# which imports the log's own package. connected.Live is the one object that answers it.
class Serving(Protocol):
    """The live memory, as far as a call's door touches it: served, counted, then forgotten."""

    def serve(self, call: str, agent: str, org: str, log: CallLog, app: SocketId | None) -> None:
        """Every entry of this call to ONE app socket, chosen now and kept for the whole call."""
        ...

    def running(self, org: str) -> int:
        """How many of this org's calls are open here right now."""
        ...

    def org_of(self, call: str) -> str | None:
        """Whose call this is, as the door that opened it said; None when none is served."""
        ...

    def close(self, call: str) -> None:
        """The call is over and nothing more will be said on it."""
        ...


ServingDep = Annotated[Serving, Depends(what_is_live)]
