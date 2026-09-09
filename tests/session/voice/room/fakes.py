"""What a room test runs on: a scripted room, a recording server API, a log read from memory."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from livekit import rtc
from livekit.api import LiveKitAPI
from livekit.protocol.sip import SIPTransferStatus, TransferSIPParticipantResponse

from pinecall.session.voice.room import Holding
from pinecall.session.voice.room.datachannel import DATA
from pinecall.session.voice.room.facts import CONNECTION, JOINED, LEFT, SPEAKERS
from pinecall.session.voice.writing import Writing
from pinecall.types import Channel
from pinecall.types.json import JsonObject
from pinecall.types.token import SCOPE_ATTRIBUTE
from tests.session.voice.fakes import CALL, Recording

ROOM_SID = "RM_fake"
TRUNK = "ST_outbound"

# livekit's second arrival event, the one agents/utils/participant.py:190 waits on.
ACTIVE = "participant_active"


@dataclass
class FakePublication:
    """A track as the room lists it: its sid, what it carries, where it comes from."""

    sid: str = "TR_microphone"
    kind: int = rtc.TrackKind.KIND_AUDIO
    source: int = rtc.TrackSource.SOURCE_MICROPHONE


@dataclass
class Published:
    """One packet the agent published on the DataChannel, decoded."""

    topic: str
    payload: JsonObject
    to: list[str]


@dataclass
class FakeParticipant:
    """A seat in the room, as the facts and the verbs read it."""

    identity: str
    kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    name: str = ""
    attributes: dict[str, str] = field(default_factory=dict[str, str])
    state: int = rtc.ParticipantState.PARTICIPANT_STATE_ACTIVE
    track_publications: dict[str, FakePublication] = field(
        default_factory=dict[str, FakePublication]
    )
    disconnect_reason: int | None = None
    published: list[Published] = field(default_factory=list[Published])

    async def publish_data(
        self, payload: bytes, *, topic: str = "", destination_identities: list[str] | None = None
    ) -> None:
        """LocalParticipant.publish_data, recorded instead of sent."""
        self.published.append(
            Published(topic, json.loads(payload), list(destination_identities or []))
        )


def a_caller(
    number: str = "+59897777", identity: str | None = None, dialled: str = "+59891111"
) -> FakeParticipant:
    """A SIP leg as livekit seats one: kind sip, and both numbers in its own attributes."""
    return FakeParticipant(
        identity=identity or f"sip_{number}",
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        attributes={
            "sip.callID": "SCL_1",
            "sip.callStatus": "active",
            "sip.trunkID": "ST_inbound",
            "sip.trunkPhoneNumber": dialled,
            "sip.phoneNumber": number,
            "sip.ruleID": "SDR_1",
        },
        track_publications={"TR_microphone": FakePublication()},
    )


def a_connected_room(*seated: FakeParticipant) -> FakeRoom:
    """A room the agent has already joined, with whoever was seated before it arrived."""
    room = FakeRoom()
    room.connect()
    for participant in seated:
        room.join(participant)
    return room


def as_a_room(room: FakeRoom) -> rtc.Room:
    """The scripted room as the signatures that take livekit's own name it."""
    return cast("rtc.Room", room)


def a_widget(identity: str = "web_ab12cd34ef56") -> FakeParticipant:
    """A browser that joined with a talk token: the scope rides its attributes."""
    return FakeParticipant(identity=identity, attributes={SCOPE_ATTRIBUTE: "talk"})


class FakeRoom:
    """livekit's Room as the facts and the DataChannel subscribe to it, driven by the test."""

    def __init__(self, name: str = CALL) -> None:
        self.name = name
        self.connected = False
        self.local_participant = FakeParticipant(
            identity="agent-AJ_fake", kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        )
        self.remote_participants: dict[str, FakeParticipant] = {}
        self.listeners: dict[str, list[Callable[..., None]]] = {}

    @property
    async def sid(self) -> str:
        """Room.sid is a coroutine: the id arrives with the connect result."""
        return ROOM_SID

    def on(self, name: str, callback: Callable[..., None]) -> None:
        self.listeners.setdefault(name, []).append(callback)

    def off(self, name: str, callback: Callable[..., None]) -> None:
        self.listeners.get(name, []).remove(callback)

    def emit(self, name: str, *args: Any) -> None:
        for callback in list(self.listeners.get(name, [])):
            callback(*args)

    # ── the script ──────────────────────────────────────────────────────────────

    def isconnected(self) -> bool:
        """livekit's own question, asked by every wait in agents/utils/participant.py."""
        return self.connected

    def connect(self) -> None:
        """The room connected: the state change is the event, as in the SDK."""
        self.connected = True
        self.emit(CONNECTION, rtc.ConnectionState.CONN_CONNECTED)

    # A seat arrives twice in the SDK: connected, then active once its state settles. The two
    # waits in the library listen on the second, and Facts on the first.
    def join(self, participant: FakeParticipant) -> None:
        self.remote_participants[participant.identity] = participant
        self.emit(JOINED, participant)
        self.emit(ACTIVE, participant)

    def leave(self, participant: FakeParticipant, reason: int) -> None:
        participant.disconnect_reason = reason
        self.remote_participants.pop(participant.identity, None)
        self.emit(LEFT, participant)

    def speaking(self, *participants: FakeParticipant) -> None:
        self.emit(SPEAKERS, list(participants))

    def receive(self, sender: FakeParticipant, topic: str, payload: Any) -> None:
        """A packet a participant published, as the room hands it over."""
        packet = rtc.DataPacket(
            data=json.dumps(payload).encode(),
            kind=rtc.DataPacketKind.KIND_RELIABLE,
            participant=cast("rtc.RemoteParticipant", sender),
            topic=topic,
        )
        self.emit(DATA, packet)

    @property
    def sent(self) -> list[Published]:
        """Everything the agent published on the DataChannel, in order."""
        return self.local_participant.published


