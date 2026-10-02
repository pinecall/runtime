"""Tests for a track of a call's recording landing: sealed under the call's key, noted, once."""

from dataclasses import replace
from pathlib import Path

from livekit.protocol import egress
from livekit.protocol.webhook import WebhookEvent

from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import Serving
from pinecall.gateway.ending.recorded import recorded
from pinecall.log.store import Claim
from pinecall.process.sealed_audio import Span, on_disk, opened, recorded_size
from pinecall.tenancy import recording_keys, recording_tracks
from tests.conftest import postgres
from tests.gateway.conftest import AGENT, OURS, a_call

AUDIO = b"OggS" + bytes(range(60)) * 100

STARTED_NS = 1_790_000_000 * 10**9


def ended(
    call: str, plain: Path, status: int = egress.EgressStatus.EGRESS_COMPLETE
) -> WebhookEvent:
    """LiveKit saying a track's egress of the call's room ended, its file written."""
    info = egress.EgressInfo(
        egress_id="EG_1",
        room_name=call,
        status=status,  # pyright: ignore[reportArgumentType]
        started_at=STARTED_NS,
        ended_at=STARTED_NS + 30 * 10**9,
        track=egress.TrackEgressRequest(room_name=call, track_id="TR_1"),
        file_results=[egress.FileInfo(filename=str(plain))],
    )
    return WebhookEvent(event="egress_ended", egress_info=info)


def recording_in(wired: Gateway, root: Path) -> Serving:
    """The gateway's serving, its recordings kept under the root."""
    connections = wired.connections
    settings = connections.settings.model_copy(update={"recordings_root": str(root)})
    return replace(wired.serving, connections=replace(connections, settings=settings))


async def opened_whole(call: str, path: Path, key: bytes) -> bytes:
    """A sealed file, opened whole."""
    sealed = await on_disk(call, path)
    assert sealed is not None
    span = Span(0, recorded_size(sealed.size) - 1)
    return b"".join([piece async for piece in opened(sealed, key, span)])


@postgres
async def test_a_track_that_landed_is_sealed_under_the_calls_key_and_noted_once(
    wired: Gateway, tmp_path: Path
) -> None:
    context = a_call(channel="phone")
    await wired.logs.store.claim(context.call, AGENT, OURS.org, Claim(OURS))
    serving = recording_in(wired, tmp_path)
    plain = tmp_path / f"{context.call}.TR_1.caller.ogg"
    plain.write_bytes(AUDIO)
    assert await recorded(serving, ended(context.call, plain)) == context.call
    assert await recorded(serving, ended(context.call, plain)) == context.call
    sealed = tmp_path / context.call / "TR_1.sealed"
    assert (plain.exists(), sealed.exists()) == (False, True)
    pool, vault = wired.connections.pool, wired.connections.vault
    key = await recording_keys.key_of(pool, vault, context.call)
    assert key is not None
    assert await opened_whole(context.call, sealed, key) == AUDIO
    (track,) = await recording_tracks.of_call(pool, context.call)
    assert (track.name, track.kind, track.started_at, track.ended_at) == (
        "TR_1.sealed",
        "caller",
        1_790_000_000.0,
        1_790_000_030.0,
    )


@postgres
async def test_a_file_named_for_no_call_or_an_egress_that_failed_is_let_be(
    wired: Gateway, tmp_path: Path
) -> None:
    context = a_call(channel="phone")
    await wired.logs.store.claim(context.call, AGENT, OURS.org, Claim(OURS))
    serving = recording_in(wired, tmp_path)
    stray = tmp_path / "somebody.TR_1.caller.ogg"
    stray.write_bytes(AUDIO)
    assert await recorded(serving, ended(context.call, stray)) is None
    failed = ended(context.call, stray, egress.EgressStatus.EGRESS_FAILED)
    assert await recorded(serving, failed) is None
    assert stray.exists()
    assert await recording_tracks.of_call(wired.connections.pool, context.call) == []
