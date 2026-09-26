"""The org's own eval runs, out of a table every org's runs share: read ahead, then filtered."""

from __future__ import annotations

from pinecall.evals.run_store import EvalRun, Runs
from pinecall.log.store import Store

# How far the read-ahead below will go for one page: a box where this org ran nothing lately does
# not read its whole history to answer "the newest twenty of yours".
MOST_READ_AHEAD = 400


# The cut belongs AFTER the org filter, and used to come before it: `newest(limit)` took the box's
# newest runs whatever org they belong to, and what was left after the filter was whatever share
# of them happened to be this org's — `?limit=2` answered an empty list on a box where two other
# tenants had run last (`pinecall runs list --limit 2`, production, 2026-09-20). So this reads
# ahead, in pages, until it has `limit` of the org's or the table is exhausted.
async def runs_of_org(
    org: str, runs: Runs, store: Store, limit: int, since: float, agent: str | None
) -> list[EvalRun]:
    """The newest `limit` runs OF THIS ORG, newest first."""
    mine: list[EvalRun] = []
    read = 0
    while len(mine) < limit:
        asked = min(MOST_READ_AHEAD, max(limit * 2, limit + read))
        page = await runs.newest(asked, since, agent)
        for run in page[read:]:
            if await is_the_orgs(org, store, run.agent):
                mine.append(run)
                if len(mine) == limit:
                    break
        if len(page) <= read or len(page) < asked:
            break
        read = len(page)
    return mine


async def is_the_orgs(org: str, store: Store, agent: str) -> bool:
    """Whether this agent is the org's: the log's owner, or nobody's yet."""
    owner = await store.owner(None, agent)
    return owner is None or owner == org
