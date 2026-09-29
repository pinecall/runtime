"""The providers doors: the catalogue, the org's own vendor keys, a vendor's voices, a sample."""

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Query, Response

from pinecall.domain.agent import Voice
from pinecall.domain.errors import DeclarationRefused, NotFound, TooManyRequests
from pinecall.domain.names import Credentials
from pinecall.gateway._call_setup import keys_of
from pinecall.gateway._deps import GatewayDep, PipelineKey, ProvidersKey, ScopeDep
from pinecall.providers import build, catalog, credentials, voices
from pinecall.providers.build import Modality, primary
from pinecall.providers.catalog import Providers
from pinecall.providers.credentials import Keyring
from pinecall.providers.declared import model_of
from pinecall.tenancy import vault
from pinecall.wire.rest.providers import (
    Availability,
    Catalogue,
    ListedVoice,
    ProviderKeyRequest,
    ProviderRow,
    Standing,
    VendorsResponse,
    VoiceSampleRequest,
    VoicesListed,
)

router = APIRouter()


WAV = "audio/wav"


# Samples cost vendor time and write no usage row, so a key gets this many a minute.
SAMPLES_A_MINUTE = 30


# Long enough for a greeting, short enough to keep the vendor's cost to seconds.
LONGEST_SAMPLE = 400


TOO_LONG = "a sample says a sentence: {length} characters, and the ceiling is {ceiling}"


TOO_MANY = "this key asked for its {count} samples this minute: the ceiling is {count} a minute"


NO_CREDENTIALS = "a key, or the credentials object the vendor's plugin takes, and not both"


NO_SUCH_KEY = "this org brought no key for {vendor}"


# What a voice says when the picker names no line and the providers row has none in the language.
A_LINE = "Hello, this is the voice you are listening to."


# Availabilities a call can run on.
READY = frozenset({"yours", "offered"})


# v1's word for each, which the console draws: a vendor nobody keyed for this org has "no key".
STANDING: dict[Availability, Standing] = {
    "yours": "ready",
    "offered": "ready",
    "bring your own": "no key",
    "broken": "no plugin",
}


# The same for every key of an org: what is installed, and whose key would run each vendor.
@router.get("/v1/providers")
async def catalogue(_key: ProvidersKey, scope: ScopeDep, gateway: GatewayDep) -> Catalogue:
    """Every vendor this build runs and how this org may run it, the defaults, the models named."""
    pool = gateway.connections.pool
    configured = await catalog.providers(pool)
    return catalogue_of(configured, await keys_of(pool, gateway.connections.vault, scope))


# Vendor names only, never a key or a prefix of one.
@router.get("/v1/provider-keys")
async def own_vendors(key: ProvidersKey, gateway: GatewayDep) -> VendorsResponse:
    """The vendors the org brought its own credentials for."""
    return VendorsResponse(vendors=await vault.vendors_of(gateway.connections.pool, key.org))


@router.put("/v1/provider-keys/{vendor}", status_code=204)
async def bring_key(
    vendor: str, body: ProviderKeyRequest, key: ProvidersKey, gateway: GatewayDep
) -> None:
    """Keep the org's own credentials for a vendor; its calls run on them from the next one."""
    named = installed_vendor(vendor)
    connections = gateway.connections
    await vault.put_credentials(
        connections.pool, connections.vault, key.org, named, credentials_of(body)
    )


@router.delete("/v1/provider-keys/{vendor}", status_code=204)
async def take_key_back(vendor: str, key: ProvidersKey, gateway: GatewayDep) -> None:
    """Forget the org's credentials for a vendor; its calls run on the box's from the next one."""
    named = installed_vendor(vendor)
    if not await vault.drop_credentials(gateway.connections.pool, key.org, named):
        raise NotFound(NO_SUCH_KEY.format(vendor=named))


# Built as a call builds the stage: the operator's options, on the key a call would use.
@router.get("/v1/voices")
async def list_voices(
    _key: PipelineKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    tts: Annotated[str, Query(min_length=1)],
    language: Annotated[str | None, Query()] = None,
) -> VoicesListed:
    """A vendor's own voices, as its plugin lists them; a vendor that lists none is a 404."""
    pool = gateway.connections.pool
    configured = await catalog.providers(pool)
    keyring = await keys_of(pool, gateway.connections.vault, scope)
    stage = credentials.stage("tts", Voice(provider=installed_vendor(tts)), configured, keyring)
    spoken = primary(language)
    listed = await voices.voices(dataclasses.replace(stage, language=spoken))
    return VoicesListed(
        tts=tts,
        language=language,
        voices=[
            voice
            for item in listed
            if (voice := _listed_voice(item)) is not None
            and (spoken is None or not voice.language or voice.language.startswith(spoken))
        ],
    )


