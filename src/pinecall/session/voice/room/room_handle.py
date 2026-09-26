"""The room as a verb reaches it: livekit's room, the server API, the door it came in, the log."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from livekit import rtc
from livekit.api import LiveKitAPI

from pinecall.session.voice.log_writer import Writing
from pinecall.session.voice.room.outbound_trunk import NO_TRUNK, Trunks
from pinecall.types import Channel
from pinecall.types.json import JsonObject
from pinecall_protocol.events import ErrorEvent

# What the log says when LiveKit refused a room verb. The message is the server's own, verbatim,
# and `command` names the verb: the app learns what did not happen and why, from the log alone.
ROOM_VERB_FAILED = "room_verb_failed"


# Built once per call by the bridge, from the job's own room and its own API client
# (job.py:438). Every room verb and the DataChannel reach the room through this and nothing else,
# so a test hands them a scripted room and a scripted API and the verbs never know.
@dataclass(frozen=True)
class Holding:
    """One call's room, held by the worker: what the verbs act on and where their facts land."""

    room: rtc.Room
    api: LiveKitAPI
    writing: Writing
    # Which door this call came in through: only a phone has a SIP leg for a verb to act on.
    channel: Channel
    # Where a second leg is dialled out through, asked for the first time a verb wants one.
    trunks: Trunks = NO_TRUNK

    async def publish(self, topic: str, payload: JsonObject, to: Sequence[str] = ()) -> int:
        """One JSON message on the DataChannel, to these identities or to the room; its size."""
        packed = json.dumps(payload, separators=(",", ":")).encode()
        await self.room.local_participant.publish_data(
            packed, topic=topic, destination_identities=list(to)
        )
        return len(packed)

    def failed(self, verb: str, why: str) -> None:
        """A verb LiveKit refused: an error entry naming it. The call goes on; the log knows."""
        self.writing.later(
            "error",
            ErrorEvent(code=ROOM_VERB_FAILED, message=why, command=verb, recoverable=True),
        )
