"""The codes a page shows a caller: issued, waiting, claimed — the agent's log is the table."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, replace

from pinecall._exceptions import PinecallError
from pinecall.log.store import Store
from pinecall.log.writers import Logs
from pinecall.types import Env
from pinecall_protocol import encode
from pinecall_protocol.defs import Projection
from pinecall_protocol.events import CodeClaimed, CodeIssued

ISSUED = "code.issued"
CLAIMED = "code.claimed"

# docs/protocol/codes.md: four digits a person reads off a page and keys on a phone, and no more
# of them live at once per agent than a page could ever need — past that it is somebody minting.
DIGITS = 4
LIVE_PER_AGENT = 50

TOO_MANY = (
    "agent {agent} already has {live} codes waiting for a call: let one be claimed or expire "
    "before issuing another"
)


class TooManyCodes(PinecallError):
    """The agent holds as many live codes as it may; str() is the sentence."""


@dataclass(frozen=True)
class Issued:
    """One code: whose agent, in which world, until when, what its page reads, and who took it."""

    code: str
    env: Env
    agent: str
    expires_at: float
    log: Projection
    claimed: str | None = None

    def expired(self, now: float) -> bool:
        """Whether its time is up, claimed or not."""
        return now >= self.expires_at


# The log is the table, as it is for WhatsApp's waiting room (api/whatsapp/waiting.py): a code is
# code.issued on the agent's own log and closed by code.claimed — with the call that took it, or
# null when its time ran out — so a restart loses none. This process keeps the live ones in memory,
# loaded from the log at start, so a page asking every few seconds costs no read. A code is unique
# per AGENT, whichever world issued it: code.claimed carries no world. And it names no org: a slug
# is one org's (api/agents/registry.py), the door that issues one asks the key's own routes, and
# the call that claims one is that very agent's.
class Codes:
    """Every live code of every agent, and the page waiting on each one."""

    def __init__(self, logs: Logs) -> None:
        self._logs = logs
        self._issued: dict[tuple[str, str], Issued] = {}
        self._taken: dict[tuple[str, str], asyncio.Event] = {}

    async def issue(self, env: Env, agent: str, ttl_s: float, log: Projection) -> Issued:
        """A code nobody else of this agent holds, written on its log, or TooManyCodes."""
        await self._swept(time.time())
        held = {code for (holder, code) in self._issued if holder == agent}
        if len(held) >= LIVE_PER_AGENT:
            raise TooManyCodes(TOO_MANY.format(agent=agent, live=len(held)))
        code = _drawn()
        while code in held:
            code = _drawn()
        issued = Issued(code, env, agent, time.time() + ttl_s, log)
        self._kept(issued)
        said = CodeIssued(code=code, env=env, expires_at=issued.expires_at, log=log)
        await self._logs.writing_agent(agent).append(ISSUED, encode(said))
        return issued

    async def standing(self, env: Env, agent: str, code: str) -> Issued | None:
        """The code as it stands — expired ones included, once — or None for one nobody issued."""
        issued = self._issued.get((agent, code))
        await self._swept(time.time())
        return issued if issued is not None and issued.env == env else None

    async def waited(self, issued: Issued, within_s: float) -> Issued:
        """The same code once a call claims it, or as it stands after `within_s` without one."""
        taken = self._taken.get((issued.agent, issued.code))
        if taken is not None and within_s > 0:
            try:
                await asyncio.wait_for(taken.wait(), within_s)
            except TimeoutError:
                pass
        return self._issued.get((issued.agent, issued.code), issued)

    # Marked before the log is written, so two claims of one code in the same instant take it once.
    async def claim(self, env: Env, agent: str, code: str, call: str) -> Issued | None:
        """The code, now this call's; None when nobody issued it, it expired, or a call has it."""
        await self._swept(time.time())
        issued = self._issued.get((agent, code))
        if issued is None or issued.env != env or issued.claimed is not None:
            return None
        claimed = replace(issued, claimed=call)
        self._issued[(agent, code)] = claimed
        said = CodeClaimed(code=code, call=call)
        await self._logs.writing_agent(agent).append(CLAIMED, encode(said))
        taken = self._taken.get((agent, code))
        if taken is not None:
            taken.set()
        return claimed

    async def loaded(self, store: Store) -> None:
        """Every code still open on any agent's log, as a process that starts finds it."""
        after = 0
        while page := await store.across([ISSUED, CLAIMED], after=after):
            for row in page:
                agent, data = row.entry.agent, row.entry.data
                if row.entry.type == ISSUED:
                    said = CodeIssued.model_validate(data)
                    self._kept(Issued(said.code, said.env, agent, said.expires_at, said.log))
                else:
                    self._closed(agent, CodeClaimed.model_validate(data))
            after = page[-1].position

    # Lazily, inside every ask, rather than a loop of its own: a code nobody asks about can wait to
    # be closed on the log until somebody does, and the next process to start closes it the same.
    async def _swept(self, now: float) -> None:
        """Every code whose time is up let go; an unclaimed one closed on the log with no call."""
        # Let go of all of them before the first write: a sweep that awaits is a sweep another ask
        # can start beside, and a code closed twice on the log is a code closed once too often.
        expired = [one for one in self._issued.values() if one.expired(now)]
        for issued in expired:
            self._issued.pop((issued.agent, issued.code), None)
            self._taken.pop((issued.agent, issued.code), None)
        for issued in expired:
            if issued.claimed is None:
                said = CodeClaimed(code=issued.code, call=None)
                await self._logs.writing_agent(issued.agent).append(CLAIMED, encode(said))

    def _kept(self, issued: Issued) -> None:
        """Held in memory, with the event its page waits on."""
        self._issued[(issued.agent, issued.code)] = issued
        self._taken[(issued.agent, issued.code)] = asyncio.Event()

    def _closed(self, agent: str, said: CodeClaimed) -> None:
        """code.claimed as the log says it: taken by a call, or let go with none."""
        issued = self._issued.get((agent, said.code))
        if issued is None:
            return
        if said.call is None:
            self._issued.pop((agent, said.code), None)
            self._taken.pop((agent, said.code), None)
        else:
            self._issued[(agent, said.code)] = replace(issued, claimed=said.call)


def _drawn() -> str:
    """Four digits at random, leading zeros kept."""
    return f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"
