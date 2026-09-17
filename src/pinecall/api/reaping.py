"""The reaper: a spoken call whose room the SFU no longer has, ended here so its log can seal."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pinecall.api._serving import Serving
from pinecall.log import NOTHING_SAID, reduce
from pinecall.log.logs import CallLog
from pinecall.log.store import LogSealed
from pinecall.log.store.index import CallIndex, Unsealed
from pinecall.log.writers import Logs
from pinecall.providers import prices
from pinecall.routes.rooms import Rooms
from pinecall_protocol import defs, encode
from pinecall_protocol.events import CallEnded, CallScore, CallSummary
from pinecall_protocol.state import AgentTurn, State

logger = logging.getLogger(__name__)

# A spoken call is the worker's, and the worker writes call.ended from a shutdown callback. A job
# that is killed runs none — a deploy whose stop reached the job processes, a box that ran out of
# memory, a machine that went — and NOTHING else in this runtime ever closes that log: three web
# calls of 2026-09-16 were `live` on the console for thirty hours. This is the net under that.
# The worker's own half of it is worker/main.py and infra/box/pinecall-worker.service.

# How long a call must have said nothing before the reaper will look at it at all. The room is the
# decision; this is only the margin around it — the seconds between a call's log opening and its
# room existing, and any answer from the SFU that is a moment out of date. livekit deletes an empty
# room after `empty_timeout` (infra/box/livekit.yaml: sixty seconds), so five minutes is that
# clock five times over and still nothing a real conversation can reach: a caller can be silent
# for an hour, and while they are, their room is there and this reaper never touches them.
QUIET_S = 5 * 60.0

# How often a pass runs, and the most calls one pass will end. A pass is two reads — the open head
# rows, then one `list_rooms` — so it is cheap; the cap is what keeps a box that was down for a
# week from writing thousands of entries in one tick instead of over the next few minutes.
EVERY_S = 60.0
AT_MOST = 100

# `drained` is the word this runtime already uses for the platform taking a worker down with a call
# still on it (session/voice/hanging_up.py: livekit's JOB_SHUTDOWN reads as this). That is exactly
# what happened to a call whose room is gone and whose log never ended — the job went, the room
# emptied, the SFU deleted it — so the reaper says the same word rather than inventing one.
# `error` would blame a fault nobody saw, and `timeout` is the written thread's own ending.
DRAINED: defs.EndReason = "drained"
BY_THE_PLATFORM: defs.EndedBy = "platform"

# Why there is no verdict. A judge reads a call through the session that ran it, and there is none
# left: the entry says so in its own words rather than being absent, because a call nobody judged
# is a third thing and not a pass (evals, ring 4).
NOT_JUDGED = (
    "the worker holding this call went away before it could end it, and the platform sealed the "
    "log: there was no session left to judge"
)

REAPED = "sealed %s: its room is gone from the SFU and it has said nothing for %.0f s"


# Everything a normal ending writes, in the order it writes it. A log that already has one of them
# — a worker that got as far as call.ended and was killed before call.summary — is finished from
# where it stopped, never written twice.
ENDED = "call.ended"
SUMMARY = "call.summary"
SCORE = "call.score"


@dataclass(frozen=True)
class Reaper:
    """The gateway's one job that is about calls it never ran: end the ones nobody can end."""

    index: CallIndex
    logs: Logs
    rooms: Rooms
    live: Serving

    async def a_pass(self, now: float) -> list[str]:
        """One tick: the quiet open calls, the rooms the SFU still has, and the rest are sealed."""
        quiet = await self.index.unsealed_spoken(now - QUIET_S, AT_MOST)
        if not quiet:
            return []
        standing = await self.rooms.still_open([one.call for one in quiet])
        orphans = [one for one in quiet if one.call not in standing]
        sealed: list[str] = []
        for orphan in orphans:
            if await self._sealed(orphan, now):
                sealed.append(orphan.call)
        return sealed

    # A second gateway may be on the same tick: whichever one writes `call.score` first seals the
    # log, and the store refuses every later append with LogSealed. So the loser writes nothing
    # past that point and says nothing about it — the call is ended, which is all this was for.
    async def _sealed(self, orphan: Unsealed, now: float) -> bool:
        """End this call's log where its own worker stopped. False when somebody else got there."""
        log = self.logs.writing(orphan.call, orphan.agent)
        try:
            entries = await log.whole()
            await _finished(log, orphan, reduce(entries), {entry.type for entry in entries})
        except LogSealed:
            return False
        self.logs.forget(orphan.call)
        self.live.close(orphan.call)
        logger.warning(REAPED, orphan.call, now - orphan.last_at)
        return True


# The clock is the LAST thing the call said and never `now`: a call whose worker died at nine and
# is reaped at noon lasted until nine. Reading `now` here would put three hours of silence into
# the duration, and from there into the minutes an org is metered on.
async def _finished(log: CallLog, orphan: Unsealed, state: State, written: set[str]) -> None:
    """The three entries a call ends with, minus whatever its worker already managed to write."""
    duration = max(orphan.last_at - orphan.started_at, 0.0)
    if ENDED not in written:
        await log.append(
            ENDED,
            encode(
                CallEnded(
                    reason=DRAINED,
                    ended_by=BY_THE_PLATFORM,
                    ended_at=orphan.last_at,
                    duration_s=duration,
                )
            ),
        )
    if SUMMARY not in written:
        # No usage rows: what the models consumed was measured in the job process, and the job
        # process is what went away. An empty list is the truth — the alternative is a number
        # nobody counted — and `cost_of` prices it the same way it prices every other summary.
        await log.append(
            SUMMARY,
            encode(
                CallSummary(
                    reason=DRAINED,
                    outcome=_last_said(state),
                    duration_s=duration,
                    turns=len(state.turns),
                    usage=[],
                    cost=prices.cost_of([]),
                )
            ),
        )
    # Appending this is what seals the log: `call.score` is the terminal entry, and CallLog.seal
    # runs on it (log/logs.py). A log that somehow has a verdict and no seal — a process that died
    # between the two — is sealed outright, so no call can be looked at by every pass for ever.
    if SCORE not in written:
        await log.append(
            SCORE, encode(CallScore(judges=[], panel=[], judge_calls=0, not_judged=NOT_JUDGED))
        )
    else:
        await log.seal()


def _last_said(state: State) -> str:
    """The last thing the agent said, which is the outcome a summary carries; else `no reply`."""
    said = [turn.text for turn in state.turns if isinstance(turn, AgentTurn) and turn.text]
    return said[-1] if said else NOTHING_SAID


# Started by the lifespan and cancelled with it. The first pass runs before the wait, so a gateway
# coming up after the deploy that killed the workers ends what that deploy left behind instead of
# waiting a minute to notice. A pass that raises is one line and the loop goes on: the reaper is
# never allowed to be the reason a gateway stops answering.
async def reaping(reaper: Reaper, every: float = EVERY_S) -> None:
    """A pass now, then one every `every` seconds, for as long as the process lives."""
    while True:
        try:
            await reaper.a_pass(time.time())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the reaper's pass failed; the next one is in %.0f s", every)
        await asyncio.sleep(every)
