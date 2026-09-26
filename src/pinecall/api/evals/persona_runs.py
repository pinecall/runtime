"""GET /v1/personas/{name}/runs: what one synthetic caller has done, off the call index."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.deps import CallIndexDep, EvalsKeyDep
from pinecall.api.evals.personas import PersonasDep
from pinecall.auth.corner import corner_of
from pinecall.log.store.index import PersonaRun
from pinecall.orgs.personas import NOBODY
from pinecall_protocol.rest import PersonaRun as Row
from pinecall_protocol.rest import PersonaRunList

router = APIRouter()

# The same screenful the sessions list shows, and for the same reason: it is a screen, not a page
# of the store.
A_SCREENFUL = 20

LIMIT = Query(ge=1, le=200)
BEFORE = Query(description="the `next` of the page before: the list continues below that call")


# The pane on the right of the Personas screen. It is a list of CALLS, so it is answered the way
# every list of calls is — off the call index, never by folding a log — and it is cut to the key's
# own corner, as the sessions list is: a developer's sandbox runs are theirs, production's are
# production's. The caller itself is the org's, so a name nobody wrote is a 404 before anything
# is counted, rather than an empty page that looks like a caller who never called.
@router.get("/v1/personas/{name}/runs")
async def runs(
    name: str,
    key: EvalsKeyDep,
    kept: PersonasDep,
    index: CallIndexDep,
    limit: Annotated[int, LIMIT] = A_SCREENFUL,
    before: Annotated[str | None, BEFORE] = None,
) -> PersonaRunList:
    """Every simulation this caller has run, newest first: when, what came of it, and which call."""
    if await kept.named(key.org, name) is None:
        raise HTTPException(404, NOBODY.format(name=name))
    whose = corner_of(key)
    found = await index.runs_of_persona(
        whose.org, whose.env, whose.holder or "", name, before, limit
    )
    return PersonaRunList(
        runs=[_a_row(run) for run in found.runs], total=found.total, next=found.next
    )


def _a_row(run: PersonaRun) -> Row:
    """One run as the pane draws it: the call's own facts, and the clock the list is ordered by."""
    facts = run.facts
    return Row.model_validate(
        {
            "call": facts.call,
            "agent": facts.agent,
            "started_at": run.started_at,
            "ended_at": facts.ended_at,
            "turns": run.turns,
            "end_reason": facts.end_reason,
            "outcome": facts.outcome,
            "cost_eur": facts.cost_eur,
            "score": facts.score_row,
        }
    )
