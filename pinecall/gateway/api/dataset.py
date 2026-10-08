"""The dataset doors: a call kept as a case or read as a golden, the inbox, a case decided."""

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.evals import calibration, dataset
from pinecall.evals.calibration import Label, Where
from pinecall.evals.dataset import Promoted
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, EvalsKey, GatewayDep, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.tenancy.keys import check_agent
from pinecall.wire.frames import Entry
from pinecall.wire.rest.evals import (
    CaseDecision,
    CaseStatus,
    EvalCase,
    EvalCaseList,
    Expect,
    Golden,
    PromoteCaseRequest,
)
from pinecall.wire.scores import CallScore

router = APIRouter()


STILL_GOING = "call {call} is still going: a case is made of a call that ended"


class GoldenQuery(BaseModel):
    """What a call read as a golden is named, and the seq it is cut at."""

    name: str | None = Field(default=None, min_length=1)
    from_seq: int = Field(default=0, ge=0)


# The case is the org's, played in the sandbox whichever world its call ran in; the key must be
# one that reads the call, as a replay's does. An expect left empty is the one the call's broken
# verdicts give.
@router.post("/v1/evals/cases", response_model_exclude_unset=True)
async def promote_case(
    body: PromoteCaseRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> EvalCase:
    """A finished call's caller lines kept as a case of the org's dataset."""
    entries, agent, env, version = await _finished(gateway, key, scope, body.call)
    score = _score_in(entries)
    expect = body.expect
    if expect == Expect() and score is not None:
        expect = dataset.expect_of(score.judges, entries)
    golden = dataset.golden_of(entries, body.name, expect)
    promoting = Promoted(
        org=key.org,
        env=env,
        name=body.name,
        author=key.bearer.key.subject or key.bearer.key.key_id,
        held_out=body.held_out,
        broke=[] if score is None else dataset.broke_of(score),
        source_version=version,
    )
    return await dataset.promoted(gateway.connections.pool, golden, agent, promoting)


# What `pinecall runs promote` writes to test/candidates: the same golden a case would hold,
# derived here once, kept nowhere.
@router.get("/v1/calls/{call}/golden", response_model_exclude_unset=True)
async def golden_of_call(
    call: Annotated[str, Path()],
    key: EvalsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[GoldenQuery, Query()],
) -> Golden:
    """The golden a finished call makes from a seq on, its expect from its broken verdicts."""
    entries, _, _, _ = await _finished(gateway, key, scope, call)
    score = _score_in(entries)
    expect = Expect() if score is None else dataset.expect_of(score.judges, entries)
    cut = dataset.cut_at(entries, query.from_seq)
    return dataset.golden_of(cut, query.name or call, expect)


@router.get("/v1/evals/cases", response_model_exclude_unset=True)
async def list_cases(
    key: EvalsKey,
    gateway: GatewayDep,
    agent: Annotated[str | None, Query()] = None,
    status: Annotated[CaseStatus | None, Query()] = None,
) -> EvalCaseList:
    """The org's cases, one agent's or every one, the pending first; how many wait, of how many."""
    if agent is not None:
        check_agent(key.bearer, agent)
    pool = gateway.connections.pool
    return EvalCaseList(
        cases=await dataset.listed(pool, key.org, agent, status),
        pending=await dataset.waiting(pool, key.org, agent),
        pending_at_most=dataset.PENDING_AT_MOST,
    )


# The judge called wrong is a calibration label in the call's own world, so the key must read the
# call as the calibration door's does.
@router.patch("/v1/evals/cases/{id}", response_model_exclude_unset=True)
async def decide_case(
    case: Annotated[str, Path(alias="id")],
    body: CaseDecision,
    key: EvalsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
) -> EvalCase:
    """Approve, dismiss, hold out or mark a case as kept in the repository."""
    pool = gateway.connections.pool
    kept = await dataset.found(pool, key.org, case)
    check_agent(key.bearer, kept.agent)
    dataset.check_decision(kept, body)
    author = key.bearer.key.subject or key.bearer.key.key_id
    if body.judge_was_wrong is not None:
        reader = _deps.Reader(acting=key, scope=scope)
        await _deps.check_readable(gateway, reader, kept.source_call)
        where = Where(org=key.org, env=kept.source_env, agent=kept.agent)
        label = Label(
            call=kept.source_call,
            judge=body.judge_was_wrong,
            held=True,
            author=author,
            note=body.note,
        )
        await calibration.labelled(pool, where, label)
    return await dataset.decided(pool, key.org, case, body, author)


@router.delete("/v1/evals/cases/{id}", status_code=204)
async def forget_case(
    case: Annotated[str, Path(alias="id")], key: EvalsKey, gateway: GatewayDep
) -> None:
    """Forget one of the org's cases; another org's, or nobody's, is the same 404."""
    await dataset.forgotten(gateway.connections.pool, key.org, case)


async def _finished(
    gateway: Gateway, key: Acting, scope: Scope, call: str
) -> tuple[list[Entry], str, Env, int | None]:
    """A call the key reads and that ended: its log, its agent, its world, its settings version."""
    await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), call)
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    entries = await gateway.logs.store.whole(call)
    if kept is None or kept.scope is None or not entries:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    check_agent(key.bearer, kept.agent)
    if not any(entry.type == "call.ended" for entry in entries):
        raise Conflict(STILL_GOING.format(call=call))
    return list(entries), kept.agent, kept.scope.env, kept.versions.config


def _score_in(entries: list[Entry]) -> CallScore | None:
    """The call's last call.score, the verdict a judge may write again after the seal."""
    found = [entry for entry in entries if entry.type == "call.score"]
    return None if not found else CallScore.model_validate(found[-1].data)
