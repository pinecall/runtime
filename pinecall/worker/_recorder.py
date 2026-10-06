"""Where a call's recording is written, and where it is kept once the session closed it."""

import asyncio
import logging
from pathlib import Path

import httpx

from pinecall.domain.errors import DeclarationRefused, GatewayRefused, UpstreamFailed
from pinecall.fleet.client import GatewayClient
from pinecall.process.recordings import AUDIO_FILE, SEALED_FILE, call_directory, recordings_of
from pinecall.process.sealed_audio import seal_file
from pinecall.process.settings import Settings

logger = logging.getLogger(__name__)


PLAIN = "the recording of %s is kept as it was written, not sealed: %s"


NOT_UNDER_THE_ROOT = "call {call!r} names no directory under the recordings root"


def recording_path(root: Path, call: str) -> Path:
    """The call's audio file, in a directory of its own under the root; refused outside it."""
    directory = call_directory(root, call)
    if directory is None:
        raise DeclarationRefused(NOT_UNDER_THE_ROOT.format(call=call))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / AUDIO_FILE


# A call that ended before its session started recording has no file, and the summary names none.
def written(audio: Path) -> bool:
    """Whether the session wrote the call's audio."""
    return audio.is_file() and audio.stat().st_size > 0


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
