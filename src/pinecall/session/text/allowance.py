"""Whether the org may still pay for the next written turn, asked before the model answers it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pinecall._exceptions import PinecallError
from pinecall_protocol.defs import EndReason

# A session counts nothing against a quota itself: the quotas are orgs/'s, and a package cannot
# both run a call and hold the table that limits it. Whoever opens a written call hands it this —
# given the turns the call has taken and the tokens it has spent so far, it answers None or the
# sentence that refuses the next one (orgs/admission.py:a_turn).
type Allowance = Callable[[int, int], Awaitable[str | None]]

# The platform ended it because what the org was allotted ran out: the word a call that outlived
# its time ends with, since that is what happened, and credits.exhausted in the agent's log says
# which quota it was.
SPENT: EndReason = "timeout"


async def unlimited(turns: int, tokens: int) -> None:  # noqa: ARG001
    """The allowance of a session nobody limited: every turn is answered."""
    return None


class TurnRefused(PinecallError):
    """The org may not pay for this turn. str() is the sentence; the call is already over."""
