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
from livekit.protocol import egress as proto

from pinecall.session.voice.platform import Platform
from pinecall.tokens.scopes import SCOPE_ATTRIBUTE
from pinecall.types import AgentConfig, CallContext, Route
from pinecall.types.dispatch import SCOPE_KEY, WRITTEN_SCOPE
from pinecall.worker import job as job_module
from pinecall.worker import job_target, recording_paths
from pinecall.worker.recording_paths import Keeping
from pinecall_testkit.fake_llm import FakeLLM
from pinecall_testkit.fake_room import FakeParticipant, a_caller, a_connected_room, as_a_room
from pinecall_testkit.silent_kit import FakeKit
from tests.worker.fakes import CountingBridge, Seen, a_gateway, a_job

pytestmark = pytest.mark.unit

CLINICA = Route(org="pinecall", agent="clinica-norte", channel="phone", number="+59891111")
CLINICA_ON_THE_WEB = Route(org="pinecall", agent="clinica-norte", channel="web")


async def test_the_door_decides_the_channel_and_the_job_decides_the_rest() -> None:
    room = a_connected_room(a_caller("+59897777", dialled="+59891111"))
    arrival = await job_target.arrival_of(
        a_job(room="call_room_1", metadata={"agent": "clinica-norte", "why": "a follow-up"}),
        as_a_room(room),
    )
    context = job_module.build_call_context("call_room_1", arrival, CLINICA, "UTC")
    assert (context.call, context.channel, context.caller) == ("call_room_1", "phone", "+59897777")
    assert context.route == CLINICA
    assert context.metadata["why"] == "a follow-up"
    assert context.today == date.today()


async def test_the_shutdown_callback_tells_the_bridge_then_seals_the_log() -> None:
    """The bridge writes call.ended and call.summary; sealing after is what closes the log."""
    seen: list[Seen] = []
    gateway = a_gateway(seen=seen)
    bridge = CountingBridge(Agent(instructions="You are Clara.", llm=FakeLLM()))  # pyright: ignore[reportUnknownMemberType]
    await job_module.seal_on_shutdown(gateway, bridge, "call_room_1")("caller_hung_up")
    assert bridge.closed_because == "caller_hung_up"
    assert [(one.method, one.path) for one in seen] == [("POST", "/v1/calls/call_room_1/sealed")]


def test_the_pointer_is_composed_without_touching_the_job_at_all(tmp_path: Path) -> None:
    """The box's recorder writes this file, so nothing of livekit's own is redirected for it."""
    job = _a_job_nobody_may_touch()
    audio = recording_paths.recording_path(job, "call_room_1", lambda call: tmp_path / call)
    assert audio == tmp_path / "call_room_1" / recording_paths.AUDIO_FILE


class _FarEnough(Exception):
    """The bridge refusing to open: everything this test is about has already happened."""


async def test_a_written_call_keeps_no_recording_even_on_a_box_that_keeps_audio(
    tmp_path: Path,
) -> None:
    """A `chat` visit carries no audio, so no audio.ogg is ever written: a summary that pointed
    at one was a session screen saying the file was on another box."""
    handed: list[Path | None] = []
    worker = _a_worker(
        bridging=_a_bridge_that_notes(handed), records=True, keeping=lambda _call: tmp_path
    )
    job = _a_job_that_records([], scope=WRITTEN_SCOPE)
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), worker)
    assert handed == [None]


async def test_an_agent_whose_world_says_not_to_record_keeps_no_audio(tmp_path: Path) -> None:
    """The one switch there is, and it is the agent's: `pinecall agent set --record off`."""
    handed: list[Path | None] = []
    worker = _a_worker(
        bridging=_a_bridge_that_notes(handed), records=False, keeping=lambda call: tmp_path / call
    )
    job = _a_job_that_records([])
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), worker)
    assert handed == [None]
    assert job.recorded == []


async def test_a_call_that_keeps_its_audio_asks_the_box_to_record_the_room(tmp_path: Path) -> None:
    """Audio only, one mix, and the room's own name: everything the call heard, in one ogg.

    Asked for BEFORE the session is built, so the greeting is inside the recording rather than
    ahead of it — the recorder takes a moment to come up and that moment is the one the session
    spends being built."""
    handed: list[Path | None] = []
    worker = _a_worker(
        bridging=_a_bridge_that_notes(handed), records=True, keeping=lambda call: tmp_path / call
    )
    job = _a_job_that_records([])
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), worker)

    assert handed == [tmp_path / "call_1" / recording_paths.AUDIO_FILE]
    (asked,) = job.recorded
    # The room's name IS the call id, which is what lets a reader of the log find the audio.
    assert asked.room_name == "call_1"
    assert asked.audio_only is True
    # Never DUAL_CHANNEL_AGENT: it carries one track per participant and drops the melody, which
    # is measured in worker/recorder.py and is the whole reason this records the room.
    assert asked.audio_mixing == proto.AudioMixing.DEFAULT_MIXING
    assert not asked.layout and not asked.custom_base_url  # the shape that runs without a browser
    (output,) = asked.file_outputs
    assert output.file_type == proto.EncodedFileType.OGG
    assert output.filepath == str(tmp_path / "call_1" / recording_paths.AUDIO_FILE)


async def test_a_recorder_that_will_not_take_the_job_still_takes_the_call(tmp_path: Path) -> None:
    """A call is never lost over its recording: the summary points at nothing instead."""
    handed: list[Path | None] = []
    worker = _a_worker(
        bridging=_a_bridge_that_notes(handed), records=True, keeping=lambda call: tmp_path / call
    )
    job = _a_job_that_records([], recorder_refuses=True)
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), worker)
    assert handed == [None]


