"""The calibration doors: a person labels what a judge should have said; each judge is judged."""

from typing import Annotated

from fastapi import APIRouter, Query

from pinecall.domain.errors import NotFound
from pinecall.evals import calibration
from pinecall.evals.calibration import Label, Where
from pinecall.gateway import _deps
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep
from pinecall.log import queries
from pinecall.tenancy.keys import check_agent
from pinecall.wire.rest.evals import Calibration, JudgeAgreement, LabelRequest

router = APIRouter()


# The key must read the call, as a replay's does; the label is kept in the call's own world.
@router.post("/v1/evals/calibration", status_code=204)
async def label_call(
    body: LabelRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> None:
    """Keep what one judge should have answered on a finished call, replacing the last label."""
    await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), body.call)
    kept = await queries.scope_of_call(gateway.connections.pool, body.call)
    if kept is None or kept.scope is None:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=body.call))
    check_agent(key.bearer, kept.agent)
    where = Where(org=key.org, env=kept.scope.env, agent=kept.agent)
    author = key.bearer.key.subject or key.bearer.key.key_id
    label = Label(call=body.call, judge=body.judge, held=body.held, author=author, note=body.note)
    await calibration.labelled(gateway.connections.pool, where, label)


@router.get("/v1/evals/calibration")
async def read_calibration(
    key: EvalsKey, gateway: GatewayDep, agent: Annotated[str | None, Query()] = None
) -> Calibration:
    """Each judge's agreement with the labels on the key's world's calls, one agent's or all."""
    if agent is not None:
        check_agent(key.bearer, agent)
    judged = await calibration.agreement(gateway.connections.pool, key.org, key.env, agent)
    return Calibration(
        judges=[
            JudgeAgreement(
                judge=item.judge,
                labelled=item.labelled,
                compared=item.compared,
                agreed=item.agreed,
                rate=item.rate,
                trusted=item.trusted,
            )
            for item in judged
        ],
        labels_to_judge=calibration.LABELS_TO_JUDGE,
        agrees_at_least=calibration.AGREES_AT_LEAST,
    )
