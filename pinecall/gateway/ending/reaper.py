"""The reaper: it seals the calls nothing runs any more, a dead worker's and a silent chat's."""

import asyncio
import logging
import time

from livekit import api

from pinecall.channels.rooms import room_closed, rooms_with_an_agent
from pinecall.domain.errors import Conflict, NotAvailable
from pinecall.gateway._served import Serving
from pinecall.gateway.ending.seal import summed_up
from pinecall.log import queries
from pinecall.wire.events import CallEnded
from pinecall.wire.parts import EndReason
from pinecall.wire.rest.calls import SealCallRequest
from pinecall.wire.scores import CallScore

logger = logging.getLogger(__name__)

NOT_JUDGED_REAPED = (
    "the worker holding this call went away before it could end it, and the platform sealed the "
    "log: there was no session left to judge"
)


NOTHING_SAID = "no reply"


# Well over livekit's empty_timeout (60 s): the empty room is the signal, this is the margin.
QUIET_S = 5 * 60.0


REAPED_EVERY_S = 60.0


AT_MOST = 100


# How long a WhatsApp thread waits for its contact before it is closed.
A_THREAD_WAITS_S = 2 * 60 * 60.0


REAPED = "sealed %s: no agent is in its room and it has said nothing for %.0f s"


# A killed job writes no call.ended, and nothing else would close its log.
async def reaped(serving: Serving, server: api.LiveKitAPI, now: float) -> list[str]:
    """Seal the quiet calls nothing runs any more, and say which."""
    sealed_now: list[str] = []
    quiet = await queries.unsealed_spoken(serving.connections.pool, now - QUIET_S, limit=AT_MOST)
    existing: set[str] = set()
    if quiet:
        existing = await rooms_with_an_agent(server, [item.call for item in quiet])
    for orphan in quiet:
        if orphan.call not in existing and await _finished(serving, orphan, "drained"):
            # The room goes too, so whoever is still in it hears the call end.
            await room_closed(server, orphan.call)
            logger.warning(REAPED, orphan.call, now - orphan.last_at)
            sealed_now.append(orphan.call)
    for orphan in await queries.unsealed_written(
        serving.connections.pool, now - QUIET_S, limit=AT_MOST
    ):
        # A written call waits for its caller as long as its channel would.
        patience = A_THREAD_WAITS_S if orphan.channel == "whatsapp" else QUIET_S
        if serving.live.calls.get(orphan.call) is not None or now - orphan.last_at < patience:
            continue
        if await _finished(serving, orphan, "timeout"):
            sealed_now.append(orphan.call)
    return sealed_now


# A pass that fails is said, and the next one runs: the reaper never stops.
async def reap_forever(serving: Serving, server: api.LiveKitAPI) -> None:
    """A pass now, and one every minute."""
    while True:
        try:
            await reaped(serving, server, time.time())
            await let_go(serving)
        except (Conflict, NotAvailable, api.TwirpError, OSError):
            logger.warning(
                "the reaper's pass failed; the next is in %.0f s", REAPED_EVERY_S, exc_info=True
            )
        await asyncio.sleep(REAPED_EVERY_S)


# A call sealed on another gateway ends its readers there; here it is only let go of, so it no
# longer counts against its org's calls at once, and idle calls first seen here go with it.
async def let_go(serving: Serving) -> list[str]:
    """Stop serving the calls sealed elsewhere, and the ones first seen here that went idle."""
    serving.live.idle(time.monotonic())
    gone = await queries.sealed_among(serving.connections.pool, list(serving.live.calls))
    for call in gone:
        serving.logs.forget(call)
        serving.live.close(call)
    return sorted(gone)


# Duration ends at the last entry, not now: reaping late bills no extra minutes.
async def _finished(serving: Serving, orphan: queries.Unsealed, reason: EndReason) -> bool:
    store = serving.logs.store
    log = serving.logs.writing(orphan.call, orphan.agent)
    written_types = {item.type for item in await store.whole(orphan.call)}
    try:
        if "call.ended" not in written_types:
            ended = CallEnded(
                reason=reason,
                ended_by="platform",
                ended_at=orphan.last_at,
                duration_s=max(orphan.last_at - orphan.started_at, 0.0),
            )
            await log.append("call.ended", ended.written())
        if "call.summary" not in written_types:
            await summed_up(
                serving.connections.pool,
                store,
                log,
                SealCallRequest(usage=[], outcome=NOTHING_SAID),
            )
        score = CallScore(judges=[], judge_calls=0, not_judged=NOT_JUDGED_REAPED)
        await log.append("call.score", score.written())
    except Conflict:
        # Another gateway sealed it first: nothing is left to do.
        return False
    finally:
        serving.logs.forget(orphan.call)
    serving.live.close(orphan.call)
    return True
