"""A Memory and a Knowledge that answer from a script, and the one call this suite serves."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from pinecall.filling import Filling, MayRemember, OpenCall, QuotasOf
from pinecall.knowledge import Base
from pinecall.log.logs import CallLog
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.memory import Spoken
from pinecall.orgs.admission import Admission
from pinecall.orgs.meter import Meter
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import MemoryVault, keys_brought_by
from pinecall.types import (
    AgentConfig,
    CallContext,
    Channel,
    Chunk,
    Contact,
    Docs,
    Fact,
    KnowledgeFile,
    MemoryPolicy,
    Model,
    Org,
    ProviderKeys,
    Quotas,
    Route,
)
from pinecall_protocol import encode
from pinecall_protocol.defs import MemoryOp
from pinecall_protocol.events import AgentTurnEnded, UserTurnEnded
from pinecall_protocol.metrics import AgentTurnMetrics, UserTurnMetrics

ORG = "clinica"
CALL = "call_filled"
AGENT = "clinica-norte"
THE_NUMBER = "+34600000001"
LEARNED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def a_fact(id: str, text: str, score: float = 1.0) -> Fact:
    """One fact of the caller, as recall would score it."""
    return Fact(
        id=id,
        contact=THE_NUMBER,
        text=text,
        category="preference",
        source=None,
        valid_from=LEARNED,
        invalidated_at=None,
        score=score,
    )


def a_chunk(id: str, heading: str, text: str, score: float = 1.0) -> Chunk:
    """One chunk of the clinic's base, as search would score it."""
    return Chunk(id=id, base="clinica", path="tarifas.md", heading=heading, text=text, score=score)


@dataclass
class ScriptedMemory:
    """A Memory answering the facts it was given, and remembering every question it was asked."""

    answers: list[Fact] = field(default_factory=list[Fact])
    failing: Exception | None = None
    recalled: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    remembered: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    async def recall(
        self,
        org: str,
        contact: str,
        query: str,
        *,
        kinds: Sequence[str] = (),
        k: int = 6,
        as_of: datetime | None = None,  # noqa: ARG002 — the Protocol's shape
    ) -> list[Fact]:
        if self.failing is not None:
            raise self.failing
        self.recalled.append(
            {"org": org, "contact": contact, "query": query, "kinds": tuple(kinds), "k": k}
        )
        return list(self.answers)[:k]

    async def remember(
        self,
        org: str,
        contact: str,
        turns: Sequence[Spoken],
        *,
        channel: Channel,
        at: datetime,  # noqa: ARG002 — the Protocol's shape
        policy: MemoryPolicy,
        llm: Model | None,
        keys: ProviderKeys,
        call: str | None = None,
    ) -> list[MemoryOp]:
        self.remembered.append(
            {
                "org": org,
                "contact": contact,
                "turns": list(turns),
                "channel": channel,
                "policy": policy,
                "llm": llm,
                "keys": dict(keys),
                "call": call,
            }
        )
        return [MemoryOp(op="remember", contact=contact, facts=[], took_ms=1.0)]

    async def forget(self, org: str, contact: str) -> int:  # noqa: ARG002
        gone, self.answers = len(self.answers), []
        return gone

    async def history(self, org: str, contact: str) -> list[Fact]:  # noqa: ARG002
        if self.failing is not None:
            raise self.failing
        return list(self.answers)

    async def kept(self, org: str) -> int:  # noqa: ARG002
        """What this org holds: the facts this fake was given, as a real table would count them."""
        return len(self.answers)


@dataclass
class ScriptedKnowledge:
    """A Knowledge answering the chunks it was given, and remembering what it was searched for."""

    answers: list[Chunk] = field(default_factory=list[Chunk])
    failing: Exception | None = None
    searched: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    pushed: dict[str, list[KnowledgeFile]] = field(default_factory=dict[str, list[KnowledgeFile]])

    async def put(self, org: str, base: str, files: Sequence[KnowledgeFile]) -> int:  # noqa: ARG002
        if self.failing is not None:
            raise self.failing
        self.pushed[base] = list(files)
        return len(files) * 2

    async def bases(self, org: str) -> list[Base]:  # noqa: ARG002
        return [
            Base(base=base, chunks=len(files) * 2, pushed_at=LEARNED)
            for base, files in sorted(self.pushed.items())
        ]

    async def drop(self, org: str, base: str) -> bool:  # noqa: ARG002
        return self.pushed.pop(base, None) is not None

    async def kept(self, org: str, besides: str | None = None) -> int:  # noqa: ARG002
        """Every base's chunks but the one a push is about to replace, on this fake's own cut."""
        return sum(
            self.how_many_chunks(files) for base, files in self.pushed.items() if base != besides
        )

    def how_many_chunks(self, files: Sequence[KnowledgeFile]) -> int:
        """This fake cuts every file into two, so a push of one file is two chunks."""
        return len(files) * 2

    async def search(
        self,
        org: str,
        base: str,
        query: str,
        *,
        k: int = 8,
        min_score: float | None = None,
    ) -> list[Chunk]:
        if self.failing is not None:
            raise self.failing
        self.searched.append(
            {"org": org, "base": base, "query": query, "k": k, "min_score": min_score}
        )
        return list(self.answers)[:k]


