"""The four-digit codes a caller keys to tie their call to a browser page, issued per agent."""

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass

from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow

from pinecall.domain.errors import NotAvailable, QuotaExhausted
from pinecall.domain.names import Env
from pinecall.log.logs import Logs
from pinecall.postgres.pool import Pool
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

# Where a claim wakes the page waiting on the code, whichever gateway it waits on.
TAKEN_CHANNEL = "code:{agent}:{code}"

# Expired codes leave with the one statement that finds them: two gateways never close one twice.
EXPIRED = """
delete from caller_codes where agent = %(agent)s and expires_at <= %(now)s
returning code, claimed
"""

LIVE = "select count(*) as live from caller_codes where agent = %(agent)s"

KEPT = """
insert into caller_codes (agent, code, env, log, expires_at)
values (%(agent)s, %(code)s, %(env)s, %(log)s, %(expires_at)s)
"""

FOUND = """
select agent, code, env, log, expires_at, claimed from caller_codes
where agent = %(agent)s and code = %(code)s
"""

# Taken by one call: the second finds it claimed and gets nothing.
TAKEN = """
update caller_codes set claimed = %(call)s
where agent = %(agent)s and code = %(code)s and env = %(env)s and claimed is null
  and expires_at > %(now)s
returning agent, code, env, log, expires_at, claimed
"""


@dataclass(frozen=True)
class IssuedCode:
    """A code a page shows, waiting for the call that keys it, and the call once one did."""

    code: str
    env: Env
    agent: str
    expires_at: float
    log: Projection
    claimed: str | None = None


# Kept in Postgres, so any gateway issues, claims and answers a code; written on each agent's log
# as code.issued and code.claimed (no call: it expired). A code is the agent's, whatever the world.
class Codes:
    """Every agent's live caller codes, and a waiting page's wake-up for each."""

    def __init__(self, logs: Logs) -> None:
        """Codes kept in the logs' store, their wake-ups on the logs' signal."""
        self.logs = logs
        self.pool: Pool = logs.store.pool

    async def issue(self, env: Env, agent: str, ttl_s: float, log: Projection) -> IssuedCode:
        """A new code for the agent, written on its log; at most fifty live at once."""
        await self._swept(agent, time.time())
        async with self.pool.connection() as connection:
            row = await (await connection.execute(LIVE, {"agent": agent})).fetchone()
        live = 0 if row is None else int(row["live"])
        if live >= LIVE_PER_AGENT:
            raise QuotaExhausted(TOO_MANY.format(agent=agent, live=live))
        issued = await self._kept(env, agent, time.time() + ttl_s, log)
        data = CodeIssued(code=issued.code, env=env, expires_at=issued.expires_at, log=log)
        await self.logs.agent(agent).append(ISSUED, data.written())
        return issued

    async def status_of(self, env: Env, agent: str, code: str) -> IssuedCode | None:
        """The code as it stands, once more after it expired; None for one nobody issued."""
        issued = await self._found(agent, code)
        await self._swept(agent, time.time())
        return issued if issued is not None and issued.env == env else None

    # Listened for before the code is read again, so a claim between the two still wakes it.
    async def waited(self, issued: IssuedCode, within_s: float) -> IssuedCode:
        """Wait up to that long for a call to key the code, and say how it stands."""
        channel = TAKEN_CHANNEL.format(agent=issued.agent, code=issued.code)
        if within_s > 0:
            with contextlib.suppress(NotAvailable):
                taken = await self.logs.relay.signal.subscribe(channel)
                try:
                    now = await self._found(issued.agent, issued.code)
                    if now is not None and now.claimed is None:
                        with contextlib.suppress(TimeoutError, StopAsyncIteration):
                            await asyncio.wait_for(anext(taken), within_s)
                finally:
                    taken.close()
        return await self._found(issued.agent, issued.code) or issued

    async def claim(self, env: Env, agent: str, code: str, call: str) -> IssuedCode | None:
        """Give the code to the call that keyed it; None when it is nobody's, dead or taken."""
        await self._swept(agent, time.time())
        wanted = {"agent": agent, "code": code, "env": env, "call": call, "now": time.time()}
        async with self.pool.connection() as connection:
            row = await (await connection.execute(TAKEN, wanted)).fetchone()
        if row is None:
            return None
        await self.logs.agent(agent).append(CLAIMED, CodeClaimed(code=code, call=call).written())
        self.logs.relay.signal.publish(TAKEN_CHANNEL.format(agent=agent, code=code), b"taken")
        return _issued(row)

    async def _kept(self, env: Env, agent: str, expires_at: float, log: Projection) -> IssuedCode:
        while True:
            issued = IssuedCode(_drawn(), env, agent, expires_at, log)
            row = {"agent": agent, "code": issued.code, "env": env, "log": log}
            try:
                async with self.pool.connection() as connection:
                    await connection.execute(KEPT, {**row, "expires_at": expires_at})
            except UniqueViolation:
                continue
            return issued

    async def _found(self, agent: str, code: str) -> IssuedCode | None:
        async with self.pool.connection() as connection:
            row = await (await connection.execute(FOUND, {"agent": agent, "code": code})).fetchone()
        return None if row is None else _issued(row)

    # Swept as codes are asked for, not by a loop of its own.
    async def _swept(self, agent: str, now: float) -> None:
        async with self.pool.connection() as connection:
            expired = await connection.execute(EXPIRED, {"agent": agent, "now": now})
            rows = await expired.fetchall()
        for row in rows:
            if row["claimed"] is None:
                closed = CodeClaimed(code=str(row["code"]), call=None).written()
                await self.logs.agent(agent).append(CLAIMED, closed)


def _issued(row: DictRow) -> IssuedCode:
    return IssuedCode(
        code=str(row["code"]),
        env="sandbox" if row["env"] == "sandbox" else "production",
        agent=str(row["agent"]),
        expires_at=float(row["expires_at"]),
        log="tenant" if row["log"] == "tenant" else "public",
        claimed=row["claimed"],
    )


def _drawn() -> str:
    return f"{secrets.randbelow(10**DIGITS):0{DIGITS}d}"
