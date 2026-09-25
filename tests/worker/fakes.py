"""What a keyless worker test runs on: a job nobody dialled, a counting bridge, a fake gateway."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from livekit.agents import JobContext
from livekit.agents.voice import Agent, AgentSession
from livekit.protocol import agent as jobs
from livekit.protocol import models

from pinecall.worker.client import Gateway
from pinecall_protocol import Command


class CountingBridge:
    """The bridge seam as a test needs it: the agent it runs, and the two moments it is told of."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self.opened_with: AgentSession[None] | None = None
        self.closed_because: str | None = None
        self.applied: list[Command] = []

    @property
    def agent(self) -> Agent:
        """The livekit Agent this call runs."""
        return self._agent

    async def opened(self, live: AgentSession[None]) -> None:
        """The session was built and is about to start."""
        self.opened_with = live

    async def apply(self, command: Command) -> None:
        """One command from the app onto this call, in the order the stream carried them."""
        self.applied.append(command)

    async def closed(self, reason: str) -> None:
        """The job is shutting down."""
        self.closed_because = reason

    async def holding(self, melody: Path | None) -> None:
        """What this call plays while a tool runs, noted."""
        self.melody = melody

    async def closing_time(self, limit_s: int) -> None:
        """The call's clock, started at this limit."""
        self.clock_started = True
        self.clock_limit_s = limit_s


# The participant is left empty on purpose: livekit fills it for a publisher job, and every job
# this worker answers is a room job, so a fake that filled it would be a fake of another library.
def a_job(*, room: str = "call_1", metadata: Mapping[str, Any] | str | None = None) -> jobs.Job:
    """A job as livekit hands a room job over: a room, dispatch metadata, and no participant."""
    return jobs.Job(
        id="AJ_fake",
        room=models.Room(name=room),
        participant=models.ParticipantInfo(),
        metadata=_said(metadata),
    )


class JobWithADirectory:
    """The two lines of JobContext a recording touches: the private field and its property."""

    def __init__(self, directory: Path) -> None:
        self._session_directory = directory

    @property
    def session_directory(self) -> Path:
        return self._session_directory


def a_job_in(directory: Path) -> JobContext:
    """A job whose directory livekit chose, as the real one exposes it (job.py:378)."""
    return cast(JobContext, JobWithADirectory(directory))


# A dispatch writes JSON; a room somebody made by hand carries whatever they typed, and the tests
# say so by passing a string straight through.
def _said(metadata: Mapping[str, Any] | str | None) -> str:
    """The job's metadata field as the protobuf carries it: always a string, often JSON."""
    if metadata is None:
        return ""
    return metadata if isinstance(metadata, str) else json.dumps(dict(metadata))


@dataclass(frozen=True)
class Seen:
    """One request the worker made, as the gateway would have received it."""

    method: str
    path: str
    body: Any


def a_gateway(
    answers: Mapping[str, Any] | None = None,
    seen: list[Seen] | None = None,
    watching: Callable[[str], None] | None = None,
) -> Gateway:
    """A gateway over a transport that opens no socket: it answers this table and 404s the rest."""

    def answer(request: httpx.Request) -> httpx.Response:
        if watching is not None:
            watching(request.url.path)
        if seen is not None:
            seen.append(Seen(request.method, request.url.path, _body(request)))
        said = (answers or {}).get(request.url.path)
        if said is not None:
            return httpx.Response(200, json=said)
        # Nothing to say is not the same as nothing there: a write the gateway accepted answers
        # 204, and only a read of something this table does not hold is a 404.
        return httpx.Response(204) if request.method == "POST" else httpx.Response(404, text="no")

    transport = httpx.MockTransport(answer)
    return Gateway(httpx.AsyncClient(transport=transport, base_url="http://gateway.test"))


def _body(request: httpx.Request) -> Any:
    """What was sent, read back as JSON; a request with no body sent nothing."""
    return json.loads(request.content) if request.content else None
