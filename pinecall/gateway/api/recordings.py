"""A call's recording as a player reads it: plain, or sealed and opened a range at a time."""

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse
from livekit.protocol import egress

from pinecall.domain.errors import Conflict, NotFound
from pinecall.gateway import _deps
from pinecall.gateway._deps import CallReaderDep, GatewayDep
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.process.mixing import Placed, mixed
from pinecall.process.recordings import (
    MIX_FILE,
    SEALED_FILE,
    Fetched,
    Recordings,
    recordings_of,
    served_sealed,
)
from pinecall.process.sealed_audio import Sealed, Span, on_disk, opened, recorded_size, seal_file
from pinecall.tenancy import recording_keys, recording_tracks

router = APIRouter()


NOT_RECORDED = "call {call} kept no recording"


NOT_HERE = "the recording of {call} is at {path} on the box that took the call, not on this one"


NOT_YET = "the recording of {call} is still landing: ask again in a few seconds"


# From the bucket once the worker moved it there, else the file the recorder wrote on this disk;
# a sealed one is opened by the call's own key, the range asked for and no more.
@router.get("/v1/calls/{call}/recording", response_model=None)
async def recording(
    call: str,
    reading: CallReaderDep,
    gateway: GatewayDep,
    byte_range: Annotated[str | None, Header(alias="range")] = None,
) -> FileResponse | StreamingResponse:
    """The call's audio, with byte ranges so a player can seek."""
    await _deps.check_readable(gateway, reading, call)
    await _deps.record_read(gateway, reading, call, "recording")
    state = await gateway.logs.reading(call).snapshot()
    summary = next(
        (
            item
            for item in reversed(await gateway.logs.store.whole(call))
            if item.type == "call.summary"
        ),
        None,
    )
    pointer = None if summary is None else summary.data.get("recording")
    if state.seq == 0 or not isinstance(pointer, str) or not pointer:
        raise NotFound(NOT_RECORDED.format(call=call))
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    # A call recorded by its tracks names its directory: what plays is their mix.
    if pointer.endswith("/") and kept is not None and kept.scope is not None:
        played = await _mix_of(gateway, kept.scope.org, call, byte_range)
        headers = {**played.headers, "content-disposition": f'attachment; filename="{call}.ogg"'}
        return StreamingResponse(
            played.body, status_code=played.status, headers=headers, media_type="audio/ogg"
        )
    path = Path(pointer)
    if kept is not None and kept.scope is not None:
        fetched = await _fetched(gateway, kept.scope.org, call, path, byte_range)
        if fetched is not None:
            headers = {
                **fetched.headers,
                "content-disposition": f'attachment; filename="{call}.ogg"',
            }
            return StreamingResponse(
                fetched.body, status_code=fetched.status, headers=headers, media_type="audio/ogg"
            )
    if path.name == SEALED_FILE or not await asyncio.to_thread(path.is_file):
        raise NotFound(NOT_HERE.format(call=call, path=pointer))
    return FileResponse(path, media_type="audio/ogg", filename=f"{call}.ogg")


# A sealed recording is in the bucket, or on this disk where its upload failed; a plain one is
# the bucket's to answer, and a file on this disk is served as a file.
async def _fetched(
    gateway: Gateway, org: str, call: str, path: Path, byte_range: str | None
) -> Fetched | None:
    connections = gateway.connections
    stored = recordings_of(connections.settings, connections.http)
    if path.name != SEALED_FILE:
        return await stored.fetch(org, call, byte_range)
    key = await recording_keys.key_of(connections.pool, connections.vault, call)
    kept = await stored.sealed(org, call) or await on_disk(call, path)
    return None if key is None or kept is None else served_sealed(kept, key, byte_range)


# The mix is made the first time it is asked for and kept sealed beside the tracks, so the second
# listener reads it; a track still in egress's hands makes it wait rather than leave a voice out.
async def _mix_of(gateway: Gateway, org: str, call: str, byte_range: str | None) -> Fetched:
    connections = gateway.connections
    key = await recording_keys.key_of(connections.pool, connections.vault, call)
    if key is None:
        raise NotFound(NOT_RECORDED.format(call=call))
    stored = recordings_of(connections.settings, connections.http)
    directory = Path(connections.settings.recordings_root) / call
    kept = await _sealed_at(stored, org, call, directory / MIX_FILE)
    if kept is None:
        await _mixing(gateway, stored, (org, call), key, directory)
        kept = await _sealed_at(stored, org, call, directory / MIX_FILE)
    if kept is None:
        raise NotFound(NOT_HERE.format(call=call, path=directory))
    return served_sealed(kept, key, byte_range)


async def _mixing(
    gateway: Gateway, stored: Recordings, of: tuple[str, str], key: bytes, directory: Path
) -> None:
    org, call = of
    connections = gateway.connections
    still = await connections.server.egress.list_egress(
        egress.ListEgressRequest(room_name=call, active=True)
    )
    if still.items:
        raise Conflict(NOT_YET.format(call=call))
    tracks = await recording_tracks.of_call(connections.pool, call)
    if not tracks:
        raise NotFound(NOT_RECORDED.format(call=call))
    first = tracks[0].started_at
    with tempfile.TemporaryDirectory() as scratch:
        placed: list[Placed] = []
        for track in tracks:
            sealed = await _sealed_at(stored, org, call, directory / track.name)
            if sealed is None:
                raise NotFound(NOT_HERE.format(call=call, path=directory / track.name))
            plain = Path(scratch) / f"{track.name}.ogg"
            await _opened_into(sealed, key, plain)
            placed.append(Placed(plain, track.kind == "caller", track.started_at - first))
        mix = Path(scratch) / "mix.ogg"
        await asyncio.to_thread(mixed, placed, mix)
        target = directory / MIX_FILE
        await asyncio.to_thread(seal_file, mix, target, key)
    await stored.store(org, call, target)


async def _sealed_at(stored: Recordings, org: str, call: str, path: Path) -> Sealed | None:
    return await stored.sealed(org, call, path.name) or await on_disk(call, path)


async def _opened_into(sealed: Sealed, key: bytes, plain: Path) -> None:
    size = recorded_size(sealed.size)
    pieces = [piece async for piece in opened(sealed, key, Span(0, size - 1))] if size else []
    await asyncio.to_thread(plain.write_bytes, b"".join(pieces))
