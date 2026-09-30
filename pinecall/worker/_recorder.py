"""The recording of a call's room, kept where the box says, and the entry that names its file."""

import asyncio
import logging
import time
from pathlib import Path

import httpx
from livekit import api
from livekit.protocol import egress

from pinecall.domain.errors import GatewayRefused, UpstreamFailed
from pinecall.fleet.client import GatewayClient
from pinecall.process.recordings import AUDIO_FILE, SEALED_FILE, recordings_of
from pinecall.process.sealed_audio import seal_file
from pinecall.process.settings import Settings

logger = logging.getLogger(__name__)


# Audio only with no layout runs on livekit's SDK, not Chromium. The default mix, never
# DUAL_CHANNEL_AGENT: it drops the agent's second track, and the melody records as silence.
THE_WHOLE_ROOM = egress.AudioMixing.DEFAULT_MIXING


# egress finishes writing after the call: the summary must not point at a file not yet there.
THE_FILE_MAY_TAKE_S = 8.0


FILE_ASKED_EVERY_S = 0.2


# The recorder writes as a uid nobody names in advance: the directory is setgid, so its file
# stays the group's the gateway reads as.
SHARED_WITH_THE_RECORDER = 0o2770


PLAIN = "the recording of %s is kept as it was written, not sealed: %s"


def recording_path(root: Path, call: str) -> Path:
    """The call's audio file, in a directory of its own the recorder may write to."""
    directory = root / call
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(SHARED_WITH_THE_RECORDER)
    except OSError as refused:
        logger.warning(
            "%s is not group-writable (%s): the recorder cannot write in it",
            directory,
            refused.strerror,
        )
    return directory / AUDIO_FILE


# A recorder that refuses leaves the call without audio, and the call goes on.
async def record_room(server: api.LiveKitAPI, room: str, audio: Path) -> str | None:
    """Start recording the room into the file; the recording's id, or None if it was refused."""
    try:
        started = await server.egress.start_room_composite_egress(
            egress.RoomCompositeEgressRequest(
                room_name=room,
                audio_only=True,
                audio_mixing=THE_WHOLE_ROOM,
                file_outputs=[
                    egress.EncodedFileOutput(
                        file_type=egress.EncodedFileType.OGG,
                        filepath=str(audio),
                        disable_manifest=True,
                    )
                ],
            )
        )
    except api.TwirpError:
        logger.warning("this call is not recorded: the recorder refused", exc_info=True)
        return None
    return started.egress_id


async def file_written(server: api.LiveKitAPI, recording: str, audio: Path) -> bool:
    """Stop the recording and wait for its file; whether it was written in time."""
    try:
        await server.egress.stop_egress(egress.StopEgressRequest(egress_id=recording))
    except api.TwirpError:
        logger.warning("the recorder had stopped already for %s", recording, exc_info=True)
    # egress says nothing when the file lands: the disk is asked, a few times a second.
    deadline = time.monotonic() + THE_FILE_MAY_TAKE_S
    while not await asyncio.to_thread(_written, audio):
        if time.monotonic() > deadline:
            logger.warning(
                "the recording %s was not written within %gs", recording, THE_FILE_MAY_TAKE_S
            )
            return False
        await asyncio.sleep(FILE_ASKED_EVERY_S)
    return True


# The seal waits for this: the summary names the file, and the gateway finds it wherever it went.
async def stored(
    settings: Settings, gateway: GatewayClient, org: str, call: str, audio: Path
) -> Path:
    """Seal a written recording and move it where the box keeps it; the file the summary names."""
    kept = await _sealed(gateway, call, audio)
    async with httpx.AsyncClient() as http:
        try:
            await recordings_of(settings, http).store(org, call, kept)
        except UpstreamFailed:
            logger.warning("the recording of %s stays on this disk", call, exc_info=True)
    return kept


# Sealed on this disk before it goes anywhere, under a key the gateway keeps sealed for the call.
# A key the gateway cannot give leaves the recording as it was written, and says so: losing the
# call's audio would be worse than keeping it plain.
async def _sealed(gateway: GatewayClient, call: str, audio: Path) -> Path:
    try:
        key = await gateway.recording_key(call)
    except GatewayRefused:
        logger.warning(PLAIN, call, "the gateway gave no key", exc_info=True)
        return audio
    if key is None:
        logger.warning(PLAIN, call, "this gateway seals no recording")
        return audio
    sealed = audio.with_name(SEALED_FILE)
    try:
        await asyncio.to_thread(seal_file, audio, sealed, key)
    except OSError:
        logger.warning(PLAIN, call, "the sealed file could not be written", exc_info=True)
        return audio
    return sealed


def _written(audio: Path) -> bool:
    return audio.is_file() and audio.stat().st_size > 0
