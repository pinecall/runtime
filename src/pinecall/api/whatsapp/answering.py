"""The waiting room's loop, wired to the gateway: loaded from the log at start, then answering."""

from __future__ import annotations

import asyncio

from starlette.datastructures import State

from pinecall.api.whatsapp.doors import doors_of
from pinecall.api.whatsapp.threads import Threads
from pinecall.api.whatsapp.waiting import Waiting, answering
from pinecall.types import Env


async def a_waiting_room(state: State) -> asyncio.Task[None]:
    """What was waiting when this process started, loaded; then the loop that answers it."""
    threads: Threads = state.threads
    await threads.waiting.loaded(state.store)
    doors = doors_of(state)

    def held(env: Env, agent: str) -> bool:
        return doors.registry.taking(env, agent) is not None

    async def answer(waiting: Waiting) -> str | None:
        return await threads.answering(doors, waiting)

    return asyncio.ensure_future(answering(threads.waiting, doors.logs, held, answer))
