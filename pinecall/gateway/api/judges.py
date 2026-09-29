"""The judge doors: the org's questions for every agent's calls, and one agent's for its own."""

import re

from fastapi import APIRouter

from pinecall.domain.agent import AgentJudge
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import THE_ORGS_OWN
from pinecall.gateway._deps import Acting, EvalsKey, GatewayDep
from pinecall.postgres.pool import Pool
from pinecall.tenancy import judges
from pinecall.tenancy.judges import StoredJudge
from pinecall.wire.rest.evals import JudgeList, JudgeRequest, JudgeRow

router = APIRouter()


# The name call.score gives the verdict: lower-case words joined by hyphens.
A_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


NOT_A_NAME = (
    "{name!r} is not a judge's name: lower-case words joined by hyphens, like offers-next-slot"
)


# One list for both worlds, as the agent's personas are.
@router.get("/v1/org/judges")
async def list_org_judges(key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """The org's judges, asked of every agent's calls, by name."""
    return await _listed(gateway.connections.pool, key.org, THE_ORGS_OWN)


@router.put("/v1/org/judges/{name}")
async def put_org_judge(
    name: str, body: JudgeRequest, key: EvalsKey, gateway: GatewayDep
) -> JudgeList:
    """Write one of the org's judges whole; the org's list after it."""
    return await _written(gateway.connections.pool, key, THE_ORGS_OWN, name, body)


@router.delete("/v1/org/judges/{name}")
async def drop_org_judge(name: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Forget one of the org's judges; the org's list after it, 404 for a name nobody wrote."""
    return await _dropped(gateway.connections.pool, key.org, THE_ORGS_OWN, name)


@router.get("/v1/agents/{slug}/judges")
async def list_judges(slug: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """The agent's own judges, by name; the org's are at /v1/org/judges."""
    return await _listed(gateway.connections.pool, key.org, slug)


@router.put("/v1/agents/{slug}/judges/{name}")
async def put_judge(
    slug: str, name: str, body: JudgeRequest, key: EvalsKey, gateway: GatewayDep
) -> JudgeList:
    """Write one of the agent's own judges whole; the agent's list after it."""
    return await _written(gateway.connections.pool, key, slug, name, body)


@router.delete("/v1/agents/{slug}/judges/{name}")
async def drop_judge(slug: str, name: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Forget one of the agent's own judges; its list after it, 404 for a name nobody wrote."""
    return await _dropped(gateway.connections.pool, key.org, slug, name)


async def _written(pool: Pool, key: Acting, agent: str, name: str, body: JudgeRequest) -> JudgeList:
    if not A_NAME.match(name):
        raise DeclarationRefused(NOT_A_NAME.format(name=name))
    written = AgentJudge(name=name, question=body.question, runs_on=body.runs_on)
    bearer = key.bearer.key
    await judges.put_judge(pool, key.org, agent, written, author=bearer.subject or bearer.key_id)
    return await _listed(pool, key.org, agent)


async def _dropped(pool: Pool, org: str, agent: str, name: str) -> JudgeList:
    await judges.drop_judge(pool, org, agent, name)
    return await _listed(pool, org, agent)


async def _listed(pool: Pool, org: str, agent: str) -> JudgeList:
    return JudgeList(
        judges=[_row_of(stored) for stored in await judges.judges_of(pool, org, agent)]
    )


def _row_of(stored: StoredJudge) -> JudgeRow:
    judge = stored.judge
    return JudgeRow(
        name=judge.name,
        question=judge.question,
        runs_on=judge.runs_on,
        author=stored.author,
        set_at=stored.set_at.timestamp(),
    )
