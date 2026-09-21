"""The box's recorder asked for one room: everything anybody on the call heard, in one file."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from livekit import api
from livekit.protocol import egress as proto

from pinecall.evals.polling import until

logger = logging.getLogger(__name__)

# One mix of the whole room. `audio_only` with no layout and no base URL is the one shape of a
# room composite that runs on livekit's own SDK instead of a headless Chromium (egress
# pkg/config/pipeline.go:ShouldUseSDKSource) — the same file for a fraction of a machine, which is
# what makes recording every call on a box of four cores a thing anyone can do.
#
# And the mix is the DEFAULT one, not DUAL_CHANNEL_AGENT, which would have been the pretty answer:
# the agent on one channel, everyone else on the other, the shape the session's own recorder had.
# Measured on the box, 2026-09-21, one call recorded each way: dual channel carries one track per
# participant, so the hold melody — which the agent publishes as a track of its OWN beside its
# voice — was subscribed to and then dropped, and the tool's twenty seconds came back as digital
# silence on both channels. The same call mixed by default has the melody in it. Since the melody
# and a supervisor who took the line are the whole reason the box records the room rather than the
# session, a mode that drops them is a mode that undoes the change.
AUDIO_BOTH_SIDES = proto.AudioMixing.DEFAULT_MIXING

# No `.json` beside the audio: the log is where a call is described, and a second description of
# it on disk is one that will disagree.
NO_MANIFEST = True

# How long the file is waited for after the room is asked to stop. Egress finishes writing after
# the call is over, and the pointer in `call.summary` must not name a file that is not there yet;
# a recording that takes longer than this is still written, and read on the next request.
THE_FILE_MAY_TAKE_S = 8.0

type Stopping = Callable[[], Awaitable[None]]
"""How a call closes its recording, held by whoever seals the log and run before the summary."""


async def recording_the_room(lk: api.LiveKitAPI, room: str, audio: Path) -> str | None:
    """Ask the box's recorder for this room's audio. None when it would not take the job."""
    try:
        started = await lk.egress.start_room_composite_egress(
            proto.RoomCompositeEgressRequest(
                room_name=room,
                audio_only=True,
                audio_mixing=AUDIO_BOTH_SIDES,
                file_outputs=[
                    proto.EncodedFileOutput(
                        file_type=proto.EncodedFileType.OGG,
                        filepath=str(audio),
                        disable_manifest=NO_MANIFEST,
                    )
                ],
            )
        )
    except Exception:  # noqa: BLE001 — a recorder that will not take the job must not take the call
        logger.warning("this call is not being recorded: the box's recorder refused", exc_info=True)
        return None
    return started.egress_id


async def and_the_file_is_written(lk: api.LiveKitAPI, egress_id: str, audio: Path) -> bool:
    """Stop the recording and wait for the file: a pointer must never name one that is not there."""
    try:
        await lk.egress.stop_egress(proto.StopEgressRequest(egress_id=egress_id))
    except Exception:  # noqa: BLE001 — the room closing stops it anyway; this only hurries it
        logger.debug("the recorder had already stopped for %s", egress_id, exc_info=True)

    async def written() -> bool:
        return audio.is_file() and audio.stat().st_size > 0

    if await until(written, within_s=THE_FILE_MAY_TAKE_S):
        return True
    logger.warning("the recording of %s was not written within %ss", egress_id, THE_FILE_MAY_TAKE_S)
    return False
