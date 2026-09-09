"""POST /v1/calls/{call}/verbs: one supervise verb from the desk, for the worker to apply."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from pinecall.api._deps import KeysDep, SettingsDep, SnapshotsDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.sink import reading
from pinecall.api.supervise.aiming import QueueingDep, VerbRefused
from pinecall.api.supervise.aiming import aimed as aimed_at
from pinecall_protocol import verbs

router = APIRouter()

# The worker stamps the seq, and it does it after this request is over: the client reads the log
# for what happened, which is the same place every other fact about the call comes from.
ACCEPTED = 202

NO_BEARER = "a supervise verb takes the org's key or a supervise token for that call"


# The bearer is parsed by the one function every log door already uses, so there is no third
# reading of an Authorization header in this tree: an org key IS the tenant, and a supervise token
# is the desk's own, minted for this one call by POST /v1/calls/{call}/supervise.
@router.post("/v1/calls/{call}/verbs", status_code=ACCEPTED)
async def verb(
    call: str,
    said: verbs.Verb,
    request: Request,
    keys: KeysDep,
    settings: SettingsDep,
    registry: RegistryDep,
    snapshots: SnapshotsDep,
    live: QueueingDep,
) -> dict[str, Any]:
    """One verb onto a live call: 202 and the verb's name, or the sentence saying why not."""
    reader = await reading(request, keys, settings, None)
    if reader is None:
        raise HTTPException(401, NO_BEARER, {"WWW-Authenticate": "Bearer"})
    try:
        await aimed_at(live, registry, snapshots, reader, call, said)
    except VerbRefused as refused:
        raise HTTPException(refused.status, refused.detail) from refused
    return {"call": call, "verb": said.verb, "seq": None}
