"""The gateway's Filler and Rememberer: memory and the knowledge base, on the call's own log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pinecall.filling.entries import a_recall, a_retrieval, a_skip
from pinecall.knowledge import Knowledge, chunks_as_text
from pinecall.log.entry import Entry
from pinecall.log.logs import CallLog
from pinecall.log.writers import Logs
from pinecall.memory import DEFAULT_FACTS_PER_TURN, Memory, Spoken, facts_as_text
from pinecall.types import AgentConfig, CallContext, Marker, ProviderKeys
from pinecall_protocol import WireModel, encode
from pinecall_protocol.events import MemoryOps

logger = logging.getLogger(__name__)

# The two turn entries memory reads back at hang-up, and the role each speaks as.
SPOKEN: dict[str, Literal["user", "agent"]] = {"turn.user": "user", "turn.agent": "agent"}


# What a fill needs to know of a call, and nothing more: the process's live table answers it
# (api/_live.py) and the service never sees the table, the registry or the app state.
@dataclass(frozen=True)
class OpenCall:
    """One call as a fill sees it: whose org, how it arrived, what its agent declared."""

    org: str
    context: CallContext
    config: AgentConfig


class Calls(Protocol):
    """Who knows which calls are open here: the door that opened each one said all of this."""

    def the_call(self, call: str) -> OpenCall | None:
        """The call by id, or None when this gateway is not serving it."""
        ...


# Whose provider keys a hang-up's model call runs on, read at that moment and never held: the
# same read the worker's provider-keys door makes. The vault is orgs/, which filling may not
# import, so the read is handed in as a function.
type KeysOf = Callable[[str], Awaitable[ProviderKeys]]


# One per process. Nothing here is per call: the call arrives by id on every verb, and what the
# service knows of it is asked of `calls` each time. A gateway on a dev key has no Postgres and so
# no memory and no knowledge; it still answers every fill, with nothing, and writes no entry.
class Filling:
    """The session's Filler and Rememberer as the gateway implements them, in one object."""

    def __init__(
        self,
        memory: Memory | None,
        knowledge: Knowledge | None,
        logs: Logs,
        calls: Calls,
        keys_of: KeysOf,
    ) -> None:
        self._memory = memory
        self._knowledge = knowledge
        self._logs = logs
        self._calls = calls
        self._keys_of = keys_of

    # ── the Filler ──────────────────────────────────────────────────────────────

    # Every marker at once: each is its own two index scans, and the turn's budget covers the
    # slowest of them, not their sum. A marker that fails is filled with nothing and its error
    # entry names why — TEI down, a table missing — while the others are still filled.
    async def fill(
        self, call: str, query: str, markers: Sequence[Marker], speech_id: str | None
    ) -> Mapping[str, str]:
        """The text each marker line becomes for this turn, keyed by the line as written."""
        opened = self._calls.the_call(call)
        log = self._logs.opened(call)
        if opened is None or log is None:
            return {}
        texts = await asyncio.gather(
            *(self._one(opened, log, marker, query, speech_id) for marker in markers)
        )
        return dict(zip((marker.line for marker in markers), texts, strict=True))

    async def _one(
        self, opened: OpenCall, log: CallLog, marker: Marker, query: str, speech_id: str | None
    ) -> str:
        """One marker's text, its entry written; nothing and a skip entry when it could not be."""
        started = time.perf_counter()
        try:
            if marker.name == "memory":
                return await self._recalled(opened, log, marker, query, speech_id, started)
            if marker.name == "retrieved":
                return await self._retrieved(opened, log, marker, query, speech_id, started)
        except Exception as failed:  # noqa: BLE001 — a fill must never break a reply
            logger.warning(
                "call %s: %s not filled", opened.context.call, marker.name, exc_info=True
            )
            await _written(log, "error", a_skip(marker.name, str(failed) or type(failed).__name__))
            return ""
        # A knowledge marker is the session's to fill, once, from the file it was handed; asked
        # here anyway, the answer is the same text, so the door is whole.
        return opened.config.knowledge.text if opened.config.knowledge else ""

    async def _recalled(
        self,
        opened: OpenCall,
        log: CallLog,
        marker: Marker,
        query: str,
        speech_id: str | None,
        started: float,
    ) -> str:
        """The contact's facts under the marker's ask, and memory.ops on the log."""
        contact = opened.context.remembered_as
        if self._memory is None or contact is None:
            return ""
        ask = marker.ask
        facts = await self._memory.recall(
            opened.org, contact, query, kinds=ask.kinds, k=ask.limit or DEFAULT_FACTS_PER_TURN
        )
        if ask.min_score is not None:
            facts = [fact for fact in facts if fact.score >= ask.min_score]
        took_ms = _since(started)
        await _written(log, "memory.ops", a_recall(contact, query, facts, took_ms, speech_id))
        return facts_as_text(facts)

    # The marker's own ask outranks the declaration: `<Retrieved k={2}/>` in the view is the
    # tenant narrowing what the class said for this one place, not contradicting it.
    async def _retrieved(
        self,
        opened: OpenCall,
        log: CallLog,
        marker: Marker,
        query: str,
        speech_id: str | None,
        started: float,
    ) -> str:
        """The best chunks of the agent's base under the marker's ask; docs.sources on the log."""
        docs = opened.config.docs
        if self._knowledge is None or docs is None:
            return ""
        ask = marker.ask
        chunks = await self._knowledge.search(
            opened.org,
            docs.base,
            query,
            k=ask.limit or docs.k,
            min_score=docs.min_score if ask.min_score is None else ask.min_score,
        )
        took_ms = _since(started)
        await _written(log, "docs.sources", a_retrieval(query, chunks, took_ms, speech_id))
        return chunks_as_text(chunks)

    # ── the Rememberer ──────────────────────────────────────────────────────────

    # After call.ended and before call.summary, so every turn is on the log this reads back. The
    # org's own model and keys, exactly as the session that just spoke was built: the vault is read
    # now, so a key rotated during the call is the key the hang-up runs on.
    async def remember(self, call: str) -> int:
        """What the call taught about the contact, written; how many ops memory answered."""
        opened = self._calls.the_call(call)
        log = self._logs.opened(call)
        if opened is None or log is None or self._memory is None:
            return 0
        contact = opened.context.remembered_as
        policy = opened.config.memory
        if contact is None or policy is None:
            return 0
        turns = _spoken(await log.whole())
        ops = await self._memory.remember(
            opened.org,
            contact,
            turns,
            channel=opened.context.channel,
            at=datetime.now(UTC),
            policy=policy,
            llm=opened.config.llm,
            keys=await self._keys_of(opened.org),
            call=call,
        )
        await _written(log, "memory.ops", MemoryOps(ops=list(ops)))
        return len(ops)


def _spoken(entries: Sequence[Entry]) -> list[Spoken]:
    """The call as the extractor reads it: the caller's words and the agent's, nothing else."""
    return [
        Spoken(role=SPOKEN[entry.type], text=str(entry.data.get("text") or ""))
        for entry in entries
        if entry.type in SPOKEN
    ]


async def _written(log: CallLog, type: str, event: WireModel) -> None:
    """One entry on the call's log, through the same door the session writes it by."""
    await log.append(type, encode(event))


def _since(started: float) -> float:
    """Milliseconds since a perf_counter reading."""
    return (time.perf_counter() - started) * 1000
