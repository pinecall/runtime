"""A version of an agent's settings on a share of its calls: set, cleared, which calls take it."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool

# A share is a whole percent of the scope's calls.
ALL_CALLS = 100


STANDING = """
SELECT holder, version, share, author, note, set_at FROM agent_canaries
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND agent = %(agent)s
ORDER BY set_at DESC, id DESC
LIMIT 1
"""


# Only ever added: a version and a share set it, a row with neither clears it.
# Stamped by the log's clock, as a call's head is, so which came first is one clock's word.
PUT = """
INSERT INTO agent_canaries (org, env, holder, agent, version, share, author, note, set_at)
VALUES (%(org)s, %(env)s, %(holder)s, %(agent)s, %(version)s, %(share)s, %(author)s, %(note)s,
        to_timestamp(%(at)s))
"""


# The canary each level of the call's scope had when the call opened.
STOOD_AT = """
SELECT DISTINCT ON (holder) holder, version, share FROM agent_canaries
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND agent = %(agent)s
  AND set_at <= to_timestamp(%(at)s)
ORDER BY holder, set_at DESC, id DESC
"""


@dataclass(frozen=True)
class Canary:
    """Which version takes a share of the calls, and how many in a hundred."""

    version: int
    share: int


@dataclass(frozen=True)
class CanarySet:
    """A canary as written: the version and share, or None to clear it, who, when and why."""

    canary: Canary | None
    author: str
    at: float
    note: str | None = None


@dataclass(frozen=True)
class Current:
    """The canary a scope stands on now: whose, which, who set it, why and when."""

    holder: str
    canary: Canary
    author: str
    note: str | None
    set_at: datetime


# sha256, not hash(): the same call lands in the same place in every process and every release,
# so a call asked for again (a gateway that restarted) keeps its version.
def bucket_of(call: str) -> int:
    """The call's place among a hundred; a canary of share N takes the places under N."""
    return int(sha256(call.encode()).hexdigest()[:8], 16) % ALL_CALLS


async def current(pool: Pool, scope: Scope, agent: str) -> Current | None:
    """The scope's own canary for the agent, or None when none stands or it was cleared."""
    params = {"org": scope.org, "env": scope.env, "holder": scope.holder, "agent": agent}
    async with pool.connection() as connection:
        row = await (await connection.execute(STANDING, params)).fetchone()
    if row is None or row["version"] is None:
        return None
    return Current(
        holder=row["holder"],
        canary=Canary(version=row["version"], share=row["share"]),
        author=row["author"],
        note=row["note"],
        set_at=row["set_at"],
    )


async def put(pool: Pool, scope: Scope, agent: str, written: CanarySet) -> None:
    """Set the scope's canary for the agent, or clear it."""
    wanted = written.canary
    params = {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "agent": agent,
        "version": None if wanted is None else wanted.version,
        "share": None if wanted is None else wanted.share,
        "author": written.author,
        "note": written.note,
        "at": written.at,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, params)


# A call runs the canary's version only when it was picked for it: the rest never run it.
async def ran_the_canary(
    pool: Pool, scope: Scope, agent: str, version: int | None, at: float
) -> bool:
    """Whether a call that opened then on this version was one of a canary's share."""
    if version is None:
        return False
    params = {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "agent": agent,
        "at": at,
    }
    async with pool.connection() as connection:
        rows = await (await connection.execute(STOOD_AT, params)).fetchall()
    return any(row["version"] == version and row["share"] for row in rows)
