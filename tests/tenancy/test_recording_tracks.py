"""Tests for a call's recorded tracks: one row per track that landed, the first to begin first."""

from pinecall.postgres.pool import Pool
from pinecall.tenancy.recording_tracks import Track, kind_of, landed, of_call
from tests.conftest import postgres


@postgres
async def test_a_track_lands_once_and_the_call_lists_its_tracks_by_when_they_began(
    pool: Pool,
) -> None:
    agent = Track("TR_agent.sealed", "agent", 101.0, 130.0)
    caller = Track("TR_caller.sealed", "caller", 100.0, 130.0)
    for track in (agent, caller, agent):
        await landed(pool, "CA_1", track)
    assert await of_call(pool, "CA_1") == [caller, agent]
    assert await of_call(pool, "CA_2") == []


def test_a_kind_nobody_named_is_other() -> None:
    assert (kind_of("melody"), kind_of("a robot")) == ("melody", "other")
