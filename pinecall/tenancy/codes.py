"""The four-digit codes a caller keys to tie their call to a browser page, issued per agent."""

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass, replace

from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.names import Env
from pinecall.log.logs import Logs
from pinecall.wire.events import CodeClaimed, CodeIssued
from pinecall.wire.parts import Projection

CLAIMED = "code.claimed"


TOO_MANY = (
    "agent {agent} already has {live} codes waiting for a call: let one be claimed or expire "
    "before issuing another"
)


# Four digits to key on a phone; the cap per agent stops a page minting them by the thousand.
DIGITS = 4


ISSUED = "code.issued"


LIVE_PER_AGENT = 50


@dataclass(frozen=True)
class IssuedCode:
    """A code a page shows, waiting for the call that keys it, and the call once one did."""

    code: str
    env: Env
    agent: str
    expires_at: float
    log: Projection
    claimed: str | None = None


# Kept on each agent's log as code.issued and code.claimed (no call: it expired), and held here
# so a page asking again costs no read. A code is the agent's, whatever the world.
class Codes:
    """Every agent's live caller codes, and a waiting page's wake-up for each."""

    def __init__(self, logs: Logs) -> None:
        """No code issued yet; `loaded` reads the ones a previous process left open."""
        self.logs = logs
        self.issued: dict[tuple[str, str], IssuedCode] = {}
        self.taken: dict[tuple[str, str], asyncio.Event] = {}

    async def issue(self, env: Env, agent: str, ttl_s: float, log: Projection) -> IssuedCode:
        """A new code for the agent, written on its log; at most fifty live at once."""
        await self._swept(time.time())
        found = {code for holder, code in self.issued if holder == agent}
        if len(found) >= LIVE_PER_AGENT:
            raise QuotaExhausted(TOO_MANY.format(agent=agent, live=len(found)))
        drawn = _drawn()
        while drawn in found:
            drawn = _drawn()
        issued = IssuedCode(drawn, env, agent, time.time() + ttl_s, log)
        self._kept(issued)
        data = CodeIssued(code=drawn, env=env, expires_at=issued.expires_at, log=log)
        await self.logs.agent(agent).append(ISSUED, data.written())
        return issued

    async def status_of(self, env: Env, agent: str, code: str) -> IssuedCode | None:
        """The code as it stands, once more after it expired; None for one nobody issued here."""
        issued = self.issued.get((agent, code))
        await self._swept(time.time())
        return issued if issued is not None and issued.env == env else None

    async def waited(self, issued: IssuedCode, within_s: float) -> IssuedCode:
        """Wait up to that long for a call to key the code, and say how it stands."""
        taken = self.taken.get((issued.agent, issued.code))
        if taken is not None and within_s > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(taken.wait(), within_s)
        return self.issued.get((issued.agent, issued.code), issued)

    # Marked before the log is written, so two calls keying the same code cannot both have it.
    async def claim(self, env: Env, agent: str, code: str, call: str) -> IssuedCode | None:
        """Give the code to the call that keyed it; None when it is nobody's, dead or taken."""
        await self._swept(time.time())
        issued = self.issued.get((agent, code))
        if issued is None or issued.env != env or issued.claimed is not None:
            return None
        claimed = replace(issued, claimed=call)
        self.issued[(agent, code)] = claimed
        await self.logs.agent(agent).append(CLAIMED, CodeClaimed(code=code, call=call).written())
        taken = self.taken.get((agent, code))
        if taken is not None:
            taken.set()
        return claimed

    async def loaded(self) -> None:
        """Read back every code still open on the agents' logs, as a new process starts."""
        after = 0
        while page := await self.logs.store.across([ISSUED, CLAIMED], after=after):
            for metered in page:
                entry = metered.entry
                if entry.type == ISSUED:
                    data = CodeIssued.model_validate(entry.data)
                    self._kept(
                        IssuedCode(data.code, data.env, entry.agent, data.expires_at, data.log)
                    )
                else:
                    self._closed(entry.agent, CodeClaimed.model_validate(entry.data))
            after = page[-1].position

    # Swept as codes are asked for, not by a loop of its own.
    async def _swept(self, now: float) -> None:
        # Taken out before the first await, so a sweep running beside this one logs none twice.
        expired = [value for value in self.issued.values() if now >= value.expires_at]
        for issued in expired:
            self.issued.pop((issued.agent, issued.code), None)
            self.taken.pop((issued.agent, issued.code), None)
        for issued in expired:
            if issued.claimed is None:
                closed = CodeClaimed(code=issued.code, call=None).written()
                await self.logs.agent(issued.agent).append(CLAIMED, closed)

    def _kept(self, issued: IssuedCode) -> None:
        self.issued[(issued.agent, issued.code)] = issued
        self.taken[(issued.agent, issued.code)] = asyncio.Event()

    def _closed(self, agent: str, code_claimed: CodeClaimed) -> None:
        issued = self.issued.get((agent, code_claimed.code))
        if issued is None:
            return
        if code_claimed.call is None:
            self.issued.pop((agent, code_claimed.code), None)
            self.taken.pop((agent, code_claimed.code), None)
            return
        self.issued[(agent, code_claimed.code)] = replace(issued, claimed=code_claimed.call)


def _drawn() -> str:
    return f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"
