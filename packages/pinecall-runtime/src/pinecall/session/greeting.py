"""The opening: which of the two verbs a session runs the moment it can speak, and with what."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pinecall.types import Greeting

# Both verbs take the same pair — the words, and whether the caller may cut them short — so the
# session hands in its own two and this module picks between them. Neither session decides what a
# greeting means: a greeting is one rule, and it is here.
type Speaks = Callable[[str, bool | None], Awaitable[None]]


# A call a RUN opened has no opening: the state it starts in is the conversation that already
# happened. Both sessions ask here, so the rule is written once (docs/decisions/dispatch.md, `run`).
def greeting_for(greeting: Greeting | None, run: str | None) -> Greeting | None:
    """The opening this call gets: none at all when a run opened it, whatever the class declared."""
    if run is not None:
        return None
    return greeting


async def open_the_call(greeting: Greeting | None, *, say: Speaks, reply: Speaks) -> None:
    """Open the call the way the class declared it. Declaring nothing is waiting for the caller."""
    if greeting is None:
        return
    # Exactly one of the two is set — Greeting refuses anything else at declaration time — so
    # these are the two arms of one choice and not two independent questions.
    if greeting.say is not None:
        await say(greeting.say, greeting.allow_interruptions)
    elif greeting.reply is not None:
        await reply(greeting.reply, greeting.allow_interruptions)
