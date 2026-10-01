"""A worker's batches of a call on one socket for its life, each answered with the seqs it got."""

import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pinecall.domain.errors import DeclarationRefused, NotSignedIn, PinecallError
from pinecall.domain.names import Json
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import Served
from pinecall.gateway.api.calls import UNKNOWN_EVENT, counted, known
from pinecall.tenancy import keys
from pinecall.wire.events import EVENTS
from pinecall.wire.rest.calls import (
    AppendEntriesRefused,
    AppendEntriesRequest,
    AppendEntriesResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# A request per batch cost the gateway its routing, its headers and its key each time; on the
# socket a batch is one frame in and one out, and the log's answer to a retry is the same as the
# batch door's. A refusal is a frame too, and the socket stays: a refused batch never ends a call.
@router.websocket("/v1/calls/{call}/entries")
async def entries_socket(websocket: WebSocket, call: str) -> None:
    """A worker's batches of a call, on one socket for its life, each answered with its seqs."""
    gateway = _deps.gateway_of(websocket)
    await websocket.accept()
    try:
        served = await _served(websocket, gateway, call)
    except PinecallError as refused:
        await _refused(websocket, refused)
        await websocket.close(code=_deps.POLICY_VIOLATION, reason=_deps.close_reason(str(refused)))
        return
    try:
        while True:
            raw: Json = await websocket.receive_json()
            await _answered(websocket, gateway, served, call, raw)
    except WebSocketDisconnect:
        logger.debug("call %s: its worker's socket closed", call)


async def _served(websocket: WebSocket, gateway: Gateway, call: str) -> Served:
    data = _deps.bearer_of(websocket.headers)
    verified = None if data is None else await gateway.keys.verify(data)
    if verified is None:
        raise NotSignedIn(_deps.TAKES_A_KEY)
    keys.check_opens(verified, "app", "fleet")
    key = Acting(bearer=verified, env=_deps.world_of_request(websocket, verified, gateway))
    return await known(gateway, key, call)


async def _answered(
    websocket: WebSocket, gateway: Gateway, served: Served, call: str, raw: Json
) -> None:
    try:
        body = AppendEntriesRequest.read(raw if isinstance(raw, dict) else {}, "entries")
        for item in body.entries:
            if item.type not in EVENTS:
                raise DeclarationRefused(UNKNOWN_EVENT.format(kind=item.type))
        gateway.live.in_use(call, time.monotonic())
        began = time.perf_counter()
        entries = await served.log.append_many(body.entries, after=body.after)
        counted(gateway, time.perf_counter() - began, entries)
    except PinecallError as refused:
        await _refused(websocket, refused)
        return
    await websocket.send_json(AppendEntriesResponse(entries=entries).written())


async def _refused(websocket: WebSocket, refused: PinecallError) -> None:
    frame = AppendEntriesRefused(refused=str(refused), status=refused.status)
    await websocket.send_json(frame.written())
