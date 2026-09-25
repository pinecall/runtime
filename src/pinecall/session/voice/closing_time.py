"""A voice call's clock: warned a minute before the agent's limit, and ended at it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from pinecall.session.voice.commands import Ending
from pinecall.types.agent import NO_LIMIT

# How long before the limit the agent is told: a minute is a goodbye and a last answer. A limit
# shorter than two minutes is told at its half, so the warning never comes before the call began.
WARNED_BEFORE_S = 60

# What the agent reads, the way a supervisor's whisper reaches it: an instruction the caller never
# hears. Spanish or English is the agent's own to choose — it answers in the caller's language.
CLOSING = (
    "The call reaches its time limit in about a minute. Bring it to a close now: answer what is "
    "pending in a sentence, tell the caller the call has to end soon, and say goodbye."
)


class Replies(Protocol):
    """The one move of the live session this clock makes: asking the agent for a turn."""

    def generate_reply(self, *, instructions: str) -> object: ...


Sleep = Callable[[float], Awaitable[None]]


# Two limits can end a call and the clock keeps whichever comes first: the agent's own
# (max_duration_s, a voice call's), and what is left of the org's minutes, which admission answered
# at the open (orgs/admission.py:a_call) and which holds a written visit too. One clock, so the
# agent is warned a minute before the call ends whichever of the two ends it.
def the_ceiling(agents_limit_s: int, seconds_left: int | None) -> int:
    """The limit this call is kept to, in seconds; NO_LIMIT when neither limit is set."""
    if seconds_left is None:
        return agents_limit_s
    if agents_limit_s == NO_LIMIT:
        return seconds_left
    return min(agents_limit_s, seconds_left)


# Counted from the moment the session is live, which is when call.started was written: the caller
# is on the line from there. The end drains the sentence being said (at_once=False) and is written
# as `timeout` by the `platform` — the words EndReason and EndedBy already have for it. The limit
# holds while a supervisor holds the line: it is the call's, not the agent's; only the warning is
# skipped then, because a turn generated now would talk over the person.
async def keep(
    limit_s: int,
    live: Replies,
    ending: Ending,
    a_person_has_the_line: Callable[[], bool],
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Warn the agent before `limit_s`, and end the call at it. Zero is no limit, and returns."""
    if limit_s == NO_LIMIT:
        return
    warned_at = limit_s - WARNED_BEFORE_S if limit_s >= 2 * WARNED_BEFORE_S else limit_s / 2
    await sleep(warned_at)
    if not a_person_has_the_line():
        live.generate_reply(instructions=CLOSING)
    await sleep(limit_s - warned_at)
    await ending.hangup("timeout", "platform")
