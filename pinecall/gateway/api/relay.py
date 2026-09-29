"""The console's asks relayed to a running app: chat, knowledge, memory, the view, evals."""

import asyncio
from dataclasses import dataclass
from typing import Annotated, get_args
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from pinecall.domain.errors import (
    AppRefused,
    Conflict,
    NotFound,
)
from pinecall.domain.names import JsonObject
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, CallsKey, GatewayDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP
from pinecall.tenancy import keys
from pinecall.wire.events import DevRequest
from pinecall.wire.parts import DevVerb

router = APIRouter()


NO_SUCH_VERB = "no dev verb {verb} in {family}: the verbs are {verbs}"


NO_ANSWER = "the app holding agent {slug} did not answer {verb} within {seconds:.0f}s"


APP_LEFT = "the app holding agent {slug} disconnected before it answered {verb}"


# Longer than any verb takes: a suite waits 20 s on the app's side.
ANSWERED_WITHIN_S = 120.0


FAMILIES: dict[str, frozenset[str]] = {
    "chat": frozenset({"chat.roster", "chat.start", "chat.say", "chat.end"}),
    "knowledge": frozenset({"knowledge.roster", "knowledge.push", "knowledge.eval"}),
    "memory": frozenset({"memory.roster", "memory.eval", "memory.extraction"}),
    "view": frozenset({"view.render"}),
}


EVERY_VERB: frozenset[str] = frozenset(get_args(DevVerb.__value__))


# FastAPI builds it: the body, and the socket the query names.
@dataclass(frozen=True)
class Ask:
    """What a console asks an app, and which of the agent's sockets it asks."""

    said: JsonObject
    app: Annotated[str | None, Query()] = None


@dataclass(frozen=True)
class Relay:
    """Which family's verb, of which agent."""

    family: str
    slug: str
    verb: str


FAMILIES["evals"] = EVERY_VERB - frozenset().union(*FAMILIES.values())


AskDep = Annotated[Ask, Depends(Ask)]


@router.post("/v1/agents/{slug}/dev/chat/{verb}")
async def relay_chat(
    slug: str, verb: str, ask: AskDep, key: _deps.TalkKey, gateway: GatewayDep
) -> JsonObject:
    """A chat verb, answered by the app holding the agent."""
    return await _relayed(gateway, key, Relay("chat", slug, verb), ask)


@router.post("/v1/agents/{slug}/dev/knowledge/{verb}")
async def relay_knowledge(
    slug: str, verb: str, ask: AskDep, key: _deps.KnowledgeKey, gateway: GatewayDep
) -> JsonObject:
    """A knowledge verb, answered by the app holding the agent."""
    return await _relayed(gateway, key, Relay("knowledge", slug, verb), ask)


@router.post("/v1/agents/{slug}/dev/memory/{verb}")
async def relay_memory(
    slug: str, verb: str, ask: AskDep, key: _deps.MemoryKey, gateway: GatewayDep
) -> JsonObject:
    """A memory verb, answered by the app holding the agent."""
    return await _relayed(gateway, key, Relay("memory", slug, verb), ask)


@router.post("/v1/agents/{slug}/dev/view/{verb}")
async def relay_view(
    slug: str, verb: str, ask: AskDep, key: CallsKey, gateway: GatewayDep
) -> JsonObject:
    """The side panel beside a conversation, rendered by the app."""
    return await _relayed(gateway, key, Relay("view", slug, verb), ask)


@router.post("/v1/agents/{slug}/dev/evals/{verb}")
async def relay_evals(
    slug: str, verb: str, ask: AskDep, key: _deps.EvalsKey, gateway: GatewayDep
) -> JsonObject:
    """An evals verb, answered by the app holding the agent."""
    return await _relayed(gateway, key, Relay("evals", slug, verb), ask)


# dev.request goes down the socket unstored; the app's refusal comes back as its own status.
async def _relayed(gateway: Gateway, key: Acting, relay: Relay, params: Ask) -> JsonObject:
    if relay.verb not in FAMILIES[relay.family]:
        verbs = sorted(FAMILIES[relay.family])
        raise NotFound(NO_SUCH_VERB.format(verb=relay.verb, family=relay.family, verbs=verbs))
    where = keys.scope_of(key.bearer, key.env)
    registration = gateway.sockets.serving(where, relay.slug, params.app)
    if registration is None:
        if params.app is not None:
            raise Conflict(NOT_THAT_APP.format(app=params.app, slug=relay.slug))
        if gateway.sockets.of(where, relay.slug) is not None:
            raise Conflict(NO_UNCLAIMED.format(slug=relay.slug))
        raise NotFound(NO_AGENT.format(slug=relay.slug))
    ask_id = f"dev_{uuid4().hex[:12]}"
    request = DevRequest.model_validate({"id": ask_id, "verb": relay.verb, "data": params.said})
    waiting = gateway.live.ask(ask_id)
    if not await gateway.live.tell(
        registration.owner, _deps.ephemeral_entry(relay.slug, request, kind="dev.request")
    ):
        gateway.live.pending_answers.pop(ask_id, None)
        raise AppRefused(502, APP_LEFT.format(slug=relay.slug, verb=relay.verb))
    try:
        answer = await asyncio.wait_for(waiting, ANSWERED_WITHIN_S)
    except TimeoutError:
        gateway.live.pending_answers.pop(ask_id, None)
        said_late = NO_ANSWER.format(slug=relay.slug, verb=relay.verb, seconds=ANSWERED_WITHIN_S)
        raise AppRefused(504, said_late) from None
    if answer.refused is not None:
        raise AppRefused(answer.refused.status, answer.refused.detail)
    return answer.result or {}
