"""GET /v1/voices and POST /v1/voices/sample: a vendor's voices, and one heard before choosing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from starlette.requests import HTTPConnection

from pinecall.api._deps import PipelineKeyDep, SettingsDep, VaultDep, held
from pinecall.auth.throttle import Throttle
from pinecall.orgs.vault import keys_brought_by
from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.tts.sampling import Sample, SampleRefused, a_line_for, a_sample
from pinecall.providers.tts.shelf import NotListed, Shelf, ShelfUnreachable
from pinecall.providers.tuning import the_voice
from pinecall.types import DeclarationRefused
from pinecall_protocol.rest import ListedVoice, VoiceSample, VoicesListed

router = APIRouter()

WAV = "audio/wav"

# Both doors ask for `pipeline`, the scope that turns the voice of an agent (api/tuning.py,
# PIPELINE_ONLY): hearing a voice is how a person who may choose it chooses it. Both run on the
# org's own key for the vendor when it brought one, and on the box's otherwise, as a call would —
# so what a person hears here is what the caller will hear, billed where the call would be.
#
# And because it is billed, it is counted. A sample is a vendor's seconds on somebody's account
# — the box's, for an org that brought no key — and the door takes no usage row, so the brake is
# a rate: so many a minute per key, then 429. Thirty is a person comparing voices; three hundred
# is a loop.
SAMPLES_A_MINUTE = 30
A_MINUTE_S = 60.0
# A sentence, not a document: long enough for the agent's greeting, short enough that a sample is
# seconds of a vendor's time and not minutes of it.
TEXT_CEILING = 400

TOO_MANY = "{key} asked for {count} samples this minute: the ceiling is {count} a minute"
TOO_LONG = "a sample says a sentence: {length} characters, and the ceiling is {ceiling}"
THE_KEY_WAS_REFUSED = "{vendor} refused the key this org runs on: {why}"

type Sampler = Callable[[str, Asked, str], Awaitable[Sample]]


def the_shelf(connection: HTTPConnection) -> Shelf:
    """The vendor catalogues, over the gateway's one HTTP client."""
    return held(connection, "shelf", Shelf)


def the_sampling(connection: HTTPConnection) -> Throttle:
    """How many samples each key has asked for lately."""
    return held(connection, "sampling", Throttle)


def the_sampler() -> Sampler:
    """What says a sentence in a voice: the vendor's plugin, as a call builds it."""
    return a_sample


ShelfDep = Annotated[Shelf, Depends(the_shelf)]
SamplingDep = Annotated[Throttle, Depends(the_sampling)]
SamplerDep = Annotated[Sampler, Depends(the_sampler)]


@router.get("/v1/voices")
async def voices(
    key: PipelineKeyDep,
    settings: SettingsDep,
    vault: VaultDep,
    shelf: ShelfDep,
    tts: Annotated[str, Query(min_length=1)],
    language: Annotated[str | None, Query()] = None,
) -> VoicesListed:
    """The vendor's voices in that language: 404 for a vendor this build does not list."""
    asked = Asked(settings=settings, keys=await keys_brought_by(vault, key.org))
    try:
        found = await shelf.voices(tts, language, asked)
    except NotListed as not_listed:
        raise HTTPException(404, str(not_listed)) from not_listed
    except NoProvider as missing:
        raise HTTPException(503, str(missing)) from missing
    except ShelfUnreachable as unreachable:
        raise HTTPException(_a_status(unreachable.status), str(unreachable)) from unreachable
    return VoicesListed(
        tts=tts, language=language, voices=[ListedVoice(**asdict(voice)) for voice in found]
    )


# The answer is the WAV itself, so a page plays it with an <audio> element and a terminal with
# whatever player it has; the two numbers ride in Server-Timing, the header a browser's own
# network panel already reads.
@router.post("/v1/voices/sample")
async def sample(
    said: VoiceSample,
    key: PipelineKeyDep,
    settings: SettingsDep,
    vault: VaultDep,
    sampling: SamplingDep,
    sampler: SamplerDep,
) -> Response:
    """The words in that voice, as a WAV: 422 a typo, 429 too many, 503 no key, 502 no answer."""
    if not sampling.allowed(key.key_id):
        raise HTTPException(429, TOO_MANY.format(key=key.key_id, count=SAMPLES_A_MINUTE))
    text = said.text if said.text else a_line_for(said.language)
    if len(text) > TEXT_CEILING:
        raise HTTPException(422, TOO_LONG.format(length=len(text), ceiling=TEXT_CEILING))
    # The three words go through the same reading the settings door gives them, so a sample that
    # plays is a setting that saves: a vendor this build has no row for, a model it would quietly
    # swap, a voice that is a typo, are all refused here in the same sentence.
    try:
        voice = the_voice(said.tts, said.voice, said.model)
    except DeclarationRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    assert voice is not None  # the_voice answers None only when all three words are None
    asked = Asked(
        settings=settings,
        model=voice.model,
        language=said.language,
        voice_id=voice.voice_id,
        keys=await keys_brought_by(vault, key.org),
    )
    try:
        heard = await sampler(voice.provider, asked, text)
    except NoProvider as missing:
        raise HTTPException(503, str(missing)) from missing
    except SampleRefused as refused:
        raise HTTPException(
            _a_status(refused.status), _said_by(voice.provider, refused)
        ) from refused
    timing = f"first-audio;dur={heard.first_audio_ms}, total;dur={heard.total_ms}"
    return Response(content=heard.wav, media_type=WAV, headers={"Server-Timing": timing})


# What a vendor's status means to whoever knocked here. A 401 or 403 from the vendor is about the
# key this org runs on — its own, or the box's — and the org (or the operator) is who fixes it;
# any other 4xx is about the words that were sent, a voice or a model the vendor does not have;
# and a 5xx, a timeout or no status at all is the vendor not answering, which is a bad gateway.
def _a_status(status: int | None) -> int:
    """The status this door answers with for the one the vendor gave."""
    if status in (401, 403):
        return 409
    if status is not None and 400 <= status < 500:
        return 422
    return 502


def _said_by(vendor: str, refused: SampleRefused) -> str:
    """The sentence, with the key named when it is the key the vendor refused."""
    if refused.status in (401, 403):
        return THE_KEY_WAS_REFUSED.format(vendor=vendor, why=refused)
    return str(refused)
