"""What a session does with its own log at hang-up: hands it to a judge it was given, if any."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from pinecall.log.entry import Entry
from pinecall.types import AgentConfig
from pinecall_protocol.events import CallScore

# A session judges nothing itself: the judges live in evals/, which drives sessions of its own for
# the rings, and a package cannot be both the thing judged and the judge. Whoever opens a call —
# the api for a text call, the worker for a spoken one — hands the session this.
type Scorer = Callable[[Sequence[Entry], AgentConfig], Awaitable[CallScore]]

NOBODY_JUDGED = "no judge was given to this session"


def nobody_judged(why: str) -> CallScore:
    """The score a call carries when no judge could be asked, and the reason."""
    return CallScore(judges=[], panel=[], judge_calls=0, not_judged=why)


async def unjudged_score(entries: Sequence[Entry], config: AgentConfig) -> CallScore:  # noqa: ARG001
    """The verdict of a session nobody handed a judge: no verdict, and the reason there is none."""
    return nobody_judged(NOBODY_JUDGED)
