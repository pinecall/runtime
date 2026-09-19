"""The in-memory Store: every log in a list, for tests. Nothing survives the process."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pinecall.log.entry import Entry
from pinecall.log.facts import CallFacts, change_of
from pinecall.log.store import memory_index
from pinecall.log.store.index import CallCorner, Day, Found, Threads, Unsealed, Wanted
from pinecall.log.store.memory_index import Indexed, StillOpen
from pinecall.log.store.protocol import DEFAULT_LIMIT, LogSealed, Metered
from pinecall.types import Versions
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
        # Who has read which inbox thread up to when: (org, env, holder, agent, reader, contact).
        self._read: dict[tuple[str, str, str, str, str, str], float] = {}
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
            return self._written(log, call, agent, type, data, ephemeral)

    async def rescored(self, call: str, agent: str, data: JsonObject) -> Entry:
        """A verdict onto a call whose log already sealed: the one entry a sealed log takes."""
        async with self._lock:
            return self._written(self._log_of(call, agent), call, agent, RESCORED, data, False)

    def _written(
        self,
        log: _Log,
        call: str | None,
        agent: str,
        type: str,
        data: JsonObject,
        ephemeral: bool,
    ) -> Entry:
        """One entry onto one log, journalled, and folded into its call's facts."""
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
        if call is None:
            return entry
        # The head row's started_at is the first append's clock, and so is this; the facts are
        # what every entry since said, folded by the one rule the postgres upsert also follows.
        if log.started_at is None:
            log.started_at = entry.ts
        facts = log.facts or CallFacts(call=call, agent=agent)
        change = change_of(entry)
        log.facts = facts if change is None else facts.changed(change)
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
        versions: Versions | None = None,
    ) -> None:
        """The first claim keeps a log, exactly as the head row's coalesce does."""
        async with self._lock:
            log = self._log_of(call, agent)
            if log.org is None:
                log.org = org
            if call is not None and log.env is None and env is not None:
                log.env = env
                log.holder = holder or ""
            if call is not None and versions is not None:
                if log.config_version is None:
                    log.config_version = versions.config
                if log.lexicon_version is None:
                    log.lexicon_version = versions.lexicon

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

    # ── the call index (log/store/index.py), answered by memory_index.py over these logs ──────

    async def corner_of_call(self, call: str) -> CallCorner | None:
        """The corner the claim wrote, production's and the org's own when none was said."""
        log = self._calls.get(call)
        if log is None or log.facts is None:
            return None
        return CallCorner(
            log.org,
            log.env or memory_index.UNCORNERED,
            log.holder or "",
            log.facts.agent,
            log.config_version,
            log.lexicon_version,
        )

    async def facts_of(self, calls: Sequence[str]) -> dict[str, CallFacts]:
        """The facts of each call that has any."""
        kept = {call: self._calls[call].facts for call in calls if call in self._calls}
        return {call: facts for call, facts in kept.items() if facts is not None}

    async def unsealed_spoken(self, quiet_since: float, limit: int) -> list[Unsealed]:
        """Every org's spoken calls still open and quiet since then, the quietest first."""
        return memory_index.unsealed_spoken(self._still_open(), quiet_since, limit)

    async def found(self, org: str, env: str, holder: str, wanted: Wanted, limit: int) -> Found:
        """The corner's calls that match, a page of them, newest first."""
        return memory_index.found(self._indexed(org, env, holder), wanted, limit)

    async def a_day(self, org: str, env: str, holder: str, start: float) -> Day:
        """The corner's day, counted."""
        return memory_index.a_day(self._indexed(org, env, holder), start)

    async def spent_between(self, org: str, start: float, end: float) -> float:
        """What the org's calls in that span cost, every world and corner."""
        return sum(
            one.facts.cost_eur or 0.0
            for one in self._indexed(org)
            if one.started_at is not None and start <= one.started_at < end
        )

    async def threads(
        self,
        org: str,
        env: str,
        holder: str,
        agent: str,
        reader: str,
        after: str | None,
        limit: int,
    ) -> Threads:
        """The agent's inbox, as this reader has read it."""
        mine = [one for one in self._indexed(org, env, holder) if one.facts.agent == agent]
        read = {
            contact: at
            for (o, e, h, a, r, contact), at in self._read.items()
            if (o, e, h, a, r) == (org, env, holder, agent, reader)
        }
        return memory_index.threads(mine, read, after, limit)

    async def calls_with(
        self, org: str, env: str, holder: str, agent: str, contact: str, limit: int
    ) -> list[str]:
        """This contact's newest calls with the agent."""
        mine = [
            one
            for one in self._indexed(org, env, holder)
            if one.facts.agent == agent and one.facts.contact == contact
        ]
        mine.sort(key=lambda one: (one.at, one.facts.call), reverse=True)
        return [one.facts.call for one in mine[:limit]]

    async def ever_reached(self, org: str, contact: str) -> bool:
        """Whether this contact has ever reached the org, any world and any agent of it."""
        return any(one.facts.contact == contact for one in self._indexed(org))

    async def read(
        self, org: str, env: str, holder: str, agent: str, reader: str, contact: str, at: float
    ) -> None:
        """The reader's cursor on this thread, moved forward and never back."""
        where = (org, env, holder, agent, reader, contact)
        self._read[where] = max(at, self._read.get(where, 0.0))

    # The postgres statement reads `max(entry.ts)` off the log's own rows; here the entries are
    # the list, so the last one's ts is the same fact — ephemerals included, as the statement
    # counts them, because a call that is still saying something is still a call.
    def _still_open(self) -> list[StillOpen]:
        """Every call with a head row that has not sealed, whatever org it is in."""
        return [
            StillOpen(
                call=call,
                agent=log.facts.agent,
                started_at=log.started_at or 0.0,
                last_at=log.entries[-1].ts if log.entries else (log.started_at or 0.0),
                spoken=log.facts.spoken,
            )
            for call, log in self._calls.items()
            if not log.sealed and log.facts is not None
        ]

    def _indexed(
        self, org: str, env: str | None = None, holder: str | None = None
    ) -> list[Indexed]:
        """Every call of the org with facts, in that world and corner when they are named."""
        rows = [
            Indexed(log.org, log.env, log.holder, log.started_at, not log.sealed, log.facts)
            for log in self._calls.values()
            if log.facts is not None
        ]
        return [one for one in rows if one.of(org, env, holder)]

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
    # Which tuning and which lexicon the call was built on, as the head row keeps them.
    config_version: int | None = None
    lexicon_version: int | None = None
    # When the call's first entry landed, as the head row's started_at, and what its entries said.
    started_at: float | None = None
    facts: CallFacts | None = None


# The one entry a sealed log takes: a verdict reached after the call was over (api/evals/judge.py).
RESCORED = "call.score"


def _page(log: _Log | None, after: int, limit: int) -> list[Entry]:
    """The slice above the cursor; an unknown log reads as empty, never as an error."""
    if log is None:
        return []
    start = max(after, 0)
    return log.entries[start : start + limit]
