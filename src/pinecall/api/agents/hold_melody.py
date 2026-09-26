"""The hold melody's doors: what an agent plays while a tool runs, choosing it, and its bytes."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.requests import HTTPConnection

from pinecall.api.deps import DeclarationKeyDep, PipelineKeyDep, held
from pinecall.api.scope.request_scope import AnAgentHeld, CornerDep
from pinecall.orgs.hold_melody import Chosen, HoldAudio
from pinecall.session.hold_melody import DEFAULT, convert_melody
from pinecall_protocol import WireModel

router = APIRouter()


def the_hold_audio(connection: HTTPConnection) -> HoldAudio:
    """Which melody each agent plays while a tool runs, when it is not the runtime's own."""
    return held(connection, "hold_audio")


HoldAudioDep = Annotated[HoldAudio, Depends(the_hold_audio)]

# An upload is a whole file in one request: a five-minute mp3 is under ten megabytes.
MAX_BYTES = 20 * 1024 * 1024
TOO_BIG = f"a hold melody is {MAX_BYTES // (1024 * 1024)} MB at most as uploaded"
OFF = "{slug} plays no hold melody: turn it back on with PUT …/pipeline/hold-audio/played"

OGG = "audio/ogg"
DEFAULT_NAME = "A New Life"


class HoldAudioAnswer(WireModel):
    """What an agent plays while a tool runs: the runtime's melody, none, or an uploaded one."""

    played: Literal["default", "off", "custom"]
    name: str | None = None
    seconds: float | None = None
    sha256: str | None = None


class Played(WireModel):
    """The two choices that need no file: the runtime's melody back, or silence."""

    played: Literal["default", "off"]


def answer(chosen: Chosen | None) -> HoldAudioAnswer:
    """The stored choice as the doors say it; no row is the runtime's own melody."""
    if chosen is None:
        return HoldAudioAnswer(played="default", name=DEFAULT_NAME, seconds=24.0)
    if chosen.played == "off":
        return HoldAudioAnswer(played="off")
    return HoldAudioAnswer(
        played="custom", name=chosen.name, seconds=chosen.seconds, sha256=chosen.sha256
    )


async def _bytes(kept: HoldAudio, org: str, slug: str) -> Response:
    """The file this agent plays, as Ogg Opus: the uploaded clip, or the runtime's own."""
    chosen = await kept.chosen(org, slug)
    if chosen is not None and chosen.played == "off":
        raise HTTPException(404, OFF.format(slug=slug))
    audio = await kept.audio(org, slug) if chosen is not None else None
    return Response(content=audio or DEFAULT.read_bytes(), media_type=OGG)


# ── the console's: the Pipeline tab, on a key that opens `pipeline` ─────────────────────────────


@router.get("/v1/agents/{slug}/pipeline/hold-audio", dependencies=[AnAgentHeld])
async def hold_audio(slug: str, key: PipelineKeyDep, kept: HoldAudioDep) -> HoldAudioAnswer:
    """What this agent plays while a tool runs."""
    return answer(await kept.chosen(key.org, slug))


@router.get("/v1/agents/{slug}/pipeline/hold-audio/audio", dependencies=[AnAgentHeld])
async def hold_audio_file(slug: str, key: PipelineKeyDep, kept: HoldAudioDep) -> Response:
    """The melody itself, to listen to before a caller does."""
    return await _bytes(kept, key.org, slug)


# The body IS the file — a wav, an mp3, an ogg, an m4a, whatever PyAV decodes — and not a form:
# one request, no multipart, and the name it had rides in `?name=`. It is converted here, once,
# so every worker plays the same Opus and none of them decodes a stranger's mp3 mid-call.
@router.put("/v1/agents/{slug}/pipeline/hold-audio", dependencies=[AnAgentHeld])
async def upload(
    slug: str,
    request: Request,
    key: PipelineKeyDep,
    kept: HoldAudioDep,
    name: Annotated[str | None, Query(max_length=200)] = None,
) -> HoldAudioAnswer:
    """A file of yours as this agent's hold melody, converted; it plays from the next call on."""
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, TOO_BIG)
    melody = await asyncio.to_thread(convert_melody, data)
    named = (name or "").strip() or None
    chosen = Chosen(played="custom", sha256=melody.sha256, seconds=melody.seconds, name=named)
    await kept.keep(key.org, slug, chosen, melody.audio)
    return answer(chosen)


@router.put("/v1/agents/{slug}/pipeline/hold-audio/played", dependencies=[AnAgentHeld])
async def choose(
    slug: str, said: Played, key: PipelineKeyDep, kept: HoldAudioDep
) -> HoldAudioAnswer:
    """The runtime's melody back, or none at all. An uploaded clip is forgotten either way."""
    if said.played == "default":
        await kept.forget(key.org, slug)
        return answer(None)
    chosen = Chosen(played="off")
    await kept.keep(key.org, slug, chosen, None)
    return answer(chosen)


# ── the worker's: the corner of the call it is building, on the fleet's key ──────────────────────


@router.get("/v1/agents/{slug}/hold-audio", dependencies=[AnAgentHeld])
async def for_a_call(
    slug: str,
    key: DeclarationKeyDep,  # noqa: ARG001 — the scope is asked here; the corner says where
    corner: CornerDep,
    kept: HoldAudioDep,
) -> HoldAudioAnswer:
    """What the call being built plays while a tool runs; the worker fetches a clip by its hash."""
    return answer(await kept.chosen(corner.org, slug))


@router.get("/v1/agents/{slug}/hold-audio/audio", dependencies=[AnAgentHeld])
async def for_a_call_file(
    slug: str,
    key: DeclarationKeyDep,  # noqa: ARG001
    corner: CornerDep,
    kept: HoldAudioDep,
) -> Response:
    """The clip's bytes, once per worker per hash: the worker keeps it on disk after that."""
    return await _bytes(kept, corner.org, slug)