@dataclass
class Requested:
    """One server API call the verbs made: livekit's method name, and the request it sent."""

    method: str
    request: Any


class FakeApi:
    """LiveKitAPI's room and sip services, recording every call; refusing them all when told to."""

    def __init__(
        self,
        refusing: str | Exception | None = None,
        answering: TransferSIPParticipantResponse | None = None,
    ) -> None:
        self.requests: list[Requested] = []
        self._refusing = refusing
        self._answering = answering or TransferSIPParticipantResponse(
            status=SIPTransferStatus.STS_TRANSFER_SUCCESSFUL
        )
        self.room = self
        self.sip = self

    async def mute_published_track(self, request: Any) -> None:
        self._record("MutePublishedTrack", request)

    async def remove_participant(self, request: Any) -> None:
        self._record("RemoveParticipant", request)

    async def create_sip_participant(self, request: Any) -> None:
        self._record("CreateSIPParticipant", request)

    async def transfer_sip_participant(self, request: Any) -> TransferSIPParticipantResponse:
        self._record("TransferSIPParticipant", request)
        return self._answering

    # A server can say no by raising — a refusal, a SIP status the far end answered with — and the
    # test says which by handing over the exception itself rather than a string to wrap.
    def _record(self, method: str, request: Any) -> None:
        if isinstance(self._refusing, str):
            raise RuntimeError(self._refusing)
        if self._refusing is not None:
            raise self._refusing
        self.requests.append(Requested(method, request))


@dataclass
class Held:
    """A room held for a test: the fakes behind the Holding, and the log they write to."""

    holding: Holding
    room: FakeRoom
    api: FakeApi
    recording: Recording

    async def settled(self) -> None:
        """Every fact queued so far has reached the gateway, background tasks included."""
        for _ in range(5):
            await asyncio.sleep(0)
        await self.holding.writing.flushed()


def a_held_room(
    api: FakeApi | None = None, trunk: str | None = TRUNK, channel: Channel = "phone"
) -> Held:
    """One call's room, held: the verbs and the DataChannel reach it through the Holding."""
    room, recording = FakeRoom(), Recording()
    served = api or FakeApi()
    writing = Writing(recording, CALL)
    writing.open()
    holding = Holding(
        room=cast("rtc.Room", room),
        api=cast("LiveKitAPI", served),
        writing=writing,
        channel=channel,
        trunk=trunk,
    )
    return Held(holding, room, served, recording)


# The gateway's three reading doors, over a log the test writes itself: durable entries in seq
# order, and a tail that stays open for whatever is pushed after.
class FakeLog:
    """A call's log as the DataChannel reads it: the state, the pages, the live tail."""

    def __init__(self, state: JsonObject, entries: Sequence[JsonObject] = ()) -> None:
        self.state_now = state
        self.entries: list[JsonObject] = list(entries)
        self._live: asyncio.Queue[JsonObject] = asyncio.Queue()

    async def state(self, call: str) -> tuple[JsonObject, int]:  # noqa: ARG002 — the shape
        return self.state_now, self.last_seq

    async def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:  # noqa: ARG002
        for entry in list(self.entries):
            if entry["seq"] > after:
                yield entry

    async def tail(self, call: str, after: int) -> AsyncIterator[JsonObject]:  # noqa: ARG002
        for entry in list(self.entries):
            if entry["seq"] > after:
                yield entry
        while True:
            yield await self._live.get()

    def push(self, entry: JsonObject) -> None:
        """The gateway numbered one more entry: it is durable now and the tail hears it."""
        self.entries.append(entry)
        self._live.put_nowait(entry)

    @property
    def last_seq(self) -> int:
        return self.entries[-1]["seq"] if self.entries else 0
