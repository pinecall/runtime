"""A track of a call's recording landed: sealed under the call's key, kept, noted as the call's."""

import asyncio
import logging
from pathlib import Path

from livekit.protocol import egress
from livekit.protocol.webhook import WebhookEvent

from pinecall.domain.errors import UpstreamFailed
from pinecall.gateway._served import Serving
from pinecall.log import queries
from pinecall.process.recordings import SEALED, recordings_of
from pinecall.process.sealed_audio import seal_file
from pinecall.tenancy import recording_keys, recording_tracks
from pinecall.tenancy.recording_tracks import Track, kind_of

logger = logging.getLogger(__name__)

ENDED = "egress_ended"

# What egress may end a track's file with: the whole, or as much as fitted its limit.
WRITTEN = frozenset({egress.EgressStatus.EGRESS_COMPLETE, egress.EgressStatus.EGRESS_LIMIT_REACHED})

NANOSECONDS = 1e9

# A track's file is named of three: its call, its track's sid, its kind.
NAMED_OF = 3

NOT_OURS = "egress ended %s, a file no call's track is named as: let be"

PLAIN = "the track %s of %s stays as it was written, not sealed: %s"


# The worker asked egress for each track of the room (worker/_recorder.py), and egress wrote it on
# this box's disk; only a gateway on this box reads it there, which is why LiveKit's webhook reaches
# the box's own gateways (infra/box/caddy/Caddyfile). A second delivery finds the file sealed
# already and the row kept: nothing is done twice.
async def recorded(serving: Serving, event: WebhookEvent) -> str | None:
    """Seal and keep the track egress wrote, as one of its call's; the call, or None."""
    info = event.egress_info
    if event.event != ENDED or not info.HasField("track") or info.status not in WRITTEN:
        return None
    files = [*info.file_results] or ([info.file] if info.HasField("file") else [])
    plain = Path(files[0].filename) if files else None
    named = None if plain is None else _named(plain)
    if plain is None or named is None or named[0] != info.room_name:
        logger.warning(NOT_OURS, info.egress_id)
        return None
    call, track, kind = named
    connections = serving.connections
    kept = await queries.scope_of_call(connections.pool, call)
    if kept is None or kept.scope is None:
        return None
    sealed = Path(connections.settings.recordings_root) / call / f"{track}{SEALED}"
    if await asyncio.to_thread(plain.is_file):
        key = await recording_keys.key_for(
            connections.pool, connections.vault, kept.scope.org, call
        )
        if key is None:
            logger.warning(PLAIN, track, call, "the vault opens no key for it")
            return None
        await asyncio.to_thread(seal_file, plain, sealed, key)
        try:
            await recordings_of(connections.settings, connections.http).store(
                kept.scope.org, call, sealed
            )
        except UpstreamFailed:
            logger.warning("the track %s of %s stays on this disk", track, call, exc_info=True)
    landed = Track(
        sealed.name, kind_of(kind), info.started_at / NANOSECONDS, info.ended_at / NANOSECONDS
    )
    await recording_tracks.landed(connections.pool, call, landed)
    return call


# `<call>.<track sid>.<kind>.ogg`, as worker/_recorder.py track_file names it.
def _named(plain: Path) -> tuple[str, str, str] | None:
    if plain.suffix != ".ogg":
        return None
    parts = plain.stem.rsplit(".", NAMED_OF - 1)
    return (parts[0], parts[1], parts[2]) if len(parts) == NAMED_OF else None
