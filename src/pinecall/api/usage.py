"""GET /v1/ops/usage and GET /v1/usage: what an org consumed, folded off the log with a cursor."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from pinecall.api._deps import OrgsDep, StoreDep, UsageKeyDep
from pinecall.api._operator import an_operators_router
from pinecall.api.calls.sink import (
    PING,
    SSE,
    SSE_HEADERS,
    AcceptDep,
    an_sse_frame,
    wants_sse,
)
from pinecall.log.store import DEFAULT_LIMIT, Store
from pinecall.log.usage import METERED_TYPES, UsageRow, a_usage_row, totals_by_org

operator = an_operators_router()

# The tenant's own read of the same rows, cut to its org by its key: what the console's Usage
# screen draws. A page and never a stream — a person reads a total, a cloud follows a cursor.
router = APIRouter()

# No table, no aggregate: the rows ARE the log's call.summary and call.score entries, folded one
# by one, and the cursor is the position the store wrote them at. A cloud that bills reads this
# from the cursor it last saw and never asks twice for the same minute. docs/decisions/orgs.md.
AFTER = Query(0, ge=0, description="the cursor the last page ended at; 0 reads from the start")
OF_ORG = Query(None, description="only this org's rows, by id or slug; every org's when absent")

# The stream has no fanout to wait on — a summary lands in some call's log, not on a topic — so it
# asks the store again this often. Cheap: one indexed read that usually comes back empty.
POLL_S = 1.0


# response_model=None: one URL, two flavours — FastAPI cannot make a model out of "or a stream".
@operator.get("/usage", response_model=None)
async def usage(
    store: StoreDep,
    orgs: OrgsDep,
    accept: AcceptDep = None,
    after: int = AFTER,
    org: str | None = OF_ORG,
    limit: Annotated[int, Query(ge=1, le=DEFAULT_LIMIT)] = DEFAULT_LIMIT,
) -> StreamingResponse | dict[str, Any]:
    """The metered rows above the cursor, per org, with the cursor to resume from."""
    only = None if org is None else (found.id if (found := await orgs.find(org)) else org)
    if wants_sse(accept):
        return StreamingResponse(_stream(store, after, only), media_type=SSE, headers=SSE_HEADERS)
    read, rows = await _a_page(store, after, limit, only)
    return {
        "rows": [asdict(row) for row in rows],
        "totals": {org: asdict(totals) for org, totals in totals_by_org(rows).items()},
        # The cursor moves past every row READ, filtered or not: a page whose every row was
        # another org's still makes progress, and an empty read is the end.
        "next": read[-1].cursor if read else None,
    }


@router.get("/v1/usage")
async def my_usage(
    key: UsageKeyDep,
    store: StoreDep,
    after: int = AFTER,
    limit: Annotated[int, Query(ge=1, le=DEFAULT_LIMIT)] = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """This org's metered rows above the cursor, with its totals and the cursor to resume from."""
    read, rows = await _a_page(store, after, limit, key.org)
    totals = totals_by_org(rows).get(key.org)
    return {
        "rows": [asdict(row) for row in rows],
        "totals": None if totals is None else asdict(totals),
        "next": read[-1].cursor if read else None,
    }


async def _a_page(
    store: Store, after: int, limit: int, only: str | None
) -> tuple[Sequence[UsageRow], Sequence[UsageRow]]:
    """One page: every row read, and the ones this asker keeps."""
    read = [a_usage_row(one) for one in await store.across(METERED_TYPES, after, limit)]
    return read, [row for row in read if only is None or row.org == only]


async def _stream(store: Store, after: int, only: str | None) -> AsyncIterator[str]:
    """Every row above the cursor, then each new one as the log grows, as SSE frames."""
    cursor = after
    while True:
        read, kept = await _a_page(store, cursor, DEFAULT_LIMIT, only)
        for row in kept:
            yield _frame(row)
        if read:
            cursor = read[-1].cursor
            continue
        yield PING
        await asyncio.sleep(POLL_S)


def _frame(row: UsageRow) -> str:
    """One row as SSE: its cursor as the id, so a reconnect resumes exactly where this left off."""
    return an_sse_frame("usage", asdict(row), id=row.cursor)
