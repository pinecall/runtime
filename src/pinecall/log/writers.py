"""The logs one process is writing, by id, and how a reader finds the fanout a writer is on."""

from __future__ import annotations

from collections.abc import Collection

from pinecall.log.entry import Entry
from pinecall.log.fanout import Fanout
from pinecall.log.logs import AgentLog, CallLog
from pinecall.log.store import Store

# What an org's own stream carries: the moments a floor changes shape, and nothing said on a
# call. An agent held or let go, a call arriving, up, and over — each already an entry of some
# log; the feed is those same entries, tapped as they are written, never a second record.
ORG_EVENTS: frozenset[str] = frozenset(
    {
        "agent.registered",
        "agent.detached",
        "call.ringing",
        "call.dialing",
        "call.started",
        "call.ended",
    }
)


# What is shared between a writer and its readers is the FANOUT, never the log object: a reader
# that opens before the writer exists — a console holding an agent's log open before the app
# connects, which is the normal order — must be subscribed to the very fanout the writer will
# publish on. Handing it a detached log with a fanout of its own is a stream that stays silent.
class Logs:
    """Which calls and agents this gateway writes, by id, and how a reader finds their fanout."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._calls: dict[str, CallLog] = {}
        self._call_fanouts: dict[str, Fanout] = {}
        self._agent_fanouts: dict[str, Fanout] = {}
        self._feeds: dict[str, Fanout] = {}

    def writing(self, call: str, agent: str) -> CallLog:
        """The log a session appends to: kept, so `sealed` is one fact and readers hear it live."""
        log = self._calls.get(call)
        if log is None:
            fanout = _fanout_of(self._call_fanouts, call)
            log = self._calls[call] = CallLog(
                self._store, agent, call, fanout=fanout, tap=self._fed
            )
        return log

    def opened(self, call: str) -> CallLog | None:
        """The log this process is writing for that call, or None when it opened none."""
        return self._calls.get(call)

    def reading(self, call: str) -> CallLog:
        """A reader's view: the store for the past, the shared fanout for what comes next."""
        self._prune(self._call_fanouts, writing=self._calls)
        return CallLog(self._store, "", call, fanout=_fanout_of(self._call_fanouts, call))

    def writing_agent(self, slug: str) -> AgentLog:
        """The agent's own log, on the fanout every reader of that agent is already holding."""
        return AgentLog(
            self._store, slug, fanout=_fanout_of(self._agent_fanouts, slug), tap=self._fed
        )

    def reading_agent(self, slug: str) -> AgentLog:
        """The same log from the reader's side: one fanout per slug, whichever side asked first."""
        self._prune(self._agent_fanouts)
        return AgentLog(self._store, slug, fanout=_fanout_of(self._agent_fanouts, slug))

    # Whose a log is lives on the store's head row, and the two doors that decide it — a call
    # opening under a key, a slug registering under one — reach the store through this table.
    async def owned(
        self,
        call: str | None,
        agent: str,
        org: str,
        env: str | None = None,
        holder: str | None = None,
    ) -> None:
        """This log is the org's — and a call's, one corner's: the world it was opened in and
        whose. The agent a key registered has no corner: one log per slug, whatever the world."""
        await self._store.owned(call, agent, org, env, holder)

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Whose log this is, or None when no org has claimed it."""
        return await self._store.owner(call, agent)

    def forget(self, call: str) -> None:
        """Drop a sealed call: its readers have finished and nothing more will be appended."""
        self._calls.pop(call, None)
        self._call_fanouts.pop(call, None)

    # ── the org's own stream ────────────────────────────────────────────────────

    def feed(self, org: str) -> Fanout:
        """What the org's readers subscribe to: every ORG_EVENTS entry of every log the org owns."""
        self._prune(self._feeds)
        return _fanout_of(self._feeds, org)

    # Whose log it is lives on the head row: asked per tapped entry, which is rare — a floor
    # changes shape a few times a minute, a call says a hundred things.
    async def _fed(self, entry: Entry) -> None:
        """Publish an entry about the org onto the org's feed, when somebody is reading it."""
        if entry.type not in ORG_EVENTS:
            return
        org = await self._store.owner(entry.call, entry.agent)
        feed = None if org is None else self._feeds.get(org)
        if feed is not None:
            feed.publish(entry)

    # A reader must never be able to grow the tables without bound: an id nobody writes and nobody
    # reads any more is dropped the next time a reader asks for anything, so a scan of made-up ids
    # leaves nothing behind but the one being asked for.
    def _prune(self, fanouts: dict[str, Fanout], writing: Collection[str] = ()) -> None:
        """Drop every fanout that has no readers and no writer holding it."""
        idle = [
            id
            for id, fanout in fanouts.items()
            if fanout.readers == 0 and id not in (writing or {})
        ]
        for id in idle:
            del fanouts[id]


def _fanout_of(fanouts: dict[str, Fanout], id: str) -> Fanout:
    """The fanout for this id, created the first time either side asks for it."""
    fanout = fanouts.get(id)
    if fanout is None:
        fanout = fanouts[id] = Fanout()
    return fanout


# ── how a route asks for it ─────────────────────────────────────────────────────