# What a Filling is handed about the org's plan: the quotas table, and the gate that reads it.
# A suite that sets no quota gets the mechanism with no numbers in it, which is a self-hosted box.
def a_plan(logs: Logs, orgs: MemoryOrgs) -> tuple[QuotasOf, MayRemember]:
    """The two questions Filling asks orgs/, over this test's own tenants."""
    # The meter is where minutes and messages are counted from; may_remember never asks it.
    return orgs.quotas_of, Admission(orgs, Meter(MemoryStore()), logs).may_remember


def the_tenants() -> MemoryOrgs:
    """The one org these tests serve, with no limits until a test writes some."""
    return MemoryOrgs([Org(id=ORG, slug=ORG, name=ORG)])


class OneCall:
    """The Calls table of a process serving exactly one call, or none."""

    def __init__(self, opened: OpenCall | None) -> None:
        self._opened = opened

    def the_call(self, call: str) -> OpenCall | None:
        return self._opened if self._opened and self._opened.context.call == call else None


def a_context(channel: Channel = "phone", contact: Contact | None = None) -> CallContext:
    """A call on the clinic's door: a number on the phone, a visitor id on the web."""
    number = None if channel == "web" else THE_NUMBER
    return CallContext(
        call=CALL,
        channel=channel,
        direction="inbound",
        caller=THE_NUMBER if channel != "web" else "visitor_9",
        route=Route(org=ORG, agent=AGENT, channel=channel, number=number),
        today=LEARNED.date(),
        contact=contact,
    )


def a_config(
    docs: Docs | None = Docs(base="clinica", k=8),  # noqa: B008 — frozen
    memory: MemoryPolicy | None = MemoryPolicy(remember=("preference",)),  # noqa: B008 — frozen
    knowledge: KnowledgeFile | None = None,
) -> AgentConfig:
    """The clinic as it declares itself for these tests: a base to search, a policy to keep."""
    return AgentConfig(
        slug=AGENT,
        channels=frozenset({"phone", "web"}),
        llm=Model(provider="anthropic", model="claude-haiku"),
        docs=docs,
        memory=memory,
        knowledge=knowledge,
    )


@dataclass
class Served:
    """One Filling over one served call, with the log it writes into readable by the test."""

    filling: Filling
    store: MemoryStore
    log: CallLog
    memory: ScriptedMemory
    knowledge: ScriptedKnowledge
    orgs: MemoryOrgs

    async def limited(self, quotas: Quotas) -> None:
        """What the org's plan includes, set the way the operator's door sets it: whole."""
        await self.orgs.set_quotas(ORG, quotas)

    async def written(self, type: str) -> list[dict[str, Any]]:
        """Every entry of that type on the call's log, as data."""
        return [dict(entry.data) for entry in await self.store.since(CALL) if entry.type == type]

    async def refusals(self) -> list[dict[str, Any]]:
        """Every credits.exhausted on the AGENT's log, which is where a quota refusal lands."""
        return [
            dict(entry.data)
            for entry in await self.store.agent_since(AGENT)
            if entry.type == "credits.exhausted"
        ]

    async def heard(self, text: str, speech_id: str = "sp_1") -> None:
        """The caller's turn on the log, as the session writes it."""
        turn = UserTurnEnded(speech_id=speech_id, text=text, metrics=UserTurnMetrics())
        await self.log.append("turn.user", encode(turn))

    async def said(self, text: str, speech_id: str = "sp_1") -> None:
        """The agent's turn on the log, as the session writes it."""
        turn = AgentTurnEnded(
            speech_id=speech_id, text=text, interrupted=False, metrics=AgentTurnMetrics()
        )
        await self.log.append("turn.agent", encode(turn))


def a_served_call(
    context: CallContext | None = None,
    config: AgentConfig | None = None,
    *,
    memory: ScriptedMemory | None = None,
    knowledge: ScriptedKnowledge | None = None,
    vault: MemoryVault | None = None,
) -> Served:
    """The service over one call this process is serving, its log open on an in-memory store."""
    store = MemoryStore()
    logs = Logs(store)
    log = logs.writing(CALL, AGENT)
    opened = OpenCall(org=ORG, context=context or a_context(), config=config or a_config())
    memory = memory or ScriptedMemory()
    knowledge = knowledge or ScriptedKnowledge()
    orgs = the_tenants()
    filling = Filling(
        memory,
        knowledge,
        logs,
        OneCall(opened),
        partial(keys_brought_by, vault),
        *a_plan(logs, orgs),
    )
    return Served(filling, store, log, memory, knowledge, orgs)
