"""One job: the call as the platform will know it, and the two things that close it."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast, override

import pytest
from livekit import rtc
from livekit.agents import NOT_GIVEN, JobContext
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions
from livekit.protocol import agent as jobs

from pinecall.auth.scopes import SCOPE_ATTRIBUTE
from pinecall.session.voice.platform import Platform
from pinecall.types import AgentConfig, CallContext, Route
from pinecall.types.dispatch import SCOPE_KEY, WRITTEN_SCOPE
from pinecall.worker import entry, recordings, router
from tests.session.fake_llm import FakeLLM
from tests.session.voice.room.fakes import (
    FakeParticipant,
    a_caller,
    a_connected_room,
    as_a_room,
)
from tests.session.voice.silence import FakeKit
from tests.worker.fakes import CountingBridge, Seen, a_gateway, a_job, a_job_in

pytestmark = pytest.mark.unit

CLINICA = Route(org="pinecall", agent="clinica-norte", channel="phone", number="+59891111")
CLINICA_ON_THE_WEB = Route(org="pinecall", agent="clinica-norte", channel="web")


async def test_the_door_decides_the_channel_and_the_job_decides_the_rest() -> None:
    room = a_connected_room(a_caller("+59897777", dialled="+59891111"))
    arrival = await router.arrival_of(
        a_job(room="call_room_1", metadata={"agent": "clinica-norte", "why": "a follow-up"}),
        as_a_room(room),
    )
    context = entry.a_call("call_room_1", arrival, CLINICA)
    assert (context.call, context.channel, context.caller) == ("call_room_1", "phone", "+59897777")
    assert context.route == CLINICA
    assert context.metadata["why"] == "a follow-up"
    assert context.today == date.today()


async def test_the_shutdown_callback_tells_the_bridge_then_seals_the_log() -> None:
    """The bridge writes call.ended and call.summary; sealing after is what closes the log."""
    seen: list[Seen] = []
    gateway = a_gateway(seen=seen)
    bridge = CountingBridge(Agent(instructions="You are Clara.", llm=FakeLLM()))  # pyright: ignore[reportUnknownMemberType]
    await entry.sealing(gateway, bridge, "call_room_1")("caller_hung_up")
    assert bridge.closed_because == "caller_hung_up"
    assert [(one.method, one.path) for one in seen] == [("POST", "/v1/calls/call_room_1/sealed")]


def test_a_box_that_keeps_no_audio_composes_no_pointer_and_touches_no_job() -> None:
    job = _a_job_nobody_may_touch()
    assert entry.where_the_audio_goes(job, "call_room_1", lambda _call: None) is None


def test_a_box_that_keeps_audio_points_the_job_at_the_calls_directory(tmp_path: Path) -> None:
    job = a_job_in(tmp_path / "livekit-tmp")
    audio = entry.where_the_audio_goes(job, "call_room_1", lambda call: tmp_path / call)
    assert audio == tmp_path / "call_room_1" / recordings.AUDIO_FILE
    assert job.session_directory == tmp_path / "call_room_1"


class _FarEnough(Exception):
    """The bridge refusing to open: everything this test is about has already happened."""


async def test_a_written_call_keeps_no_recording_even_on_a_box_that_keeps_audio(
    tmp_path: Path,
) -> None:
    """A `chat` visit carries no audio, so no audio.ogg is ever written: a summary that pointed
    at one was a session screen saying the file was on another box."""
    handed: list[Path | None] = []

    def a_bridge_that_notes(
        context: CallContext,
        config: AgentConfig,
        platform: Platform,
        recording: Path | None,
    ) -> entry.Bridge:
        handed.append(recording)
        return _a_bridge_that_refuses(context, config, platform, recording)

    worker = dataclasses.replace(
        _a_worker(bridging=a_bridge_that_notes), keeping=lambda _: tmp_path
    )
    job = _a_job_that_records([], scope=WRITTEN_SCOPE)
    with pytest.raises(_FarEnough):
        await entry.answer(cast(JobContext, job), worker)
    assert handed == [None]


async def test_every_livekit_line_of_the_call_names_the_room_it_belongs_to() -> None:
    """One assignment at the top of the job, and a box running forty calls can read its log."""
    job = _a_job_that_records([])
    with pytest.raises(_FarEnough):
        await entry.answer(cast(JobContext, job), _a_worker())
    assert job.log_context_fields == {"room": "call_room_1"}


async def test_the_fleets_routes_are_asked_for_while_the_room_is_still_being_joined() -> None:
    """The routes table belongs to the org, not to this call, so nothing about it has to wait
    for the room. Asked for after the connect it was one more round trip the caller sat through."""
    order: list[str] = []
    job = _a_job_that_records(order)
    with pytest.raises(_FarEnough):
        await entry.answer(cast(JobContext, job), _a_worker(order))
    assert order.index("routes asked") < order.index("connected")


async def test_the_session_is_pointed_at_the_callers_seat_before_it_subscribes_to_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supervisor sits in the same room, and the agent hears the phone and nobody else."""
    room = a_connected_room(
        FakeParticipant("ana", attributes={SCOPE_ATTRIBUTE: "supervise"}),
        a_caller("+59897777", dialled="+59891111"),
    )
    started = await _the_session_started_in(monkeypatch, as_a_room(room))
    assert started.participant_identity == "sip_+59897777"


