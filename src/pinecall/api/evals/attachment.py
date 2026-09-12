"""The app socket a run drives, watched: a call ends the moment it stops holding the agent."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any

from pinecall._exceptions import PinecallError
from pinecall.api.agents.doors import Agent
from pinecall.api.agents.holding import SocketId
from pinecall.api.agents.registry import Registry
from pinecall_protocol import defs

# What a call that was left with nobody rendering it is ended as, and who ended it: not the caller
# and not the agent — the platform noticed the socket was gone. Named once, here.
APP_DETACHED: defs.EndReason = "app_detached"
ENDED_BY: defs.EndedBy = "platform"

# Nothing on the wire announces a socket closing, so the registry is polled. Short enough that a
# call ends on the detach rather than on the turn it was in the middle of, and cheap: one dict
# lookup, and only while a conversation is actually running.
WATCHED_S = 0.05

LEFT = "app {app} stopped holding agent {slug} mid-call"


class AppDetached(PinecallError):
    """The app the run was opened against closed its socket while a conversation was running."""


class Attachment:
    """One run's grip on the app it drives: the socket the registry chose, watched as it works."""

    def __init__(self, registry: Registry, agent: Agent, app: SocketId) -> None:
        self._registry = registry
        self._agent = agent
        self._app = app

    @property
    def socket(self) -> SocketId:
        """The socket this run was opened against, and the only one it will ever drive."""
        return self._app

    @property
    def holder(self) -> str | None:
        """Whose corner that socket is in: what the run's calls recall and search. See 0021."""
        held = self._registry.on(*self._agent, self._app)
        return None if held is None else held.holder

    @property
    def held(self) -> bool:
        """True while that socket is still holding the agent; False the moment it disconnects."""
        return self._registry.on(*self._agent, self._app) is not None

    # The conversation is a task rather than an await so that the watch can end it: a turn waiting
    # on a model would otherwise hold the run for the whole of that model's own timeout, which is
    # exactly the wait this card exists to remove.
    async def driving[T](self, conversation: Coroutine[Any, Any, T]) -> T:
        """Run the conversation, and cancel it the moment the app stops holding the agent."""
        driving = asyncio.ensure_future(conversation)
        watching = asyncio.ensure_future(self._until_it_leaves())
        try:
            await asyncio.wait((driving, watching), return_when=asyncio.FIRST_COMPLETED)
            if driving.done():
                return driving.result()
            await _cancelled(driving)
            raise AppDetached(LEFT.format(app=self._app, slug=self._agent[1]))
        finally:
            await _cancelled(watching)

    async def _until_it_leaves(self) -> None:
        """Return when the socket is no longer holding the agent, and not before."""
        while self.held:
            await asyncio.sleep(WATCHED_S)


async def _cancelled(task: asyncio.Future[Any]) -> None:
    """Stop a task and wait for it to actually be gone, so nothing is left running behind us."""
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
