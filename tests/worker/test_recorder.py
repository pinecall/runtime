"""Tests for a call's room recorded a track at a time: each audio track asked of egress once."""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from livekit import rtc
from livekit.rtc._proto import handle_pb2, track_pb2

from pinecall.domain.agent import AgentConfig
from pinecall.domain.scope import SCOPE_ATTRIBUTE
from pinecall.log.logs import Log
from pinecall.log.store import Store
from pinecall.session.call import Call
from pinecall.session.room import CallRoom
from pinecall.worker._recorder import MELODY_TRACK, Tracks, prefix_of, track_file
from tests.conftest import AGENT, postgres
from tests.fakes.acme import seat
from tests.fakes.livekit import Room, Server, Speaking, microphone
from tests.session.conftest import Box, context_of


@pytest.fixture
def box(store: Store, call: str) -> Box:
    """The platform around one call, on the test's own log."""
    return Box(Log(store, call, AGENT))


@pytest.fixture
async def server() -> AsyncIterator[Server]:
    """The SFU's server client, its doors the test's, closed at the end."""
    opened = Server()
    yield opened
    await opened.aclose()


def a_publication(
    sid: str, name: str, kind: int = track_pb2.KIND_AUDIO
) -> rtc.RemoteTrackPublication:
    """A track of the room, as livekit hands one to its listeners."""
    info = track_pb2.TrackPublicationInfo(sid=sid, name=name, kind=kind)  # pyright: ignore[reportArgumentType]
    owned = track_pb2.OwnedTrackPublication(handle=handle_pb2.FfiOwnedHandle(id=0), info=info)
    return rtc.RemoteTrackPublication(owned)


def recording(box: Box, server: Server, room: Room, root: Path) -> Tracks:
    """The track recorder of a spoken call in that room."""
    assert box.log.call is not None
    call = Call(
        context_of(box.log.call, "phone"), AgentConfig(slug="clinica-norte"), box.platform()
    )

    async def answered(_to: str) -> object:
        raise AssertionError

    where = CallRoom(call, room, server, trunks=answered, claim=answered)  # pyright: ignore[reportArgumentType]
    return Tracks(server, where, root)


async def settled(tracks: Tracks) -> None:
    """Every ask sent."""
    await asyncio.gather(*tracks.tasks)


def test_a_track_lands_flat_in_the_root_named_for_its_call_and_kind(tmp_path: Path) -> None:
    assert track_file(tmp_path, "CA_1", "TR_1", "caller") == tmp_path / "CA_1.TR_1.caller.ogg"
    assert prefix_of("org_1", "CA_1") == "org_1/CA_1/"


@postgres
async def test_every_audio_track_in_the_room_is_asked_of_egress_once_and_named_for_who_it_is(
    box: Box, server: Server, tmp_path: Path
) -> None:
    supervisor = Speaking(
        seat("sup", attributes={SCOPE_ATTRIBUTE: "supervise"}), a_publication("TR_sup", "mic")
    )
    room = Room(box.log.call or "", microphone("caller"), supervisor)
    tracks = recording(box, server, room, tmp_path)
    tracks.start()
    room.emit("local_track_published", a_publication("TR_agent", "roomio_audio"), None)
    room.emit("local_track_published", a_publication("TR_melody", MELODY_TRACK), None)
    room.emit("local_track_published", a_publication("TR_agent", "roomio_audio"), None)
    room.emit("local_track_published", a_publication("TR_cam", "cam", track_pb2.KIND_VIDEO), None)
    await settled(tracks)
    named = {start.track_id: Path(start.file.filepath).name for start in server.recorder.started}
    call = box.log.call
    assert named == {
        "TR_caller": f"{call}.TR_caller.caller.ogg",
        "TR_sup": f"{call}.TR_sup.supervisor.ogg",
        "TR_agent": f"{call}.TR_agent.agent.ogg",
        "TR_melody": f"{call}.TR_melody.melody.ogg",
    }
    assert {start.room_name for start in server.recorder.started} == {call}


@postgres
async def test_a_track_egress_refuses_is_said_and_the_others_are_recorded_and_stopped(
    box: Box, server: Server, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server.recorder.refused = {"TR_caller"}
    room = Room(box.log.call or "", microphone("caller"))
    tracks = recording(box, server, room, tmp_path)
    with caplog.at_level(logging.WARNING):
        tracks.start()
        room.emit("local_track_published", a_publication("TR_agent", "roomio_audio"), None)
        await settled(tracks)
    assert "the track TR_caller of" in caplog.text
    await tracks.stop()
    assert server.recorder.stopped == ["EG_TR_agent"]
