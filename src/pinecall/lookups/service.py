"""The gateway's Lookup and Rememberer: memory and the knowledge base, on the call's own log."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pinecall.knowledge import Knowledge
from pinecall.log.entry import Entry
from pinecall.log.logs import CallLog
from pinecall.log.writers import Logs
from pinecall.lookups.answers import found, recalled
from pinecall.lookups.entries import a_recall, a_retrieval, a_skip
from pinecall.memory import DEFAULT_FACTS_PER_TURN, Memory, Spoken
from pinecall.types import (
    AgentConfig,
    CallContext,
    Chunk,
    Counting,
    PlatformTool,
    ProviderKeys,
    Quotas,
)
from pinecall_protocol import WireModel, encode
from pinecall_protocol.defs import MemoryOp
from pinecall_protocol.events import MemoryOps

logger = logging.getLogger(__name__)

# The two turn entries memory reads back at hang-up, and the role each speaks as.
SPOKEN: dict[str, Literal["user", "agent"]] = {"turn.user": "user", "turn.agent": "agent"}


# What a lookup needs to know of a call, and nothing more: the process's live table answers it
# (api/_live.py) and the service never sees the table, the registry or the app state.
@dataclass(frozen=True)
class OpenCall:
    """One call as a lookup sees it: whose org, whose corner, how it arrived, what it declared."""

    org: str
    context: CallContext
    config: AgentConfig
    # Whose corner of the world this call is being served in: the developer in the sandbox, and
    # nobody in production. What it recalls and what it searches are that corner's, so a test call
    # on one laptop never reads the facts another laptop's test call planted. See 0021.
    holder: str | None = None


class Calls(Protocol):
    """Who knows which calls are open here: the door that opened each one said all of this."""

    def the_call(self, call: str) -> OpenCall | None:
        """The call by id, or None when this gateway is not serving it."""
        ...


# Whose provider keys a hang-up's model call runs on, read at that moment and never held: the
# same read the worker's provider-keys door makes. The vault is orgs/, which lookups may not
# import, so the read is handed in as a function.
type KeysOf = Callable[[str], Awaitable[ProviderKeys]]

# What the org's quotas say, and whether it may keep one more fact — both live in orgs/, which
# lookups may not import, so they arrive as functions exactly as the vault read above does. The
# runtime prices nothing: these two answer what a plan INCLUDES, never what it costs.
type QuotasOf = Callable[[str], Awaitable[Quotas]]
type MayRemember = Callable[[str, str, Counting], Awaitable[bool]]


# One per process. Nothing here is per call: the call arrives by id on every verb, and what the
# service knows of it is asked of `calls` each time. A gateway on a dev key has no Postgres and so
# no memory and no knowledge; it still runs every lookup, finds nothing, and writes no entry.
class Lookups:
    """The session's Lookup and Rememberer as the gateway implements them, in one object."""

    def __init__(
        self,
        memory: Memory | None,
        knowledge: Knowledge | None,
        logs: Logs,
        calls: Calls,
        keys_of: KeysOf,
        quotas_of: QuotasOf,
        may_remember: MayRemember,
    ) -> None:
        self._memory = memory
        self._knowledge = knowledge
        self._logs = logs
        self._calls = calls
        self._keys_of = keys_of
        self._quotas_of = quotas_of
        self._may_remember = may_remember

    # ── the Lookup ──────────────────────────────────────────────────────────────

    # The answer is a JSON object and never prose: it reaches the model inside a tool_result,
    # which is where everything from outside the conversation goes and the only place it goes.
    # A lookup that fails answers with nothing found and writes an error entry naming why — TEI
    # down, a table missing — because a caller is on the line and a turn must not stop for it.
    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        """What the tool found, in the shape its tool result carries; its entry on the log."""
        opened = self._calls.the_call(call)
        log = self._logs.opened(call)
        if opened is None or log is None:
            return _nothing_found(tool)
        query = str(input.get("query") or "")
        # A class searching for itself (`this.knowledge.search(q, {k})`) may say how many; a turn's
        # own search takes each base's k.
        asked = input.get("k")
        k = asked if isinstance(asked, int) and asked > 0 else None
        started = time.perf_counter()
        try:
            # One read per lookup: a plan that switched a feature off answers with nothing, and
            # pays no embedder to find that out.
            quotas = await self._quotas_of(opened.org)
            if tool == "recall":
                return await self._recalled(opened, log, quotas, query, speech_id, started)
            return await self._searched(opened, log, quotas, query, speech_id, started, k)
        except Exception as failed:  # noqa: BLE001 — a lookup must never break a reply
            logger.warning("call %s: %s did not run", opened.context.call, tool, exc_info=True)
            await _written(log, "error", a_skip(tool, str(failed) or type(failed).__name__))
            return _nothing_found(tool)

    async def _recalled(
        self,
        opened: OpenCall,
        log: CallLog,
        quotas: Quotas,
        query: str,
        speech_id: str | None,
        started: float,
    ) -> Mapping[str, Any]:
        """The contact's facts that answer the caller's words, and memory.ops on the log."""
        contact = opened.context.remembered_as
        # A plan without memory is not a failure, so nothing is written: an empty memory.ops
        # would say a search ran and found nothing, and no search ran. Nothing is embedded
        # either — a lookup that cannot use its answer must not pay for one.
        if self._memory is None or contact is None or quotas.switched_off("memory_facts"):
            return recalled(())
        facts = await self._memory.recall(
            opened.org,
            opened.context.route.env,
            opened.holder,
            contact,
            query,
            k=DEFAULT_FACTS_PER_TURN,
        )
        took_ms = _since(started)
        await _written(log, "memory.ops", a_recall(contact, query, facts, took_ms, speech_id))
        return recalled(facts)

    async def _searched(
        self,
        opened: OpenCall,
        log: CallLog,
        quotas: Quotas,
        query: str,
        speech_id: str | None,
        started: float,
        k: int | None = None,
    ) -> Mapping[str, Any]:
        """The best chunks of every base the agent reads, each under its k; docs.sources logged."""
        bases = opened.config.bases
        # An org that may keep no chunks has none to find, and docs.sources with no sources would
        # tell the grounded judge the base was searched and answered nothing. Nothing is written,
        # and the query is not embedded to search a base that cannot exist.
        if self._knowledge is None or not bases or quotas.switched_off("knowledge_chunks"):
            return found(())
        # Several bases, one answer: each searched under its own k, the scores already read
        # against each base's best, the best of all of them first, cut to the largest k asked.
        chunks: list[Chunk] = []
        for docs in bases:
            chunks.extend(
                await self._knowledge.search(
                    opened.org,
                    opened.context.route.env,
                    opened.holder,
                    docs.base,
                    query,
                    k=k or docs.k,
                    min_score=docs.min_score,
                )
            )
        chunks.sort(key=lambda chunk: chunk.score, reverse=True)
        chunks = chunks[: k or max(docs.k for docs in bases)]
        took_ms = _since(started)
        await _written(log, "docs.sources", a_retrieval(query, chunks, took_ms, speech_id))
        return found(chunks)

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
        # Before the model, never after: asking it is the whole cost of a hang-up, and an org that
        # may keep no more facts must not pay for an extraction nothing will store. The gate wrote
        # credits.exhausted into the agent's log; what this call did — nothing — goes on the call's.
        if not await self._may_remember(opened.org, opened.context.route.agent, self._memory.kept):
            await _written(log, "memory.ops", _nothing_kept(contact))
            return 0
        turns = _spoken(await log.whole())
        ops = await self._memory.remember(
            opened.org,
            opened.context.route.env,
            opened.holder,
            contact,
            turns,
            channel=opened.context.channel,
            at=datetime.now(UTC),
            policy=policy,
            llm=opened.config.llm,
            keys=await self._keys_of(opened.org),
            call=call,
            tools=opened.config.tools,
        )
        await _written(log, "memory.ops", MemoryOps(ops=list(ops)))
        return len(ops)


def _nothing_found(tool: PlatformTool) -> Mapping[str, Any]:
    """The empty answer in the tool's own shape: the key is always there, the list is empty."""
    return recalled(()) if tool == "recall" else found(())


# The same shape a hang-up that DID remember writes, carrying what was written: nothing. The
# reason is one entry away, in the agent's log, where every quota refusal has always been.
def _nothing_kept(contact: str) -> MemoryOps:
    """The memory.ops of a hang-up whose org may keep no more facts: an op that wrote none."""
    return MemoryOps(ops=[MemoryOp(op="remember", contact=contact, facts=[], took_ms=0.0)])


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
