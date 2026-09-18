"""Which file a call plays while a tool runs: the runtime's own, none, or a clip by its hash."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from pinecall.session.hold_audio import DEFAULT
from pinecall.types import Env
from pinecall.worker.client import Gateway, GatewayRefused

logger = logging.getLogger(__name__)

# A worker keeps each clip it fetched, named by its hash, so a clip is downloaded once per box and
# never again: a changed clip is a new hash. The temp dir is enough — losing it costs one download.
CACHE = Path(tempfile.gettempdir()) / "pinecall-hold"


# Never in the way of a call: a gateway that cannot say, or a clip that cannot be fetched, plays
# the runtime's own melody rather than nothing, and says so in the log.
async def the_melody(
    gateway: Gateway,
    slug: str,
    *,
    org: str | None,
    env: Env | None,
    holder: str | None,
    cache: Path = CACHE,
) -> Path | None:
    """The file this call plays while a tool runs, or None when the agent was turned off."""
    try:
        said = await gateway.hold_audio(slug, org=org, env=env, holder=holder)
    except GatewayRefused:
        logger.warning("the gateway did not say which hold melody %s plays: the default", slug)
        return DEFAULT
    if said.played == "off":
        return None
    if said.played != "custom" or said.sha256 is None:
        return DEFAULT
    kept = cache / f"{said.sha256}.ogg"
    if kept.is_file():
        return kept
    try:
        audio = await gateway.hold_audio_file(slug, org=org, env=env, holder=holder)
    except GatewayRefused:
        logger.warning("the hold melody of %s could not be fetched: the default plays", slug)
        return DEFAULT
    cache.mkdir(parents=True, exist_ok=True)
    # Written beside and renamed, so a second job on this box never reads half a file.
    part = kept.with_suffix(f".{os.getpid()}.part")
    part.write_bytes(audio)
    part.replace(kept)
    return kept
