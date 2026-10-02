"""A call's room recorded a track at a time: egress copies each audio track as it was sent."""

import asyncio
import logging
from pathlib import Path

from livekit import api, rtc
from livekit.protocol import egress

from pinecall.domain.names import RecordedTrack
from pinecall.session.room import CallRoom

logger = logging.getLogger(__name__)


# livekit's background player names its track so: the hold melody, published for the whole call.
MELODY_TRACK = "background_audio"

# Who a seat of the room is, as its track is recorded: the caller on the left of the mix.
KIND_OF_SEAT: dict[str, RecordedTrack] = {
    "caller": "caller",
    "supervisor": "supervisor",
    "sip": "transfer",
}

REFUSED = "the track %s of %s is not recorded: egress refused it"


# Track egress writes each track's Opus into an Ogg with no decode and no encode; the room
# composite before it decoded, mixed and encoded again in a process of its own per call (~0.12
# vCPU, infra/lab/). The box's gateway seals each file when egress says it ended.
class Tracks:
    """The audio tracks of a call's room, each asked of egress once."""

    def __init__(self, server: api.LiveKitAPI, where: CallRoom, root: Path) -> None:
        """A room nothing of which is recorded yet."""
        self.server = server
        self.where = where
        self.root = root
        self.egresses: dict[str, str] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        """Record every audio track the room has, and each one it publishes from now on."""
        room = self.where.room
        room.on("track_published", self._remote)  # pyright: ignore[reportUnknownMemberType]
        room.on("local_track_published", self._local)  # pyright: ignore[reportUnknownMemberType]
        for seat in room.remote_participants.values():
            for publication in seat.track_publications.values():
                self._remote(publication, seat)
        for publication in room.local_participant.track_publications.values():
            self._local(publication, None)

    async def stop(self) -> None:
        """Stop every track's egress; one that ended with its track already is let be."""
        room = self.where.room
        room.off("track_published", self._remote)  # pyright: ignore[reportUnknownMemberType]
        room.off("local_track_published", self._local)  # pyright: ignore[reportUnknownMemberType]
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for running in self.egresses.values():
            try:
                await self.server.egress.stop_egress(egress.StopEgressRequest(egress_id=running))
            except api.TwirpError:
                logger.info("the egress %s had ended with its track", running)

    def _remote(self, publication: rtc.TrackPublication, seat: rtc.Participant) -> None:
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return
        kind = KIND_OF_SEAT.get(self.where.kind_of(seat, dict(seat.attributes)), "other")
        self._ask(publication.sid, kind)

    def _local(self, publication: rtc.TrackPublication, _track: object) -> None:
        if publication.kind != rtc.TrackKind.KIND_AUDIO:
            return
        self._ask(publication.sid, "melody" if publication.name == MELODY_TRACK else "agent")

    def _ask(self, track: str, kind: RecordedTrack) -> None:
        if track in self.egresses:
            return
        self.egresses[track] = ""
        task = asyncio.create_task(self._record(track, kind))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _record(self, track: str, kind: RecordedTrack) -> None:
        call = self.where.call.context.call
        output = egress.DirectFileOutput(
            filepath=str(track_file(self.root, call, track, kind)), disable_manifest=True
        )
        wanted = egress.TrackEgressRequest(
            room_name=self.where.room.name, track_id=track, file=output
        )
        try:
            started = await self.server.egress.start_track_egress(wanted)
        except api.TwirpError:
            logger.warning(REFUSED, track, call, exc_info=True)
            del self.egresses[track]
            return
        self.egresses[track] = started.egress_id


def track_file(root: Path, call: str, track: str, kind: RecordedTrack) -> Path:
    """Where egress writes a track: flat in the recordings' root, named for its call and kind."""
    return root / f"{call}.{track}.{kind}.ogg"


def prefix_of(org: str, call: str) -> str:
    """What the call's summary names for a recording kept by its tracks: its directory."""
    return f"{org}/{call}/"
