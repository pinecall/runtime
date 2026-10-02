"""A call's recorded tracks: one row per track egress landed, sealed, in the call's directory."""

from dataclasses import dataclass

from pinecall.domain.names import RECORDED_TRACKS, RecordedTrack
from pinecall.postgres.pool import Pool

# A webhook delivered twice lands once.
LANDED = """
INSERT INTO recording_tracks (call, name, kind, started_at, ended_at)
VALUES (%(call)s, %(name)s, %(kind)s, %(started_at)s, %(ended_at)s)
ON CONFLICT (call, name) DO NOTHING
"""

OF_CALL = """
SELECT name, kind, started_at, ended_at FROM recording_tracks
WHERE call = %(call)s ORDER BY started_at, name
"""


@dataclass(frozen=True)
class Track:
    """One track of a call's recording: its sealed object's name, who it is, when it ran."""

    name: str
    kind: RecordedTrack
    started_at: float
    ended_at: float


async def landed(pool: Pool, call: str, track: Track) -> None:
    """Keep that the track is sealed in the call's directory."""
    params = {
        "call": call,
        "name": track.name,
        "kind": track.kind,
        "started_at": track.started_at,
        "ended_at": track.ended_at,
    }
    async with pool.connection() as connection:
        await connection.execute(LANDED, params)


async def of_call(pool: Pool, call: str) -> list[Track]:
    """The call's tracks that landed, the first to begin first."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(OF_CALL, {"call": call})).fetchall()
    return [
        Track(row["name"], kind_of(row["kind"]), row["started_at"], row["ended_at"]) for row in rows
    ]


def kind_of(written: str) -> RecordedTrack:
    """The kind a name says, or other."""
    for kind in RECORDED_TRACKS:
        if kind == written:
            return kind
    return "other"
