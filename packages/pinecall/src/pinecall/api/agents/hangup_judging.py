"""Whether an org's calls are judged at hang-up: the tenant's read and switch, the worker's ask."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api.calls.worker_writes import NOT_OPEN
from pinecall.api.deps import (
    AppKeyDep,
    CallsKeyDep,
    LiveDep,
    OrgsDep,
    SettingsDep,
    UsageKeyDep,
)
from pinecall.auth.keys import is_fleet_key
from pinecall_protocol.rest import Judging, JudgingWanted

router = APIRouter()


# The org's and not a world's: the judges are the platform's measurement of every call the org
# takes, and what they may cost is the org's bill, which spans both worlds. The ceiling is the
# box's (`PINECALL_JUDGE_CEILING_EUR`), read here and set by nobody through this door.
@router.get("/v1/org/judging")
async def judging_standing(key: CallsKeyDep, orgs: OrgsDep, settings: SettingsDep) -> Judging:
    """Whether this org's calls are judged at hang-up, and what judging one may spend."""
    return Judging(on=await orgs.judges(key.org), ceiling_eur=settings.judge_ceiling_eur)


# `usage`: what an org spends is the manager's and the admin's to decide, and a model judge is
# spending. Off, a hang-up writes a call.score that says nobody judged it and why.
@router.put("/v1/org/judging")
async def turn_judging(
    said: JudgingWanted, key: UsageKeyDep, orgs: OrgsDep, settings: SettingsDep
) -> Judging:
    """Judge this org's calls from the next hang-up, or stop."""
    await orgs.set_judging(key.org, said.on)
    return Judging(on=said.on, ceiling_eur=settings.judge_ceiling_eur)


# The worker's, as /lookup and /remember are: a spoken call is judged in the worker, which holds
# no org table, so it asks about the call it is sealing. Another org's call is a 404 in the words
# a call nobody opened gets; the fleet's key asks for any call it opened.
@router.get("/v1/calls/{call}/judging")
async def judging_this_call(
    call: str, key: AppKeyDep, live: LiveDep, orgs: OrgsDep, settings: SettingsDep
) -> Judging:
    """Whether the org this open call belongs to judges its calls."""
    opened = live.the_call(call)
    if opened is None or (opened.org != key.org and not is_fleet_key(key)):
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))
    return Judging(on=await orgs.judges(opened.org), ceiling_eur=settings.judge_ceiling_eur)
