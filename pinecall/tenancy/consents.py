"""Consent and the do-not-call list: facts about a number, never changed, the newest standing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import parse_e164
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.wire.rest.numbers import ConsentHistory, ConsentKind, ConsentRow, DoNotCall, OptedOut

type Standing = Literal["consented", "opted_out", "unknown"]

GIVE = """
INSERT INTO contact_consents (org, env, number, kind, source, text, evidence, given_by, call)
VALUES (%(org)s, %(env)s, %(number)s, %(kind)s, %(source)s, %(text)s, %(evidence)s,
        %(given_by)s, %(call)s)
"""

HISTORY = """
SELECT kind, source, text, evidence, given_by, call, given_at FROM contact_consents
WHERE org = %(org)s AND env = %(env)s AND number = %(number)s
ORDER BY given_at DESC, id DESC
"""

NEWEST = """
SELECT kind FROM contact_consents
WHERE org = %(org)s AND env = %(env)s AND number = %(number)s
ORDER BY given_at DESC, id DESC LIMIT 1
"""

# The numbers whose newest row is an opt-out, newest row first, after the row id a page ended on.
OPTED_OUT = """
SELECT id, number, given_at, source, given_by FROM (
    SELECT DISTINCT ON (number) number, kind, given_at, source, given_by, id
    FROM contact_consents WHERE org = %(org)s AND env = %(env)s
    ORDER BY number, given_at DESC, id DESC
) newest
WHERE kind = 'opt_out' AND (%(after)s::bigint IS NULL OR id < %(after)s)
ORDER BY id DESC
LIMIT %(limit)s
"""

OPTED_OUT_ON = """
SELECT EXISTS (SELECT 1 FROM contact_consents WHERE call = %(call)s AND kind = 'opt_out') AS opted
"""

NOT_A_CURSOR = "{after} is no cursor of this list: the last page said it"

A_PAGE = 200


@dataclass(frozen=True)
class Given:
    """A fact about a number, as it is written down."""

    kind: ConsentKind
    source: str
    given_by: str
    text: str | None = None
    evidence: str | None = None
    call: str | None = None


async def give(pool: Pool, scope: Scope, number: str, given: Given) -> None:
    """Write one fact about the number in the scope's world."""
    params = {"org": scope.org, "env": scope.env, "number": parse_e164(number), **vars(given)}
    async with pool.connection() as connection:
        await connection.execute(GIVE, params)


async def standing_of(pool: Pool, scope: Scope, number: str) -> Standing:
    """What stands for the number: its newest row's, or unknown when there is none."""
    params = {"org": scope.org, "env": scope.env, "number": number.strip()}
    async with pool.connection() as connection:
        row = await (await connection.execute(NEWEST, params)).fetchone()
    return _as_standing(None if row is None else str(row["kind"]))


async def history(pool: Pool, scope: Scope, number: str) -> ConsentHistory:
    """Every fact about the number, newest first, and what stands."""
    kept = parse_e164(number)
    params = {"org": scope.org, "env": scope.env, "number": kept}
    async with pool.connection() as connection:
        rows = await (await connection.execute(HISTORY, params)).fetchall()
    listed = [_row(row) for row in rows]
    return ConsentHistory(
        number=kept,
        standing=_as_standing(listed[0].kind if listed else None),
        rows=listed,
    )


async def do_not_call(
    pool: Pool, scope: Scope, *, after: str | None = None, limit: int = A_PAGE
) -> DoNotCall:
    """The world's do-not-call list, newest first, a page after the cursor."""
    params = {"org": scope.org, "env": scope.env, "after": _cursor(after), "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(OPTED_OUT, params)).fetchall()
    numbers = [
        OptedOut(
            number=str(row["number"]),
            since=_epoch(row["given_at"]),
            source=str(row["source"]),
            given_by=str(row["given_by"]),
        )
        for row in rows
    ]
    last = rows[-1] if len(rows) == limit else None
    return DoNotCall(numbers=numbers, next=None if last is None else str(last["id"]))


async def opted_out_on(pool: Pool, call: str) -> bool:
    """Whether a number went on the do-not-call list on this call."""
    async with pool.connection() as connection:
        row = await (await connection.execute(OPTED_OUT_ON, {"call": call})).fetchone()
    return row is not None and bool(row["opted"])


async def opt_out_many(
    pool: Pool, scope: Scope, numbers: list[str], given: Given
) -> tuple[int, list[str]]:
    """Put every number that is one on the list, in one transaction; how many, and the refused."""
    kept: list[str] = []
    refused: list[str] = []
    for number in numbers:
        try:
            kept.append(parse_e164(number))
        except DeclarationRefused:
            refused.append(number)
    rows = [
        {"org": scope.org, "env": scope.env, "number": number, **vars(given)} for number in kept
    ]
    async with (
        pool.connection() as connection,
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        await cursor.executemany(GIVE, rows)
    return len(kept), refused


def _as_standing(kind: str | None) -> Standing:
    if kind is None:
        return "unknown"
    return "opted_out" if kind == "opt_out" else "consented"


def _row(row: DictRow) -> ConsentRow:
    return ConsentRow.model_validate({**row, "given_at": _epoch(row["given_at"])})


def _epoch(at: datetime) -> float:
    return at.timestamp()


def _cursor(after: str | None) -> int | None:
    if after is None:
        return None
    try:
        return int(after)
    except ValueError:
        raise DeclarationRefused(NOT_A_CURSOR.format(after=after)) from None
