"""Who read what: a person's or the operator's read of a call or a number, once an hour at most."""

from dataclasses import dataclass

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.wire.rest.calls import ReadKind, ReadRow

# The same reader reading the same thing again within the hour is the same read; an org erased
# has no access log left to write to (a traceback still finds its calls' records).
RECORD = """
INSERT INTO reads (org, env, subject, what, reader)
SELECT %(org)s, %(env)s, %(subject)s, %(what)s, %(reader)s
WHERE EXISTS (SELECT 1 FROM orgs WHERE id = %(org)s) AND NOT EXISTS (
    SELECT 1 FROM reads
    WHERE org = %(org)s AND subject = %(subject)s AND what = %(what)s AND reader = %(reader)s
      AND env IS NOT DISTINCT FROM %(env)s AND at > now() - interval '1 hour'
)
"""

OF_ORG = """
SELECT subject, what, env, reader, at FROM reads
WHERE org = %(org)s AND (%(subject)s::text IS NULL OR subject = %(subject)s)
ORDER BY at DESC, id DESC
LIMIT %(limit)s
"""

# Who reads, or erases, on the box's own behalf: off the box, or through its own doors.
OPERATOR = "operator"

A_PAGE = 200


@dataclass(frozen=True)
class Read:
    """One read: the call or the number, what of it, and who."""

    subject: str
    what: ReadKind
    reader: str


async def record(pool: Pool, scope: Scope, read: Read) -> None:
    """Write the read down, unless the same reader read the same thing within the hour."""
    params = {"org": scope.org, "env": scope.env, **vars(read)}
    async with pool.connection() as connection:
        await connection.execute(RECORD, params)


async def of_org(
    pool: Pool, org: str, *, subject: str | None = None, limit: int = A_PAGE
) -> list[ReadRow]:
    """The org's reads, newest first, of one call or number when named."""
    params = {"org": org, "subject": subject, "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_ORG, params)).fetchall()
    return [ReadRow.model_validate({**row, "at": row["at"].timestamp()}) for row in rows]
