"""The judge doors: Pinecall's switched on or off, the org's own and an agent's, and a try."""

from collections.abc import Mapping

from fastapi import APIRouter

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.evals import catalog, judges
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, EvalsKey, GatewayDep, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.ending.seal import judged_call, panel_for
from pinecall.log import lists
from pinecall.postgres.pool import Pool
from pinecall.tenancy import judges as written
from pinecall.tenancy.judges import StoredJudge, Switched
from pinecall.wire.rest.evals import (
    JudgeList,
    JudgeRequest,
    JudgeRow,
    JudgeTried,
    JudgeTriedRow,
    JudgeTry,
)
from pinecall.wire.scores import CallScore

router = APIRouter()


PINECALL = "pinecall"
THE_ORG = "org"


ONLY_ON = (
    '{name} is one of Pinecall\'s judges: only whether it runs is written, as {{"on": true}} or '
    '{{"on": false}}; a question of your own takes a name of your own'
)
NOT_A_SWITCH = "{name} is a judge of your own: it runs while it is written, and DELETE stops it"
NOT_YOURS = (
    '{name} is one of Pinecall\'s judges and is never deleted: PUT {{"on": false}} turns it off'
)
ONE_OR_THE_OTHER = "a try names the agent's last calls or the calls themselves, never both"
NOTHING_TO_TRY = "a try names the agent's last calls (last) or the calls themselves (calls)"
NOT_FINISHED = "the call has not finished"


