"""A golden played on a written call: its state, its lines, its facts injected, its memory."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pinecall.domain.errors import PinecallError
from pinecall.domain.names import JsonObject
from pinecall.log.logs import Subscription
from pinecall.session import text
from pinecall.session.call import Lookup
from pinecall.session.session import Session
from pinecall.wire.commands import CallEvent, SessionConfigure
from pinecall.wire.parts import EndedBy, EndReason, PlatformTool
from pinecall.wire.rest.evals import EventStep, Golden

# The wire has no "the app is done reacting": the log going quiet is the sign. Long enough for a
# render and its re-render to be one burst, short enough to cost little per turn.
QUIET_S = 0.15


# An app that never answers cannot hold a run up longer than this per step.
AT_MOST_S = 2.0


# The source of a golden's facts, so the log never passes them off as real memory.
A_GOLDEN = "golden"


APP_DETACHED: tuple[EndReason, EndedBy] = ("app_detached", "platform")


HUNG_UP: tuple[EndReason, EndedBy] = ("caller_hung_up", "caller")


# A golden the call refused (an event nobody declared) ends the call it opened.
BROKE: tuple[EndReason, EndedBy] = ("error", "platform")


@dataclass(frozen=True)
class Played:
    """A golden played to its end, or to where the app let go of the agent."""

    held: bool
    # The requests the model was sent, one per request, as they went.
    requests: tuple[JsonObject, ...]


def events_after(golden: Golden, turn: int) -> tuple[EventStep, ...]:
    """The facts injected after this many caller lines, in the order the golden lists them."""
    return tuple(event for event in golden.events if event.after_turn == turn)


# recall reads the golden's facts and nothing else; search is the real index, since that is what
# the golden is asking about.
def golden_lookup(facts: Sequence[str], search: Lookup) -> Lookup:
    """The lookup a golden's call runs: recall answered by its facts, search by the index."""

    async def lookup(tool: PlatformTool, arguments: JsonObject, speech: str | None) -> JsonObject:
        if tool != "recall":
            return await search(tool, arguments, speech)
        return {"facts": [{"text": fact, "source": A_GOLDEN} for fact in facts]}

    return lookup


# The call opens with no greeting (a run starts mid-conversation); the facts go through the
# app's own `call.event`, so an event the agent never declared is refused as on a real call.
async def drive(
    session: Session, golden: Golden, heard: Subscription, *, is_held: Callable[[], bool]
) -> Played:
    """Start the call, seed its state, say each line and inject each fact, then hang up."""
    await session.start()
    try:
        kept_on = await _played(session, golden, heard, is_held=is_held)
    except PinecallError:
        await text.end(session, *BROKE)
        raise
    await text.end(session, *(HUNG_UP if kept_on else APP_DETACHED))
    return Played(held=kept_on, requests=tuple(session.call.requests))


async def settled(heard: Subscription, *, quiet_s: float = QUIET_S) -> None:
    """Return once nothing reached the log for `quiet_s`, and always within AT_MOST_S."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + AT_MOST_S
    while (left := deadline - loop.time()) > 0:
        try:
            await asyncio.wait_for(anext(heard), min(quiet_s, left))
        except (TimeoutError, StopAsyncIteration):
            return


async def _played(
    session: Session, golden: Golden, heard: Subscription, *, is_held: Callable[[], bool]
) -> bool:
    await settled(heard)
    await session.apply(SessionConfigure(state=dict(golden.state)))
    await settled(heard)
    for turn, line in enumerate([*golden.input, None]):
        for event in events_after(golden, turn):
            await session.apply(CallEvent(name=event.name, data=dict(event.data)))
            await settled(heard)
        if not is_held():
            return False
        if line is None:
            break
        await text.hears(session, line)
        await settled(heard)
    return True
