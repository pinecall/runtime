"""The org's own settings: whether its calls are judged at hang-up, and the ceiling per call."""

from fastapi import APIRouter

from pinecall.gateway._deps import (
    CallsKey,
    GatewayDep,
    UsageKey,
)
from pinecall.tenancy import orgs
from pinecall.wire.rest.agents import (
    JudgingRequest,
    JudgingSettings,
)

router = APIRouter()


# Per org, not per world: judging is billed to the org across both.
@router.get("/v1/org/judging")
async def get_judging(key: CallsKey, gateway: GatewayDep) -> JudgingSettings:
    """Whether hang-up judging is on, and its ceiling per call."""
    on = await orgs.judged(gateway.connections.pool, key.org)
    return JudgingSettings(on=on, ceiling_eur=gateway.connections.settings.judge_ceiling_eur)


@router.put("/v1/org/judging")
async def put_judging(body: JudgingRequest, key: UsageKey, gateway: GatewayDep) -> JudgingSettings:
    """Hang-up judging on or off, from the next call."""
    await orgs.set_judging(gateway.connections.pool, key.org, on=body.on)
    return JudgingSettings(on=body.on, ceiling_eur=gateway.connections.settings.judge_ceiling_eur)
