"""The dataset doors: a real call promoted to a case, the org's cases listed, one forgotten."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from pinecall.domain.errors import Conflict, NotFound
from pinecall.evals import dataset
from pinecall.evals.dataset import Promoted
from pinecall.gateway import _deps
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.log import queries
from pinecall.tenancy.keys import check_agent
from pinecall.wire.rest.evals import EvalCase, EvalCaseList, PromoteCaseRequest

router = APIRouter()


STILL_GOING = "call {call} is still going: a case is made of a call that ended"


# The case is the org's, played in the sandbox whichever world its call ran in; the key must be
# one that reads the call, as a replay's does.
@router.post("/v1/evals/cases", response_model_exclude_unset=True)
async def promote_case(
    body: PromoteCaseRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> EvalCase:
    """A finished call's caller lines kept as a case of the org's dataset."""
    await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), body.call)
    kept = await queries.scope_of_call(gateway.connections.pool, body.call)
    entries = await gateway.logs.store.whole(body.call)
    if kept is None or kept.scope is None or not entries:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=body.call))
    check_agent(key.bearer, kept.agent)
    if not any(entry.type == "call.ended" for entry in entries):
        raise Conflict(STILL_GOING.format(call=body.call))
    golden = dataset.golden_of(entries, body.name, body.expect)
    promoting = Promoted(
        org=key.org,
        env=kept.scope.env,
        name=body.name,
        author=key.bearer.key.subject or key.bearer.key.key_id,
        held_out=body.held_out,
    )
    return await dataset.promoted(gateway.connections.pool, golden, kept.agent, promoting)


@router.get("/v1/evals/cases", response_model_exclude_unset=True)
async def list_cases(
    key: EvalsKey, gateway: GatewayDep, agent: Annotated[str | None, Query()] = None
) -> EvalCaseList:
    """The org's cases, one agent's or every one, by agent and name."""
    if agent is not None:
        check_agent(key.bearer, agent)
    return EvalCaseList(cases=await dataset.listed(gateway.connections.pool, key.org, agent))


@router.delete("/v1/evals/cases/{id}", status_code=204)
async def forget_case(
    case: Annotated[str, Path(alias="id")], key: EvalsKey, gateway: GatewayDep
) -> None:
    """Forget one of the org's cases; another org's, or nobody's, is the same 404."""
    await dataset.forgotten(gateway.connections.pool, key.org, case)