async def test_every_livekit_line_of_the_call_names_the_room_it_belongs_to() -> None:
    """One assignment at the top of the job, and a box running forty calls can read its log."""
    job = _a_job_that_records([])
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), _a_worker())
    assert job.log_context_fields == {"room": "call_room_1"}


async def test_the_fleets_routes_are_asked_for_while_the_room_is_still_being_joined() -> None:
    """The routes table belongs to the org, not to this call, so nothing about it has to wait
    for the room. Asked for after the connect it was one more round trip the caller sat through."""
    order: list[str] = []
    job = _a_job_that_records(order)
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), _a_worker(order))
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


# `record` is said in every one of these, and said False unless a card is about the recording:
# whether a call keeps its audio is the agent's own setting now, and a job that keeps it asks the
# box's recorder for the room — which is a door, and a door nothing else here is about.
def _a_worker(
    order: list[str] | None = None,
    bridging: job_module.Bridging | None = None,
    *,
    records: bool = False,
    keeping: Keeping | None = None,
) -> job_module.Worker:
    """The worker one job runs on: both of the agent's doors, and a bridge that stops it."""
    seen: list[Seen] = []
    gateway = a_gateway(
        answers={
            "/v1/routes": [dataclasses.asdict(CLINICA), dataclasses.asdict(CLINICA_ON_THE_WEB)],
            "/v1/agents/clinica-norte/config": {
                "slug": "clinica-norte",
                "channels": ["phone", "web"],
                "record": records,
            },
            "/v1/agents/clinica-norte/provider-keys": {"keys": {}},
        },
        seen=seen,
        watching=_records("routes asked", order),
    )
    return job_module.Worker(
        gateway=gateway,
        kit=FakeKit(FakeLLM()),
        bridging=bridging or _a_bridge_that_refuses,
        keeping=keeping or (lambda call: Path("/nowhere") / call),
    )


def _a_bridge_that_notes(handed: list[Path | None]) -> job_module.Bridging:
    """A bridging seam that writes down the pointer it was born with, and then stops the job."""

    def bridging(
        context: CallContext, config: AgentConfig, platform: Platform, recording: Path | None
    ) -> job_module.Bridge:
        handed.append(recording)
        return _a_bridge_that_refuses(context, config, platform, recording)

    return bridging


def _a_bridge_that_refuses(
    context: CallContext,  # noqa: ARG001 — the Bridging seam's signature
    config: AgentConfig,  # noqa: ARG001
    platform: Platform,  # noqa: ARG001
    recording: Path | None,  # noqa: ARG001
) -> job_module.Bridge:
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
        self,
        order: list[str],
        room: rtc.Room | None = None,
        scope: str | None = None,
        recorder_refuses: bool = False,
    ) -> None:
        self.log_context_fields: dict[str, str] = {}
        self._order = order
        said = {"agent": "clinica-norte"} | ({SCOPE_KEY: scope} if scope is not None else {})
        self._job = a_job(room="call_room_1", metadata=said)
        self._room = room or as_a_room(a_connected_room(a_caller("+59897777", dialled="+59891111")))
        self._api = _ARecorder(refuses=recorder_refuses)

    @property
    def api(self) -> Any:
        """livekit's own API client, which is how a room is asked to be recorded."""
        return self._api

    @property
    def recorded(self) -> list[proto.RoomCompositeEgressRequest]:
        """Every recording this job asked the box for, as it asked for it."""
        return self._api.egress.asked

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
    order: list[str],
    room: rtc.Room | None = None,
    scope: str | None = None,
    recorder_refuses: bool = False,
) -> _AJobThatRecords:
    return _AJobThatRecords(order, room, scope, recorder_refuses)


class _TheBoxsRecorder:
    """The egress half of livekit's API client: what was asked for, and an id or a refusal."""

    def __init__(self, refuses: bool) -> None:
        self.asked: list[proto.RoomCompositeEgressRequest] = []
        self._refuses = refuses

    async def start_room_composite_egress(
        self, request: proto.RoomCompositeEgressRequest
    ) -> proto.EgressInfo:
        """One room composite asked for, or the refusal a box with no recorder answers with."""
        self.asked.append(request)
        if self._refuses:
            raise ConnectionError("no egress on this box")
        return proto.EgressInfo(egress_id="EG_fake", room_name=request.room_name)


class _ARecorder:
    """livekit's API client as this job uses it: the one service a call reaches for."""

    def __init__(self, refuses: bool) -> None:
        self.egress = _TheBoxsRecorder(refuses)


def _a_job_nobody_may_touch() -> JobContext:
    """A job that fails the test on any attribute at all: composing a path must never reach it."""

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

    monkeypatch.setattr(job_module.session, "build_session", a_session)
    job = _a_job_that_records([], room)
    with pytest.raises(_FarEnough):
        await job_module.answer(cast(JobContext, job), _a_worker(bridging=_a_bridge_that_opens))
    assert recorder.started_with is not None
    return recorder.started_with


def _a_bridge_that_opens(
    context: CallContext,  # noqa: ARG001 — the Bridging seam's signature
    config: AgentConfig,  # noqa: ARG001
    platform: Platform,  # noqa: ARG001
    recording: Path | None,  # noqa: ARG001
) -> job_module.Bridge:
    """A bridge that lets the job through to the start it is here to watch."""
    return CountingBridge(Agent(instructions="You are Clara.", llm=FakeLLM()))  # pyright: ignore[reportUnknownMemberType]


class _RecordsTheStart:
    """The AgentSession seam as `answer` uses it: what start() was told, and then far enough."""

    def __init__(self) -> None:
        self.started_with: RoomOptions | None = None

    async def start(self, agent: Agent, **said: Any) -> None:  # noqa: ARG002 — livekit's signature
        self.started_with = cast(RoomOptions, said["room_options"])
        raise _FarEnough
