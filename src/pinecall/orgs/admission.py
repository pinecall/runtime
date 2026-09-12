"""Admission: what an org may open, hold, keep and push right now, judged by its quotas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from pinecall._exceptions import PinecallError
from pinecall.log.writers import Logs
from pinecall.orgs.meter import Meter
from pinecall.orgs.table import Orgs
from pinecall.types import Counting, QuotaName, Quotas
from pinecall_protocol import encode
from pinecall_protocol.events import CreditsExhausted

# The event the refusal is written as, into the agent's own log — which is the org's log, since
# every agent is one org's — before the door says no. A tenant reading its log sees WHY the call
# never rang, in the protocol's own vocabulary, and the door's 429 says the same sentence.
EXHAUSTED = "credits.exhausted"

# The sentence, one shape for every quota: what ran out, how much was used, what the limit was.
REFUSED = "org {org} has used {used} of its {limit} {quota}: {event}"


@dataclass(frozen=True)
class Exhausted:
    """Which quota ran out for which org, and the two numbers that say so."""

    org: str
    quota: QuotaName
    used: float
    limit: int

    @override
    def __str__(self) -> str:
        used = int(self.used) if float(self.used).is_integer() else round(self.used, 2)
        return REFUSED.format(
            org=self.org, used=used, limit=self.limit, quota=self.quota, event=EXHAUSTED
        )


class QuotaExhausted(PinecallError):
    """The door said no because a quota ran out. `exhausted` says which; str() is the sentence."""

    def __init__(self, exhausted: Exhausted) -> None:
        super().__init__(str(exhausted))
        self.exhausted = exhausted


# The counts that are live facts — calls running now, agents held now — belong to the process's
# memory and are handed in by the door that has them, so this module reads the log and the quotas
# table and nothing of the gateway's live tables. See docs/decisions/orgs.md for the order.
class Admission:
    """The gate a call, a register, a hang-up and a push pass: the quotas against the facts."""

    def __init__(self, orgs: Orgs, meter: Meter, logs: Logs) -> None:
        self._orgs = orgs
        self._meter = meter
        self._logs = logs

    async def a_call(self, org: str, agent: str, running: int) -> None:
        """May this org open one more call for this agent, with `running` calls open already."""
        quotas = await self._orgs.quotas_of(org)
        await self._refuse_past(org, agent, quotas, "concurrent_calls", running)
        if quotas.minutes is None and quotas.messages is None:
            return
        totals = await self._meter.totals(org)
        await self._refuse_past(org, agent, quotas, "minutes", totals.minutes)
        await self._refuse_past(org, agent, quotas, "messages", totals.messages)

    async def an_agent(self, org: str, agent: str, holding: int) -> None:
        """May this org hold one more agent, with `holding` other agents held already."""
        quotas = await self._orgs.quotas_of(org)
        await self._refuse_past(org, agent, quotas, "agents", holding)

    async def a_managed_number(self, org: str, agent: str, bought: int) -> None:
        """May the box buy one more number for this org, with `bought` on its account already."""
        quotas = await self._orgs.quotas_of(org)
        await self._refuse_past(org, agent, quotas, "numbers", bought)

    # A hang-up refuses nobody: the call is over and nothing is waiting on an answer. So this one
    # says no by answering False, and the entry it writes is the whole of the refusal — the
    # gateway then writes what memory did (nothing) beside it, on the call's own log.
    # `keeping` is called only when a limit is set, the way the meter is: counting an org's facts
    # is a query over the table, and a box that limits nobody must not run one at every hang-up.
    async def may_remember(self, org: str, agent: str, keeping: Counting) -> bool:
        """Whether this org may keep one more fact about a contact, by what it keeps already."""
        quotas = await self._orgs.quotas_of(org)
        if quotas.memory_facts is None:
            return True
        kept = await keeping(org)
        limit = quotas.reached("memory_facts", kept)
        if limit is None:
            return True
        await self._written(org, agent, "memory_facts", used=kept, limit=limit)
        return False

    # The one refusal in this module that writes NO entry: a push names no agent and opens no
    # call, so there is no log of the org's to write it into, and an org-level log is the third
    # kind of log orgs.md declined to invent. The tenant is standing at the door reading the 429,
    # which is the difference — a call refused here never rings and has to be found afterwards.
    async def a_push(self, org: str, keeping: int) -> None:
        """May this org keep this many chunks across its bases once the push has replaced one."""
        quotas = await self._orgs.quotas_of(org)
        limit = quotas.exceeded("knowledge_chunks", keeping)
        if limit is None:
            return
        raise QuotaExhausted(
            Exhausted(org=org, quota="knowledge_chunks", used=keeping, limit=limit)
        )

    async def _refuse_past(
        self, org: str, agent: str, quotas: Quotas, quota: QuotaName, used: float
    ) -> None:
        """Write credits.exhausted to the agent's log and raise, when this quota is spent."""
        limit = quotas.reached(quota, used)
        if limit is None:
            return
        await self._written(org, agent, quota, used=used, limit=limit)
        raise QuotaExhausted(Exhausted(org=org, quota=quota, used=used, limit=limit))

    async def _written(
        self, org: str, agent: str, quota: QuotaName, *, used: float, limit: int
    ) -> None:
        """The refusal on the agent's own log, which is the org's: one home for every quota."""
        event = CreditsExhausted(org=org, quota=quota, used=used, limit=limit)
        await self._logs.writing_agent(agent).append(EXHAUSTED, encode(event))
