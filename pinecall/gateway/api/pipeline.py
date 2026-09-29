"""An agent's pipeline: what it hears, decides and speaks with, how fast, and its hold melody."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from pinecall.domain.agent import AgentConfig, Greeting
from pinecall.domain.errors import DeclarationRefused, NotFound, PinecallError
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import keys_of, tuned
from pinecall.gateway._deps import GatewayDep, PipelineKey, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.api.providers import catalogue_of
from pinecall.log.reduce import medians, reduce
from pinecall.providers import catalog, credentials
from pinecall.providers.build import Modality, primary
from pinecall.providers.credentials import Keyring
from pinecall.session import hold
from pinecall.tenancy import agents
from pinecall.tenancy.agents import Chosen, Clip
from pinecall.wire.parts import GreetingConfig
from pinecall.wire.rest.agents import (
    HoldAudio,
    Measured,
    PipelineReport,
    PipelineStage,
    PlayedRequest,
)
from pinecall.wire.state import Turn

router = APIRouter()


# Medians pool the turns of this many calls, so a short call weighs as much as its turns.
LAST_CALLS = 20


# The whole file in one request; a five-minute mp3 is under 10 MB.
LONGEST_UPLOAD = 20 * 1024 * 1024


NO_CLIP = "agent {slug} plays no clip of its own: the box's melody is the worker's, off or default"


OGG = "audio/ogg"


TOO_BIG = f"a hold melody is {LONGEST_UPLOAD // (1024 * 1024)} MB at most as uploaded"


# The stages are the ones the next call would be built with: the declaration under the scope's
# settings. Nothing is built: a vendor without a key is a reason, not a failure.
@router.get("/v1/agents/{slug}/pipeline")
async def pipeline_report(
    slug: str, _key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> PipelineReport:
    """The agent's three stages, the catalogue, and the latencies of its last calls."""
    pool = gateway.connections.pool
    registration = gateway.sockets.of(scope, slug)
    declared = AgentConfig(slug=slug) if registration is None else registration.config
    configured = await catalog.providers(pool)
    config, _ = await tuned(pool, declared, scope, configured)
    keyring = await keys_of(pool, gateway.connections.vault, scope)
    language = primary(config.language)
    stages = {
        "hears": _stage("stt", config, configured, language=language),
        "decides": _stage("llm", config, configured, language=None),
        "speaks": _stage("tts", config, configured, language=language),
    }
    listed = catalogue_of(configured, keyring)
    turns = await _recent_turns(gateway, slug)
    return PipelineReport(
        agent=slug,
        hears=stages["hears"],
        decides=stages["decides"],
        speaks=stages["speaks"],
        greeting=_greeting(config.greeting),
        voices=listed.voices,
        providers=listed.providers,
        defaults=listed.defaults,
        models=listed.models,
        calls=turns[0],
        medians=[
            Measured(name=row.name, seconds=row.seconds, turns=row.turns)
            for row in medians(turns[1])
        ],
        unavailable_reasons=_unavailable(stages, keyring),
    )


@router.get("/v1/agents/{slug}/pipeline/hold-audio")
async def hold_audio(
    slug: str, _key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> HoldAudio:
    """What the agent plays while a tool runs: the box's melody, silence, or a clip of its own."""
    return hold_audio_row(await agents.hold_of(gateway.connections.pool, _of(scope), slug))


# The body is the file, no multipart; the name rides the query. Converted here, once, so no
# worker decodes a tenant's upload in the middle of a call.
@router.put("/v1/agents/{slug}/pipeline/hold-audio")
async def upload_hold_audio(
    slug: str,
    request: Request,
    _key: PipelineKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    name: Annotated[str | None, Query(max_length=200)] = None,
) -> HoldAudio:
    """A file of the org's as the agent's melody, from the next call on."""
    data = await request.body()
    if len(data) > LONGEST_UPLOAD:
        raise DeclarationRefused(TOO_BIG)
    melody = await asyncio.to_thread(hold.converted, data)
    clip = Clip(audio=melody.audio, seconds=melody.seconds, name=(name or "").strip() or "melody")
    chosen = await agents.keep_hold(gateway.connections.pool, _of(scope), slug, clip)
    return hold_audio_row(chosen)


@router.get("/v1/agents/{slug}/pipeline/hold-audio/audio")
async def hold_audio_clip(
    slug: str, _key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> Response:
    """The agent's own clip, Ogg Opus, to hear before a caller does."""
    audio = await agents.hold_audio(gateway.connections.pool, _of(scope), slug)
    if audio is None:
        raise NotFound(NO_CLIP.format(slug=slug))
    return Response(audio, media_type=OGG)


@router.put("/v1/agents/{slug}/pipeline/hold-audio/played")
async def choose_hold_audio(
    slug: str, body: PlayedRequest, _key: PipelineKey, scope: ScopeDep, gateway: GatewayDep
) -> HoldAudio:
    """The box's melody back, or silence; an uploaded clip is forgotten either way."""
    pool = gateway.connections.pool
    if body.played == "default":
        await agents.forget_hold(pool, _of(scope), slug)
        return HoldAudio(played="default")
    await agents.silence_hold(pool, _of(scope), slug)
    return HoldAudio(played="off")


def hold_audio_row(chosen: Chosen | None) -> HoldAudio:
    """The stored choice as the doors send it; no row is the box's melody."""
    if chosen is None:
        return HoldAudio(played="default")
    return HoldAudio(
        played=chosen.played, sha256=chosen.sha256, seconds=chosen.seconds, name=chosen.name
    )


def _stage(
    modality: Modality, config: AgentConfig, configured: catalog.Providers, *, language: str | None
) -> PipelineStage:
    default = configured.defaults[modality]
    if modality == "tts":
        voice = config.voice
        return PipelineStage(
            vendor=default.vendor if voice is None else voice.provider,
            model=None if voice is None else voice.model,
            voice_id=None if voice is None else voice.voice_id,
            language=language,
        )
    declared = config.stt if modality == "stt" else config.llm
    return PipelineStage(
        vendor=default.vendor if declared is None else declared.provider,
        model=None if declared is None else declared.model or None,
        language=language,
    )


def _greeting(greeting: Greeting | None) -> GreetingConfig | None:
    if greeting is None:
        return None
    return GreetingConfig(
        say=greeting.say, reply=greeting.reply, allow_interruptions=greeting.allow_interruptions
    )


async def _recent_turns(gateway: Gateway, slug: str) -> tuple[int, list[Turn]]:
    store = gateway.logs.store
    calls = await store.newest_calls(LAST_CALLS, agent=slug)
    turns: list[Turn] = []
    for call in calls:
        turns += reduce(await store.whole(call)).turns
    return len(calls), turns


# Which stage has no key to run on, and why, before a call fails for want of it.
def _unavailable(stages: dict[str, PipelineStage], keyring: Keyring) -> dict[str, str]:
    missing: dict[str, str] = {}
    for where, stage in stages.items():
        try:
            credentials.running(keyring, stage.vendor, stage.model)
        except PinecallError as refused:
            missing[where] = str(refused)
    return missing


# The melody and the widget are the agent's in a world, whoever holds it.
def _of(scope: Scope) -> Scope:
    return Scope(scope.org, scope.env)
