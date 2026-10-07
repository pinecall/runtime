"""A call's recording once the session closed it: whether it was written, sealed, and kept."""

import asyncio
import logging
from http import HTTPStatus
from pathlib import Path

import httpx

from pinecall.domain.errors import GatewayRefused, UpstreamFailed
from pinecall.fleet.client import GatewayClient
from pinecall.process.recordings import SEALED_FILE, recordings_of
from pinecall.process.sealed_audio import seal_file
from pinecall.process.settings import Settings

logger = logging.getLogger(__name__)


DROPPED = "the recording of %s is dropped, never kept unsealed: %s"


# A gateway that is away is asked again after each wait; one that answered a refusal is not.
KEY_WAITS_S = (1.0, 3.0, 9.0)


# A call that ended before its session started recording has no file, and the summary names none.
def written(audio: Path) -> bool:
    """Whether the session wrote the call's audio."""
    return audio.is_file() and audio.stat().st_size > 0


# The seal waits for this: the summary names the file, and the gateway finds it wherever it went.
async def stored(
    settings: Settings, gateway: GatewayClient, org: str, call: str, audio: Path
) -> Path | None:
    """Seal a written recording and move it where the box keeps it; the file the summary names."""
    kept = await _sealed(gateway, call, audio)
    if kept is None:
        return None
    async with httpx.AsyncClient() as http:
        try:
            await recordings_of(settings, http).store(org, call, kept)
        except UpstreamFailed:
            logger.warning("the recording of %s stays on this disk", call, exc_info=True)
    return kept


# Sealed on this disk before it goes anywhere, under a key the gateway keeps sealed for the call.
# No recording is ever kept as it was written: one the worker cannot seal is removed and the call's
# summary names none, as a call nobody recorded (docs/security/private-values.md).
async def _sealed(gateway: GatewayClient, call: str, audio: Path) -> Path | None:
    key = await _key_of(gateway, call)
    sealed = audio.with_name(SEALED_FILE)
    if key is None:
        logger.error(DROPPED, call, "the gateway gave no key")
        await asyncio.to_thread(_removed, audio, sealed)
        return None
    try:
        await asyncio.to_thread(seal_file, audio, sealed, key)
    except OSError:
        logger.exception(DROPPED, call, "the sealed file could not be written")
        await asyncio.to_thread(_removed, audio, sealed)
        return None
    return sealed


async def _key_of(gateway: GatewayClient, call: str) -> bytes | None:
    for wait_s in (*KEY_WAITS_S, None):
        try:
            return await gateway.recording_key(call)
        except GatewayRefused as refused:
            away = refused.answered is None or refused.answered >= HTTPStatus.INTERNAL_SERVER_ERROR
            if not away or wait_s is None:
                logger.warning("no key for the recording of %s", call, exc_info=True)
                return None
            await asyncio.sleep(wait_s)
    return None


def _removed(*files: Path) -> None:
    for file in files:
        file.unlink(missing_ok=True)
