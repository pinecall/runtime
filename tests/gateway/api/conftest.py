"""What every door test knocks with: a call of the org's agent, and an app socket holding it."""

from collections.abc import AsyncIterator
from datetime import date

from websockets.asyncio.client import ClientConnection

from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.names import Env
from tests.conftest import AGENT, Knocking, received_until, sent

A_NUMBER = "+59829001199"
THE_CALLER = "+59899123456"
HER_PHONE = "+59899000001"


def a_call(knocking: Knocking, *, env: Env = "sandbox", channel: str = "phone") -> CallContext:
    """A call of the org's agent that rang at its number."""
    return CallContext(
        call=new_call_id(),
        channel="phone" if channel == "phone" else "web",
        direction="inbound",
        caller=THE_CALLER,
        route=Route(
            org=knocking.org.id,
            agent=AGENT,
            channel="phone" if channel == "phone" else "web",
            number=A_NUMBER if channel == "phone" else None,
            env=env,
        ),
        today=date(2026, 9, 28),
    )


async def an_app(
    knocking: Knocking, key: str | None = None, *, env: Env = "sandbox", console: bool = False
) -> ClientConnection:
    """An app socket holding the agent in the world of the key, the org's own unless given."""
    socket = await knocking.socket("/v1/apps", key or knocking.app[env])
    await sent(socket, "agent.register", {"routes": [], "takes_unclaimed": not console})
    await received_until(socket, "agent.registered")
    return socket


async def first_data(lines: AsyncIterator[str]) -> str:
    """The first `data:` line of an SSE stream, stripped."""
    async for line in lines:
        if line.startswith("data:"):
            return line[5:].strip()
    return ""
