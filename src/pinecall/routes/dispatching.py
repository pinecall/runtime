"""Starting a job in a room nobody rang: the dispatch an outbound call opens with."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from livekit import api

from pinecall._settings import Settings
from pinecall.routes.sfu import Sfu
from pinecall.types import Env
from pinecall.types.dispatch import (
    AGENT_KEY,
    CALLER_KEY,
    DIAL_KEY,
    DIRECTION_KEY,
    ENV_KEY,
    HOLDER_KEY,
    ORG_KEY,
)

OUTBOUND = "outbound"

NO_LIVEKIT = (
    "this gateway has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it cannot start a job for a call"
    " it places"
)


# What the worker is told to dial, and it is told rather than asked: the gateway holds the trunk,
# the org's numbers and the org's policy, and the worker holds one key for every org. A dispatch
# is the gateway's own writing — nothing on the media plane can forge one — so the ceiling on the
# call rides here beside the number, and the worker enforces it without a door of its own.
@dataclass(frozen=True)
class Dialling:
    """The leg the worker places when its job starts: through what, to whom, shown as what."""

    trunk: str
    to: str
    shown: str
    max_duration_s: int

    @property
    def as_json(self) -> dict[str, Any]:
        """What travels in the dispatch's metadata, under one key."""
        return {
            "trunk": self.trunk,
            "to": self.to,
            "shown": self.shown,
            "max_duration_s": self.max_duration_s,
        }


@dataclass(frozen=True)
class Job:
    """One outbound job: whose it is, which agent answers on it, and the room it opens in."""

    call: str
    agent: str
    org: str
    env: Env
    dialling: Dialling
    holder: str | None = None


class Dispatches(Protocol):
    """How the gateway starts a worker on a call nobody rang. The only verb an outbound door has."""

    async def started(self, job: Job) -> None:
        """A job for this call, in a room named by it. The worker places the leg from there."""
        ...


class MemoryDispatches:
    """What a clone with no LiveKit pair, and every test, would have dispatched."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def started(self, job: Job) -> None:
        self.jobs.append(job)


class LivekitDispatches:
    """One `create_dispatch`, exactly as a spoken eval run makes one (evals/calling.py)."""

    def __init__(self, sfu: Sfu, fleet: str) -> None:
        self._sfu = sfu
        self._fleet = fleet

    # The room is not created first: livekit makes it when the dispatch lands, and its NAME is the
    # call id, which is what lets the worker's router read the log and the log find the room with
    # nothing minted in between (worker/entry.py: `a_call(ctx.room.name …)`).
    async def started(self, job: Job) -> None:
        """The worker dispatched into the call's own room, carrying whose it is and what to dial."""
        async with self._sfu.api() as livekit:
            await livekit.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    room=job.call,
                    agent_name=self._fleet,
                    metadata=json.dumps(metadata_of(job), separators=(",", ":")),
                )
            )


def metadata_of(job: Job) -> dict[str, Any]:
    """What the dispatch tells the worker: whose call, which agent, that it is ours to dial."""
    said: dict[str, Any] = {
        AGENT_KEY: job.agent,
        ORG_KEY: job.org,
        ENV_KEY: job.env,
        DIRECTION_KEY: OUTBOUND,
        # The far end is the contact on an outbound call: it is who the call is with, and it is
        # what memory files the call under. The router falls back to the room name without it.
        CALLER_KEY: job.dialling.to,
        DIAL_KEY: job.dialling.as_json,
    }
    if job.holder is not None:
        said[HOLDER_KEY] = job.holder
    return said


def dispatches_for(settings: Settings) -> Dispatches | None:
    """The SFU when the process has the LiveKit pair; None when it has none: the door says so."""
    sfu = Sfu.of(settings)
    return None if sfu is None else LivekitDispatches(sfu, settings.fleet)
