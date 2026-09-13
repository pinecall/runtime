"""The Store Protocol: the verbs a log's persistence answers, and the one refusal it raises."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pinecall._exceptions import PinecallError
from pinecall.log.entry import Entry
from pinecall.types.json import JsonObject

# One page of a log. A reader replays in pages of this size, then goes live.
DEFAULT_LIMIT = 500


# A seq numbers one log; a position numbers every durable row the store ever wrote, in the order
# it wrote them. It is what a projection over EVERY log resumes from — the operator's usage — and
# it is never on the wire's envelope: a reader of one call has a seq and needs nothing else.
@dataclass(frozen=True)
class Metered:
    """One entry as a projection across logs reads it: where it sits, whose log, the entry."""

    position: int
    org: str | None
    entry: Entry


class LogSealed(PinecallError):
    """An append reached a log that already ended. The call is over; nothing more is true of it."""


# append is the only place a seq is born. Everything else reads, and reads by seq.
class Store(Protocol):
    """Where entries live: one log per call, and one per agent for what happens outside a call."""

    async def append(
        self,
        call: str | None,
        agent: str,
        type: str,
        data: JsonObject,
        ephemeral: bool = False,
    ) -> Entry:
        """Write one entry, seq and ts assigned here, and return it. call None: the agent's log."""
        ...

    async def since(self, call: str, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """A call's entries above the cursor, seq order, at most limit. Ephemerals may be gone."""
        ...

    async def agent_since(
        self, agent: str, after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Entry]:
        """The agent's own log above the cursor: registered, configured, errors, a line per call."""
        ...

    async def seal(self, call: str) -> None:
        """Close a call's log for good: every later append is refused with LogSealed."""
        ...

    async def calls_of(self, org: str, limit: int) -> list[str]:
        """The org's newest calls across every agent it holds, newest first, at most `limit`."""
        ...

    async def list_calls(self, agent: str) -> list[str]:
        """The id of every call the agent handled, oldest first."""
        ...

    async def latest_seq(self, call: str) -> int:
        """The highest seq the call's log has given out; 0 for a call nobody wrote to."""
        ...

    # Whose a log is, is a fact about the log and lives on its head row: the org whose key opened
    # the call, the org that registered the slug. The first claim stands; nothing moves a log.
    async def owned(self, call: str | None, agent: str, org: str) -> None:
        """This log is the org's. Said at open or at register; a later claim changes nothing."""
        ...

    # The one thing that undoes `owned`, and the reason it exists: a slug is one org's for as long
    # as its log is, so an agent registered from a laptop that was pointed at the wrong key stayed
    # in that org for good — with every call it had taken. There was no way back. It is the box
    # operator's verb and nobody else's: `orgs move`.
    async def moved(self, agent: str, org: str) -> int:
        """Put this agent's own log and every call of it in another org. How many rows moved."""
        ...

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Whose log this is, or None when no org has claimed it."""
        ...

    async def across(
        self, types: Sequence[str], after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Metered]:
        """Every durable entry of these types, whatever its log, above the position, in order."""
        ...
