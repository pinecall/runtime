"""The in-memory Store: every log in a list, for tests. Nothing survives the process."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pinecall.log.entry import Entry
from pinecall.log.store.protocol import DEFAULT_LIMIT, LogSealed, Metered
from pinecall.types.json import JsonObject


# Keeps ephemeral entries too: a test reads back exactly what it wrote, in the order it wrote it.
class MemoryStore:
    """A Store in dicts. Seq is a counter per log, from 1, assigned under one lock in append."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._calls: dict[str, _Log] = {}
        self._agents: dict[str, _Log] = {}
        self._calls_of: dict[str, list[str]] = {}
        # Every durable entry in the order it was written, whatever its log: what across() pages.
        self._journal: list[tuple[_Log, Entry]] = []
        # The seq and the write must happen with no await between them: that gap is the seq race,
        # two appends reading one counter. One lock makes the guarantee structural, not a habit.
        self._lock = asyncio.Lock()

    async def append(
        self,
        call: str | None,
        agent: str,
        type: str,
        data: JsonObject,
        ephemeral: bool = False,
    ) -> Entry:
        """The next seq of the call's log, or the agent's when call is None. Refused after seal."""
        async with self._lock:
            log = self._log_of(call, agent)
            if log.sealed:
                raise LogSealed(f"call {call} has ended: {type} cannot be appended")
            entry = Entry(
                seq=len(log.entries) + 1,
                ts=self._clock(),
                call=call,
                agent=agent,
                type=type,
                ephemeral=ephemeral,
                data=data,
            )
            log.entries.append(entry)
            if not ephemeral:
                self._journal.append((log, entry))
            return entry

    async def since(self, call: str, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """Entries above the cursor. Seq is position plus one here, so the cursor is a slice."""
        return _page(self._calls.get(call), after, limit)

    async def agent_since(
        self, agent: str, after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Entry]:
        """The agent's own entries above the cursor, the same way."""
        return _page(self._agents.get(agent), after, limit)

    async def seal(self, call: str) -> None:
        """Sealing twice is still sealed, and a call nobody wrote to can be sealed too."""
        async with self._lock:
            self._calls.setdefault(call, _Log()).sealed = True

    async def list_calls(self, agent: str) -> list[str]:
        """Every call this agent wrote to, in the order it first did."""
        return list(self._calls_of.get(agent, []))

    async def calls_of(
        self,
        org: str,
        limit: int,
        env: str | None = None,
        holder: str | None = None,
        agent: str | None = None,
    ) -> list[str]:
        """The org's calls newest first: a dict keeps the order the logs were opened in."""
        mine = [
            call
            for call, log in self._calls.items()
            if log.org == org
            # A call owned with no corner reads as production's, the org's own: what 0024 says
            # of every row from before it, and what the postgres store answers for them.
            and (env is None or (log.env or "production") == env)
            and (holder is None or (log.holder or "") == holder)
            and (agent is None or call in self._calls_of.get(agent, ()))
        ]
        return list(reversed(mine))[:limit]

    async def latest_seq(self, call: str) -> int:
        """How many entries the call has, which is its highest seq; 0 when it has none."""
        log = self._calls.get(call)
        return len(log.entries) if log else 0

    async def owned(
        self,
        call: str | None,
        agent: str,
        org: str,
        env: str | None = None,
        holder: str | None = None,
    ) -> None:
        """The first claim keeps a log, exactly as the head row's coalesce does."""
        async with self._lock:
            log = self._log_of(call, agent)
            if log.org is None:
                log.org = org
            if call is not None and log.env is None and env is not None:
                log.env = env
                log.holder = holder or ""

    async def moved(self, agent: str, org: str) -> int:
        """The agent's own log and every call of it, into another org. As many as there were."""
        async with self._lock:
            logs = [self._agents[agent]] if agent in self._agents else []
            logs += [
                self._calls[call] for call in self._calls_of.get(agent, ()) if call in self._calls
            ]
            for log in logs:
                log.org = org
            return len(logs)

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Whose log this is, without creating one to ask."""
        log = self._agents.get(agent) if call is None else self._calls.get(call)
        return None if log is None else log.org

    async def across(
        self, types: Sequence[str], after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Metered]:
        """The journal above the position, kept to these types. Position is index plus one."""
        wanted = set(types)
        rows = self._journal[max(after, 0) :]
        return [
            Metered(position=after + offset + 1, org=log.org, entry=entry)
            for offset, (log, entry) in enumerate(rows)
            if entry.type in wanted
        ][:limit]

    def _log_of(self, call: str | None, agent: str) -> _Log:
        """The agent's own log when there is no call; otherwise the call's, filed by agent."""
        if call is None:
            return self._agents.setdefault(agent, _Log())
        if call not in self._calls:
            self._calls[call] = _Log()
            self._calls_of.setdefault(agent, []).append(call)
        return self._calls[call]


@dataclass
class _Log:
    """One log: its entries in seq order, and whether it has ended."""

    entries: list[Entry] = field(default_factory=list[Entry])
    sealed: bool = False
    org: str | None = None
    # A call's corner, as the head row keeps it: None until a claim said which world.
    env: str | None = None
    holder: str | None = None


def _page(log: _Log | None, after: int, limit: int) -> list[Entry]:
    """The slice above the cursor; an unknown log reads as empty, never as an error."""
    if log is None:
        return []
    start = max(after, 0)
    return log.entries[start : start + limit]
