"""The DataChannel: the call's log to the browsers in the room, projected public; what they send."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, Protocol, cast

from livekit import rtc

# The Literal `rtc.Room.on` accepts, taken from livekit and not re-exported through facts.py: an
# alias for somebody else's type is imported where it is used, like any other library name.
from livekit.rtc.room import EventTypes as RoomEvent

from pinecall._exceptions import PinecallError
from pinecall.log.projection import PUBLIC, project_entry, project_state
from pinecall.log.replay import caught_up
from pinecall.session.voice.room.facts import CONNECTION, JOINED, LEFT
from pinecall.session.voice.room.holding import Holding
from pinecall.types import AgentConfig
from pinecall.types.json import JsonObject
from pinecall.types.token import READS_ITS_OWN_CALL, SCOPE_ATTRIBUTE
from pinecall_protocol import defs, encode
from pinecall_protocol.room import EventReceived

logger = logging.getLogger(__name__)

# The topics, as docs/protocol/projections.md names them. pinecall.ui is room.send's and not here.
LOG = "pinecall.log"
SNAPSHOT = "pinecall.snapshot"
REPLAY = "pinecall.replay"
EVENT = "pinecall.event"

# The room's event for a packet somebody published.
DATA: RoomEvent = "data_received"

# Where a browser's fact came from, as event.received names it.
FROM_A_BROWSER: defs.EventSource = "participant"


# What the DataChannel asks the gateway for, and all it asks. The worker never learns a seq when
# it writes — the gateway numbers the log — so the entries a browser gets are READ back, numbered,
# through the doors worker/client.py knocks on. A Protocol, and not the Gateway itself, because the
# tests script a log in memory with a live tail, which no HTTP fake could do as plainly.
class Reading(Protocol):
    """The call's log as the gateway lets the worker read it: state now, a page, the tail."""

    async def state(self, call: str) -> tuple[JsonObject, int]:
        """The reduced state and the seq it was folded to."""
        ...

    def since(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """Every durable entry above the cursor, in seq order, until the store runs out."""
        ...

    def tail(self, call: str, after: int) -> AsyncIterator[JsonObject]:
        """The same, then log.caught_up, then live — for as long as the call lasts."""
        ...


# One per call, holding who is listening — every participate identity in the room, with the last
# seq it was sent — and one tail of the log shared by all of them. The projection is applied HERE,
# per viewer, before a byte leaves for the browser: a participant's own event.received reaches it
# and nobody else's does.
class DataChannel:
    """The worker as the room's authority: the log out to the widgets, their facts back in."""

    def __init__(self, holding: Holding, reading: Reading, config: AgentConfig, call: str) -> None:
        self._holding = holding
        self._reading = reading
        self._config = config
        self._call = call
        self._room: rtc.Room | None = None
        self._listening: dict[RoomEvent, Callable[..., None]] = {}
        self._audience: dict[str, int] = {}
        self._cursor = 0
        self._tailing: asyncio.Task[None] | None = None
        self._answering: set[asyncio.Task[None]] = set()

    def watch(self, room: rtc.Room) -> None:
        """Subscribe to the room: who arrives, who leaves, and what they publish."""
        self._room = room
        self._listening = {
            CONNECTION: self._connection_changed,
            JOINED: self._arrived,
            LEFT: self._left,
            DATA: self._received,
        }
        for name, callback in self._listening.items():
            room.on(name, callback)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`

    def stop(self) -> None:
        """Let go of the room and the tail: the call is over."""
        for task in (self._tailing, *self._answering):
            if task is not None:
                task.cancel()
        self._tailing = None
        self._answering.clear()
        room = self._room
        if room is None:
            return
        for name, callback in self._listening.items():
            room.off(name, callback)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        self._room = None

    # ── who is listening ────────────────────────────────────────────────────────

    def _connection_changed(self, state: int) -> None:
        """Connected: the widgets that were in the room before the agent are greeted now."""
        if state == rtc.ConnectionState.CONN_CONNECTED and self._room is not None:
            for participant in self._room.remote_participants.values():
                self._arrived(participant)

    def _arrived(self, participant: rtc.RemoteParticipant) -> None:
        """A participate token joined: the snapshot first, so a widget never starts empty."""
        if _is_a_widget(participant):
            self._answer(self._greet(participant.identity))

    def _left(self, participant: rtc.RemoteParticipant) -> None:
        """A widget left: nothing more is addressed to it."""
        self._audience.pop(participant.identity, None)

    # The snapshot is read once the room's own facts about this arrival have reached the gateway,
    # so it already holds the seat that asked for it. It stands at last_seq; the tail may be past
    # it or behind it. Whatever the tail already sent past the snapshot is resent to this one
    # viewer, and from then on the tail sends it only what lies above the last seq it was given:
    # no entry is skipped and none arrives twice.
    async def _greet(self, identity: str) -> None:
        """pinecall.snapshot, then the entries the tail already passed, then the live tail."""
        await self._holding.writing.flushed()
        state, last_seq = await self._reading.state(self._call)
        public = project_state(state, PUBLIC, self._config, identity)
        await self._holding.publish(SNAPSHOT, {"state": public, "last_seq": last_seq}, [identity])
        if self._tailing is None:
            self._cursor = last_seq
            self._tailing = asyncio.ensure_future(self._tail(last_seq))
        sent = last_seq
        while sent < self._cursor:
            sent = await self._resend(identity, sent, self._cursor)
        self._audience[identity] = sent

    async def _tail(self, after: int) -> None:
        """Every entry the gateway numbers from here on, to everyone listening, in order."""
        async for entry in self._reading.tail(self._call, after):
            self._cursor = entry["seq"]
            for identity, sent in sorted(self._audience.items()):
                if entry["seq"] > sent:
                    await self._send(entry, identity)
                # A viewer that left while the entry was on its way is not written back in.
                if identity in self._audience:
                    self._audience[identity] = max(sent, entry["seq"])

    async def _resend(self, identity: str, after: int, until: int) -> int:
        """The durable entries in (after, until], to one viewer; the last seq sent."""
        sent = after
        async for entry in self._reading.since(self._call, after):
            if entry["seq"] > until:
                break
            sent = entry["seq"]
            await self._send(entry, identity)
        return max(sent, until)

    async def _send(self, entry: JsonObject, identity: str) -> None:
        """One entry to one viewer, through the public projection; nothing when it drops it."""
        public = project_entry(entry, PUBLIC, self._config, identity)
        if public is not None:
            await self._holding.publish(LOG, public, [identity])

    # A greeting or a replay runs on its own task, off the room's synchronous callback. A gateway
    # that refuses the read must not become a traceback nobody retrieves: it is said once, and the
    # widget stays unserved until it asks again.
    def _answer(self, answering: Coroutine[Any, Any, None]) -> None:
        """Run one answer to a widget in the background, and log the gateway's refusal if any."""

        async def answered() -> None:
            try:
                await answering
            except PinecallError as refused:
                logger.warning("call %s: the DataChannel could not read: %s", self._call, refused)

        task = asyncio.ensure_future(answered())
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)

    # ── what they send ──────────────────────────────────────────────────────────

    # Only a participate token speaks here: a supervisor's browser has the tenant's doors. What a
    # widget may hand the agent is exactly what the tenant declared with `participant` among the
    # senders — the same gate the gateway keeps for the app's call.event, asked of the same config.
    def _received(self, packet: rtc.DataPacket) -> None:
        """pinecall.replay {after} or pinecall.event {name, data}, from a widget in the room."""
        sender = packet.participant
        if sender is None or not _is_a_widget(sender):
            return
        if packet.topic == REPLAY:
            said = _decoded(packet.data)
            if said is not None and isinstance(said.get("after"), int):
                self._answer(self._replay(sender.identity, said["after"]))
        elif packet.topic == EVENT:
            self._event_from(sender.identity, _decoded(packet.data))

    async def _replay(self, identity: str, after: int) -> None:
        """The durable entries above the cursor to that viewer, then log.caught_up, as over SSE."""
        sent = await self._resend(identity, after, self._cursor)
        marker = caught_up(self._call, self._config.slug, sent)
        await self._send(encode(marker), identity)

    def _event_from(self, identity: str, said: JsonObject | None) -> None:
        """event.received when the agent declared it from a participant; dropped with a warning."""
        name = None if said is None else said.get("name")
        data: Any = None if said is None else said.get("data")
        if not isinstance(name, str) or not isinstance(data, dict):
            logger.warning("call %s: %s from %s is not {name, data}", self._call, EVENT, identity)
            return
        if not self._config.accepts(name, FROM_A_BROWSER):
            logger.warning(
                "call %s: %s %r from %s is not declared for a participant; dropped",
                self._call,
                EVENT,
                name,
                identity,
            )
            return
        self._holding.writing.later(
            "event.received",
            EventReceived(
                name=name,
                data=cast("JsonObject", data),
                source=FROM_A_BROWSER,
                identity=identity,
            ),
        )


def _is_a_widget(participant: rtc.Participant) -> bool:
    """Whether this seat was taken with a call token — talk, chat or participate — by its scope."""
    return participant.attributes.get(SCOPE_ATTRIBUTE, "") in READS_ITS_OWN_CALL


def _decoded(data: bytes) -> JsonObject | None:
    """The packet as a JSON object, or None for anything that is not one."""
    try:
        said: Any = json.loads(data)
    except ValueError:
        return None
    return cast("JsonObject", said) if isinstance(said, dict) else None
