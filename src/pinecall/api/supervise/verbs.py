"""POST /v1/calls/{call}/verbs: one supervise verb from the desk, for the worker to apply."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.requests import Request
from starlette.status import HTTP_202_ACCEPTED

from pinecall.api._deps import KeysDep, SettingsDep, SnapshotsDep, StoreDep
from pinecall.api.calls.sink import reading
from pinecall.api.supervise.aiming import STEERS, QueueingDep
from pinecall.api.supervise.aiming import aimed as aimed_at
from pinecall.auth.keys import not_opening
from pinecall_protocol import WireModel, verbs

router = APIRouter()


NO_BEARER = "a supervise verb takes the org's key or a supervise token for that call"


class VerbTaken(WireModel):
    """The 202: which call, which verb, and no seq yet — the worker stamps that in the log."""

    call: str
    verb: str
    seq: int | None


# 202: the worker stamps the seq, and it does it after this request is over — the client reads
# the log for what happened, which is where every other fact about the call comes from.
# The bearer is parsed by the one function every log door already uses, so there is no third
# reading of an Authorization header in this tree: an org key IS the tenant, and a supervise token
# is the desk's own, minted for this one call by POST /v1/calls/{call}/supervise.
@router.post("/v1/calls/{call}/verbs", status_code=HTTP_202_ACCEPTED)
async def verb(
    call: str,
    said: verbs.Verb,
    request: Request,
    keys: KeysDep,
    settings: SettingsDep,
    store: StoreDep,
    snapshots: SnapshotsDep,
    live: QueueingDep,
) -> VerbTaken:
    """One verb onto a live call: 202 and the verb's name, or the sentence saying why not."""
    reader = await reading(request, keys, settings, None)
    if reader is None:
        raise HTTPException(401, NO_BEARER, {"WWW-Authenticate": "Bearer"})
    if reader.key is not None and (closed := not_opening(reader.key, STEERS)) is not None:
        raise HTTPException(403, closed)
    await aimed_at(live, store, snapshots, reader, call, said)
    return VerbTaken(call=call, verb=said.verb, seq=None)