# The three words are read exactly as the settings door reads them, so what plays is what saves.
# The answer is the WAV itself, its latencies in Server-Timing, which a browser shows.
@router.post("/v1/voices/sample")
async def voice_sample(
    body: VoiceSampleRequest, key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> Response:
    """The line said by that vendor's voice, over the path a call speaks on."""
    if not gateway.samples.allowed(key.bearer.key.key_id):
        raise TooManyRequests(TOO_MANY.format(count=gateway.samples.tries))
    pool = gateway.connections.pool
    configured = await catalog.providers(pool)
    language = primary(body.language)
    text = body.text or configured.lines.get(language or "", A_LINE)
    if len(text) > LONGEST_SAMPLE:
        raise DeclarationRefused(TOO_LONG.format(length=len(text), ceiling=LONGEST_SAMPLE))
    named = model_of(body.tts, "tts", in_use=configured.defaults["tts"].vendor)
    voice = Voice(
        provider=configured.defaults["tts"].vendor if named is None else named.provider,
        model=body.model or (None if named is None else named.model or None),
        voice_id=body.voice,
    )
    keyring = await keys_of(pool, gateway.connections.vault, scope)
    stage = credentials.stage("tts", voice, configured, keyring)
    heard = await voices.sample(
        dataclasses.replace(stage, voice=voice.voice_id, language=language), text
    )
    timing = f"first-audio;dur={heard.first_audio_ms}, total;dur={heard.total_ms}"
    return Response(content=heard.wav, media_type=WAV, headers={"Server-Timing": timing})


def catalogue_of(configured: Providers, keyring: Keyring) -> Catalogue:
    """Every installed vendor as this org may run it, with the row's defaults and models."""
    return Catalogue(
        providers=[
            ProviderRow(
                name=vendor.name,
                does=list(vendor.does),
                aliases=[],
                note=vendor.broken or "",
                standing=STANDING[vendor.availability],
                ready=vendor.availability in READY,
                env=None,
                extra=vendor.name,
                voices_listed=_lists_voices(vendor.name, vendor.does),
                availability=vendor.availability,
                broken=vendor.broken,
            )
            for vendor in credentials.readiness(build.installed(), keyring)
        ],
        defaults={modality: stage.vendor for modality, stage in configured.defaults.items()},
        voices=[],
        models={named: [model] for named, model in configured.models.items()},
    )


def installed_vendor(vendor: str) -> str:
    """The vendor's name as installed; refused naming the ones this build runs."""
    named = vendor.strip().lower()
    if named not in build.installed():
        known = ", ".join(sorted(build.installed()))
        raise DeclarationRefused(f"no vendor named {vendor!r}; this build runs: {known}")
    return named


def credentials_of(body: ProviderKeyRequest) -> Credentials:
    """One key, or the object the plugin takes, out of the body; refused when neither or both."""
    if (body.key is None) == (body.credentials is None):
        raise DeclarationRefused(NO_CREDENTIALS)
    if body.key is not None:
        if not body.key.strip():
            raise DeclarationRefused(NO_CREDENTIALS)
        return body.key.strip()
    return body.credentials or {}


# A plugin lists its voices when its TTS class has a `list_voices`; nothing is built to ask.
def _lists_voices(vendor: str, does: tuple[Modality, ...]) -> bool:
    if "tts" not in does:
        return False
    try:
        speech = getattr(build.plugin(vendor), "TTS", None)
    except DeclarationRefused:
        return False
    return callable(getattr(speech, "list_voices", None))


def _listed_voice(item: voices.ListedVoice) -> ListedVoice | None:
    detail = item.detail
    return ListedVoice(
        id=item.id,
        name=item.name,
        language=str(detail.get("language") or ""),
        description=str(detail.get("description") or ""),
        gender=str(detail.get("gender") or ""),
        country=str(detail.get("country") or ""),
        accent=str(detail.get("accent") or ""),
    )
