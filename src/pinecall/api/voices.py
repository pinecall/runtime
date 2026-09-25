"""GET /v1/voices and POST /v1/voices/sample: a vendor's voices, and one heard before choosing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field
from starlette.requests import HTTPConnection

from pinecall.api._deps import PipelineKeyDep, SettingsDep, VaultDep, held
from pinecall.orgs.vault import keys_brought_by
from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.tts.sampling import Sample, SampleRefused, a_sample
from pinecall.providers.tts.shelf import NotListed, Shelf, ShelfUnreachable
from pinecall.providers.tts.voices import voice_declared
from pinecall.types import DeclarationRefused
from pinecall_protocol import WireModel

router = APIRouter()

WAV = "audio/wav"

# Both doors ask for `pipeline`, the scope that turns the voice of an agent (api/tuning.py,
# PIPELINE_ONLY): hearing a voice is how a person who may choose it chooses it. Both run on the
# org's own key for the vendor when it brought one, and on the box's otherwise, as a call would —
# so what a person hears here is what the caller will hear, billed where the call would be.

type Sampler = Callable[[str, Asked, str], Awaitable[Sample]]


def the_shelf(connection: HTTPConnection) -> Shelf:
    """The vendor catalogues, over the gateway's one HTTP client."""
    return held(connection, "shelf", Shelf)


def the_sampler() -> Sampler:
    """What says a sentence in a voice: the vendor's plugin, as a call builds it."""
    return a_sample


ShelfDep = Annotated[Shelf, Depends(the_shelf)]
SamplerDep = Annotated[Sampler, Depends(the_sampler)]


class ListedVoice(WireModel):
    """One voice a picker offers: the id the setting takes, and what a person chooses by."""

    id: str
    name: str
    language: str
    description: str
    gender: str
    country: str
    accent: str


class Listed(WireModel):
    """A vendor's voices in one language, in the vendor's own order."""

    tts: str
    language: str | None
    voices: list[ListedVoice]


class Sampling(WireModel):
    """What a person wants to hear: which vendor, which model, which voice, which words."""

    tts: str
    voice: str
    model: str | None = None
    language: str | None = None
    # A sentence, not a document: long enough for the agent's greeting, short enough that a
    # sample is seconds of a vendor's time and not minutes of it.
    text: str = Field(min_length=1, max_length=400)


@router.get("/v1/voices")
async def voices(
    key: PipelineKeyDep,
    settings: SettingsDep,
    vault: VaultDep,
    shelf: ShelfDep,
    tts: Annotated[str, Query(min_length=1)],
    language: Annotated[str | None, Query()] = None,
) -> Listed:
    """The vendor's voices in that language: 404 for a vendor this build does not list."""
    asked = Asked(settings=settings, keys=await keys_brought_by(vault, key.org))
    try:
        found = await shelf.voices(tts, language, asked)
    except NotListed as not_listed:
        raise HTTPException(404, str(not_listed)) from not_listed
    except NoProvider as missing:
        raise HTTPException(409, str(missing)) from missing
    except ShelfUnreachable as unreachable:
        raise HTTPException(502, str(unreachable)) from unreachable
    return Listed(
        tts=tts,
        language=language,
        voices=[ListedVoice(**voice.__dict__) for voice in found],
    )


# The answer is the WAV itself, so a page plays it with an <audio> element and a terminal with
# whatever player it has; the two numbers ride in Server-Timing, the header a browser's own
# network panel already reads.
@router.post("/v1/voices/sample")
async def sample(
    said: Sampling,
    key: PipelineKeyDep,
    settings: SettingsDep,
    vault: VaultDep,
    sampler: SamplerDep,
) -> Response:
    """The words said in that voice, as a WAV: 409 no key, 422 a typo, 502 the vendor said no."""
    try:
        voice = voice_declared(said.voice, said.tts, None)
    except DeclarationRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    asked = Asked(
        settings=settings,
        model=said.model,
        language=said.language,
        voice_id=voice.voice_id,
        keys=await keys_brought_by(vault, key.org),
    )
    try:
        heard = await sampler(voice.provider or said.tts, asked, said.text)
    except NoProvider as missing:
        raise HTTPException(409, str(missing)) from missing
    except SampleRefused as refused:
        raise HTTPException(502, str(refused)) from refused
    timing = f"first-audio;dur={heard.first_audio_ms}, total;dur={heard.total_ms}"
    return Response(content=heard.wav, media_type=WAV, headers={"Server-Timing": timing})