async def test_a_room_the_agent_reached_first_leaves_livekits_own_rule_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody to point at yet: the option is left unset rather than pinned at a guess."""
    started = await _the_session_started_in(monkeypatch, as_a_room(a_connected_room()))
    assert started.participant_identity is NOT_GIVEN


def _a_worker(
    order: list[str] | None = None, bridging: entry.Bridging | None = None
) -> entry.Worker:
    """The worker one job runs on: both of the agent's doors, and a bridge that stops it."""
    seen: list[Seen] = []
    gateway = a_gateway(
        answers={
            "/v1/routes": [dataclasses.asdict(CLINICA), dataclasses.asdict(CLINICA_ON_THE_WEB)],
            "/v1/agents/clinica-norte/config": {
                "slug": "clinica-norte",
                "channels": ["phone", "web"],
            },
            "/v1/agents/clinica-norte/provider-keys": {"keys": {}},
        },
        seen=seen,
        watching=_records("routes asked", order),
    )
    return entry.Worker(
        gateway=gateway,
        kit=FakeKit(FakeLLM()),
        bridging=bridging or _a_bridge_that_refuses,
        keeping=lambda _call: None,
    )


def _a_bridge_that_refuses(
    context: CallContext,  # noqa: ARG001 — the Bridging seam's signature
    config: AgentConfig,  # noqa: ARG001
    platform: Platform,  # noqa: ARG001
    recording: Path | None,  # noqa: ARG001
) -> entry.Bridge:
    """One bridge per call, as the worker builds them; this one is never asked to say anything."""
    return _RefusesToOpen(Agent(instructions="You are Clara.", llm=FakeLLM()))  # pyright: ignore[reportUnknownMemberType]


def _records(what: str, order: list[str] | None) -> Callable[[str], None]:
    """A hook that writes one word into the order the test is watching, and nothing otherwise."""

    def note(path: str) -> None:
        if order is not None and path == "/v1/routes":
            order.append(what)

    return note


class _RefusesToOpen(CountingBridge):
    """The bridge, stopping the job at `opened` — which is past everything under test here."""

    @override
    async def opened(self, live: AgentSession[None]) -> None:
        raise _FarEnough


class _AJobThatRecords:
    """The five lines of JobContext one job touches, with a room already seated and a slow join."""

    def __init__(
        self, order: list[str], room: rtc.Room | None = None, scope: str | None = None
    ) -> None:
        self.log_context_fields: dict[str, str] = {}
        self._order = order
        said = {"agent": "clinica-norte"} | ({SCOPE_KEY: scope} if scope is not None else {})
        self._job = a_job(room="call_room_1", metadata=said)
        self._room = room or as_a_room(a_connected_room(a_caller("+59897777", dialled="+59891111")))

    @property
    def job(self) -> jobs.Job:
        return self._job

    @property
    def room(self) -> rtc.Room:
        return self._room

    async def connect(self) -> None:
        """A join takes a moment; whatever else the worker started runs while it does."""
        await asyncio.sleep(0.02)
        self._order.append("connected")

    def add_shutdown_callback(self, callback: object) -> None:
        """livekit keeps these; nothing in this test ever runs one."""


def _a_job_that_records(
    order: list[str], room: rtc.Room | None = None, scope: str | None = None
) -> _AJobThatRecords:
    return _AJobThatRecords(order, room, scope)


def _a_job_nobody_may_touch() -> JobContext:
    """A job that fails the test on any attribute at all: RECORD=0 must never reach it."""

    class Untouchable:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"the job was touched: {name}")

        @override
        def __setattr__(self, name: str, value: object) -> None:
            raise AssertionError(f"the job was touched: {name}")

    return cast(JobContext, Untouchable())


# The one moment this card is about: `answer` has resolved the call, built the session and told the
# bridge — and the very next thing it does is hand livekit the identity it must listen to.
async def _the_session_started_in(monkeypatch: pytest.MonkeyPatch, room: rtc.Room) -> RoomOptions:
    """Run one job as far as `live.start`, and give back the room options it was started with."""
    recorder = _RecordsTheStart()
    built = cast(AgentSession[None], recorder)

    def a_session(*_asked: object, **_how: object) -> AgentSession[None]:
        """`session.a_session` for this job: the recorder, whatever it was asked to build."""
        return built

    monkeypatch.setattr(entry.session, "a_session", a_session)
    job = _a_job_that_records([], room)
    with pytest.raises(_FarEnough):
        await entry.answer(cast(JobContext, job), _a_worker(bridging=_a_bridge_that_opens))
    assert recorder.started_with is not None
    return recorder.started_with


def _a_bridge_that_opens(
    context: CallContext,  # noqa: ARG001 — the Bridging seam's signature
    config: AgentConfig,  # noqa: ARG001
    platform: Platform,  # noqa: ARG001
    recording: Path | None,  # noqa: ARG001
) -> entry.Bridge:
    """A bridge that lets the job through to the start it is here to watch."""
    return CountingBridge(Agent(instructions="You are Clara.", llm=FakeLLM()))  # pyright: ignore[reportUnknownMemberType]


class _RecordsTheStart:
    """The AgentSession seam as `answer` uses it: what start() was told, and then far enough."""

    def __init__(self) -> None:
        self.started_with: RoomOptions | None = None

    async def start(self, agent: Agent, **said: Any) -> None:  # noqa: ARG002 — livekit's signature
        self.started_with = cast(RoomOptions, said["room_options"])
        raise _FarEnough