# One list for both worlds, as the agent's personas are.
@router.get("/v1/org/judges")
async def list_org_judges(key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Pinecall's judges as the org switched them, then the org's own, asked of every agent."""
    return await _listed(gateway.connections.pool, key.org, THE_ORGS_OWN)


@router.put("/v1/org/judges/{name}")
async def put_org_judge(
    name: str, body: JudgeRequest, key: EvalsKey, gateway: GatewayDep
) -> JudgeList:
    """One of Pinecall's switched for every agent, or one of the org's own written whole."""
    return await _written(gateway.connections.pool, key, THE_ORGS_OWN, name, body)


@router.delete("/v1/org/judges/{name}")
async def drop_org_judge(name: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Forget one of the org's own judges; 404 for a name nobody wrote, 409 for Pinecall's."""
    return await _dropped(gateway.connections.pool, key.org, THE_ORGS_OWN, name)


@router.get("/v1/agents/{slug}/judges")
async def list_judges(slug: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Every judge the agent's calls may meet: Pinecall's, the org's own, the agent's own."""
    return await _listed(gateway.connections.pool, key.org, slug)


@router.put("/v1/agents/{slug}/judges/{name}")
async def put_judge(
    slug: str, name: str, body: JudgeRequest, key: EvalsKey, gateway: GatewayDep
) -> JudgeList:
    """One of Pinecall's switched for this agent, or one of its own written whole."""
    return await _written(gateway.connections.pool, key, slug, name, body)


@router.delete("/v1/agents/{slug}/judges/{name}")
async def drop_judge(slug: str, name: str, key: EvalsKey, gateway: GatewayDep) -> JudgeList:
    """Forget one of the agent's own judges; 404 for a name nobody wrote, 409 for Pinecall's."""
    return await _dropped(gateway.connections.pool, key.org, slug, name)


# Asked as the seal asks it, on the model the seal would judge the call on, and written on no log.
@router.post("/v1/agents/{slug}/judges/try")
async def try_judge(
    slug: str, body: JudgeTry, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> JudgeTried:
    """Ask one judge, written or only in the body, of finished calls of the agent."""
    if body.last is not None and body.calls:
        raise DeclarationRefused(ONE_OR_THE_OTHER)
    pool = gateway.connections.pool
    spec = (
        _spec_of(body.name, body)
        if body.question.strip()
        else await _named(pool, key.org, slug, body.name)
    )
    calls = body.calls or await _newest(pool, scope, slug, body.last)
    tried = [await _tried(gateway, key, scope, call, spec) for call in calls]
    return JudgeTried(
        rows=[row for row, _ in tried],
        evals=sum(score.evals for _, score in tried),
        cost_usd=sum(score.judge_cost_usd or 0.0 for _, score in tried),
    )


async def _written(pool: Pool, key: Acting, agent: str, name: str, body: JudgeRequest) -> JudgeList:
    bearer = key.bearer.key
    author = bearer.subject or bearer.key_id
    if name in catalog.library():
        if body.on is None or body.question.strip():
            raise Conflict(ONLY_ON.format(name=name))
        await written.switch(pool, key.org, agent, Switched((name,), body.on, author))
    else:
        if body.on is not None:
            raise Conflict(NOT_A_SWITCH.format(name=name))
        await written.put_judge(pool, key.org, agent, _spec_of(name, body), author=author)
    return await _listed(pool, key.org, agent)


async def _dropped(pool: Pool, org: str, agent: str, name: str) -> JudgeList:
    if name in catalog.library():
        raise Conflict(NOT_YOURS.format(name=name))
    await written.drop_judge(pool, org, agent, name)
    return await _listed(pool, org, agent)


# An agent's list carries the org's own too, since its calls meet them; the org's ('') is the org's
# alone.
async def _listed(pool: Pool, org: str, agent: str) -> JudgeList:
    switches = await written.switches_for(pool, org, agent)
    rows = [_library_row(judge, switches) for judge in catalog.library().values()]
    rows += [_own_row(stored) for stored in await written.for_call(pool, org, agent)]
    return JudgeList(judges=rows)


async def _newest(pool: Pool, scope: Scope, agent: str, last: int | None) -> list[str]:
    if last is None:
        raise DeclarationRefused(NOTHING_TO_TRY)
    found = await lists.found(pool, scope, lists.ListFilters(agent=agent), limit=last)
    return found.calls


async def _tried(
    gateway: Gateway, key: Acting, scope: Scope, call: str, spec: JudgeSpec
) -> tuple[JudgeTriedRow, CallScore]:
    pool = gateway.connections.pool
    declared = await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), call)
    entries = await gateway.logs.store.whole(call)
    if not any(entry.type == "call.ended" for entry in entries):
        unfinished = CallScore(judges=[], judge_calls=0, not_judged=NOT_FINISHED)
        return JudgeTriedRow(call=call, judgment=None, not_judged=NOT_FINISHED), unfinished
    panel = judges.alone(spec, await panel_for(pool, key.org, entries, declared))
    score = await judged_call(gateway.connections, entries, declared, panel, scope)
    judgment = next(iter(score.judges), None)
    return JudgeTriedRow(call=call, judgment=judgment, not_judged=score.not_judged), score


def _spec_of(name: str, body: JudgeRequest) -> JudgeSpec:
    return JudgeSpec(
        name=name,
        question=body.question,
        answer=body.answer,
        choices=tuple(body.choices),
        on=body.when,
        trigger=body.trigger,
        reads_prompt="prompt" in body.reads,
        reads_evidence="evidence" in body.reads,
        reads_facts="facts" in body.reads,
    )


async def _named(pool: Pool, org: str, agent: str, name: str) -> JudgeSpec:
    found = catalog.library().get(name)
    if found is not None:
        return found.spec
    own = {stored.judge.name: stored.judge for stored in await written.for_call(pool, org, agent)}
    if name not in own:
        raise NotFound(written.NOBODY.format(whose=agent, name=name))
    return own[name]


def _library_row(judge: catalog.Written, switches: Mapping[str, bool]) -> JudgeRow:
    return JudgeRow.model_validate(
        {
            **_spec_fields(judge.spec),
            "owner": PINECALL,
            "on": switches.get(judge.spec.name, judge.on_by_default),
            "summary": judge.summary,
            "version": judge.version,
        }
    )


def _own_row(stored: StoredJudge) -> JudgeRow:
    return JudgeRow.model_validate(
        {
            **_spec_fields(stored.judge),
            "owner": stored.agent or THE_ORG,
            "on": True,
            "author": stored.author,
            "set_at": stored.set_at.timestamp(),
        }
    )


def _spec_fields(spec: JudgeSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "question": spec.question,
        "answer": spec.answer,
        "choices": list(spec.choices),
        "when": spec.on,
        "trigger": spec.trigger,
        "reads": list(spec.reads),
    }
