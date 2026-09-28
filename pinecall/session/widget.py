"""The DataChannel to a browser widget: the public log of its call, and the events it sends."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass

from livekit import rtc
from pydantic import TypeAdapter, ValidationError

from pinecall.domain.errors import PinecallError
from pinecall.domain.names import JsonObject
from pinecall.domain.person import READS_ITS_OWN_CALL
from pinecall.domain.scope import SCOPE_ATTRIBUTE
from pinecall.log.readers import project_entry, project_state
from pinecall.session.call import Call
from pinecall.wire.events import EventReceived
from pinecall.wire.frames import Entry
from pinecall.wire.state import State

logger = logging.getLogger(__name__)

# The topics of docs/protocol/projections.md; pinecall.ui is room.send's.
LOG = "pinecall.log"
SNAPSHOT = "pinecall.snapshot"
REPLAY = "pinecall.replay"
EVENT = "pinecall.event"
_A_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

# What a widget's greeting waits for the log to hold its arrival.
FLUSH_S = 5.0


# Only the gateway numbers a log's entries, so what reaches a browser is read back from it,
# never taken from what the session wrote.
@dataclass(frozen=True)
class Reading:
    """The call's log as the gateway holds it: the state now, a stretch of it, and its tail."""

    state: Callable[[], Awaitable[tuple[State, int]]]
    since: Callable[[int], AsyncIterator[Entry]]
    tail: Callable[[int], AsyncIterator[Entry]]


class Widget:
    """One call's channel to its widgets: one tail of the log, projected for each of them."""

    def __init__(self, call: Call, room: rtc.Room, reading: Reading) -> None:
        """A channel nobody listens to yet."""
        self.call = call
        self.room = room
        self.reading = reading
        # What each widget in the room has been sent, by its seat.
        self.audience: dict[str, int] = {}
        self.cursor = 0
        self.tailing: asyncio.Task[None] | None = None
        self.answering: set[asyncio.Task[None]] = set()

    def watch(self) -> None:
        """Greet each widget that is or comes into the room, and hear what it sends."""
        for name, listener in self._listeners().items():
            self.room.on(name, listener)  # pyright: ignore[reportUnknownMemberType]

    def stop(self) -> None:
        """Let go of the room and of the tail."""
        for name, listener in self._listeners().items():
            self.room.off(name, listener)  # pyright: ignore[reportUnknownMemberType]
        for task in (self.tailing, *self.answering):
            if task is not None:
                task.cancel()
        self.tailing = None
        self.answering.clear()

    def _listeners(self) -> dict[rtc.room.EventTypes, Callable[..., None]]:
        return {
            "connection_state_changed": self._connection,
            "participant_connected": self._arrived,
            "participant_disconnected": self._left,
            "data_received": self._received,
        }

    def _connection(self, state: int) -> None:
        if state == rtc.ConnectionState.CONN_CONNECTED:
            for seat in self.room.remote_participants.values():
                self._arrived(seat)

    def _arrived(self, seat: rtc.RemoteParticipant) -> None:
        if _a_widget(seat):
            self._answer(self._greet(seat.identity))

    def _left(self, seat: rtc.RemoteParticipant) -> None:
        self.audience.pop(seat.identity, None)

    # Flushed first, so the snapshot holds the widget's own arrival. What the tail sent past the
    # snapshot is sent again to this widget alone: nothing skipped, nothing twice.
    async def _greet(self, identity: str) -> None:
        await self.call.writing.flushed(FLUSH_S)
        state, last = await self.reading.state()
        public = project_state(state, "public", self.call.config, identity)
        await self._publish(SNAPSHOT, {"state": public, "last_seq": last}, identity)
        if self.tailing is None:
            self.cursor = last
            self.tailing = asyncio.create_task(self._tail(last))
        sent = last
        while sent < self.cursor:
            sent = await self._resend(identity, sent, self.cursor)
        self.audience[identity] = sent

    async def _tail(self, after: int) -> None:
        async for entry in self.reading.tail(after):
            self.cursor = entry.seq
            for identity, sent in sorted(self.audience.items()):
                if entry.seq > sent:
                    await self._send(entry, identity)
                # A widget that left while this was sent is not added back.
                if identity in self.audience:
                    self.audience[identity] = max(sent, entry.seq)

    async def _resend(self, identity: str, after: int, until: int) -> int:
        sent = after
        async for entry in self.reading.since(after):
            if entry.seq > until:
                break
            sent = entry.seq
            await self._send(entry, identity)
        return max(sent, until)

    async def _send(self, entry: Entry, identity: str) -> None:
        public = project_entry(entry, "public", self.call.config, identity)
        if public is not None:
            await self._publish(LOG, public, identity)

    async def _publish(self, topic: str, payload: JsonObject, identity: str) -> None:
        packed = json.dumps(payload, separators=(",", ":")).encode()
        await self.room.local_participant.publish_data(
            packed, topic=topic, destination_identities=[identity]
        )

    # Only a widget's seat speaks here. A replay ends with log.caught_up, as the SSE door's does;
    # an event must be one the agent declared a participant may send.
    def _received(self, packet: rtc.DataPacket) -> None:
        sender = packet.participant
        if sender is None or not _a_widget(sender):
            return
        data = _decoded(packet.data)
        if packet.topic == REPLAY and isinstance(data.get("after"), int):
            after = data["after"]
            if isinstance(after, int):
                self._answer(self._replay(sender.identity, after))
        elif packet.topic == EVENT:
            self._event(sender.identity, data)

    async def _replay(self, identity: str, after: int) -> None:
        sent = await self._resend(identity, after, self.cursor)
        caught_up = Entry(
            seq=sent,
            ts=time.time(),
            call=self.call.context.call,
            agent=self.call.config.slug,
            type="log.caught_up",
            ephemeral=True,
            data={"seq": sent},
        )
        await self._send(caught_up, identity)

    def _event(self, identity: str, payload: JsonObject) -> None:
        name, data = payload.get("name"), payload.get("data")
        if not isinstance(name, str) or not isinstance(data, dict):
            logger.warning("%s from a widget is not {name, data}; dropped", EVENT)
            return
        if not self.call.config.accepts(name, "participant"):
            logger.warning("%s %r is not one a participant may send; dropped", EVENT, name)
            return
        received = EventReceived(name=name, data=data, source="participant", identity=identity)
        self.call.writing.write("event.received", received)

    # Runs off the room's synchronous listener: the gateway's refusal is a warning, never a
    # task's exception nobody reads.
    def _answer(self, answering: Coroutine[object, object, None]) -> None:
        async def answered() -> None:
            try:
                await answering
            except PinecallError:
                logger.warning("the widget's channel could not read the log", exc_info=True)

        task = asyncio.create_task(answered())
        self.answering.add(task)
        task.add_done_callback(self.answering.discard)


def _a_widget(seat: rtc.Participant) -> bool:
    return seat.attributes.get(SCOPE_ATTRIBUTE, "") in READS_ITS_OWN_CALL


def _decoded(data: bytes) -> JsonObject:
    try:
        return _A_JSON_OBJECT.validate_json(data)
    except ValidationError:
        return {}
