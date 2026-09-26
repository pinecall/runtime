"""POST /v1/agents/{slug}/dev/…: a console's ask, relayed to the app standing in the directory."""

from __future__ import annotations

import asyncio
from typing import get_args
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.agents.handlers import Socket, handles, parse_command
from pinecall.api.agents.registry import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP, RegistryDep
from pinecall.api.deps import (
    CallsKeyDep,
    EvalsKeyDep,
    KnowledgeKeyDep,
    MemoryKeyDep,
    TalkKeyDep,
)
from pinecall.api.live import LiveDep
from pinecall.auth.keys import KeyRecord, is_held_by
from pinecall.log.entry import ephemeral_entry
from pinecall.types import JsonObject
from pinecall_protocol import Command
from pinecall_protocol.commands import DevAnswer
from pinecall_protocol.defs import DevVerb
from pinecall_protocol.events import DevRequest

router = APIRouter()

# The verbs, as the wire closes the set; the door refuses a word that is not one before any app
# is asked. Grouped by the scope the console needs to ask them, which is the scope of the thing
# each family is about: a written call is `talk`, the base is `knowledge`, the goldens are `evals`,
# the panel beside a conversation is `calls`.
# The path names both — `/dev/evals/goldens.run` — because the family is the door's scope and the
# verb is the wire's word, and a verb asked under the wrong family is a 404 that says so.
VERBS: frozenset[str] = frozenset(get_args(DevVerb.__value__))
FAMILIES: dict[str, frozenset[str]] = {
    "chat": frozenset({"chat.roster", "chat.start", "chat.say", "chat.end"}),
    "knowledge": frozenset({"knowledge.roster", "knowledge.push", "knowledge.eval"}),
    "memory": frozenset({"memory.roster", "memory.eval", "memory.extraction"}),
    # The panel drawn beside a conversation is read where the conversations are read, so it is
    # asked for with the scope that opens them and not with the developer's `evals`.
    "view": frozenset({"view.render"}),
    "evals": VERBS
    - frozenset({"chat.roster", "chat.start", "chat.say", "chat.end"})
    - frozenset({"knowledge.roster", "knowledge.push", "knowledge.eval"})
    - frozenset({"memory.roster", "memory.eval", "memory.extraction"})
    - frozenset({"view.render"}),
}

# How long the door waits for the app. A push reads a folder and a suite waits for its run's row
# to appear (twenty seconds on the app's side); a golden's extraction is one model call per case.
# Two minutes is past every one of those and still a bounded wait at a browser's door.
ANSWERED_WITHIN_S = 120.0

NO_SUCH_VERB = "no dev verb {verb} in {family}: the verbs are {verbs}"
NO_ANSWER = "the app holding agent {slug} did not answer {verb} within {seconds:.0f}s"
APP_LEFT = "the app holding agent {slug} disconnected before it answered {verb}"

# Which of the org's processes the console asks, when it names one — `?app=` as the chat door
# takes it, so a developer's own terminal is the one that mounts the class.
APP = Query(None, description="which app socket answers, as agent.registered named it")

# The dev.answer named nothing waiting: it came late, twice, or to the wrong gateway.
NOBODY_ASKED = "no_session"


@router.post("/v1/agents/{slug}/dev/chat/{verb}")
async def chat(
    slug: str,
    verb: str,
    said: JsonObject,
    key: TalkKeyDep,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None = APP,
) -> JsonObject:
    """A written call to the class mounted in the agent's directory: open, say, end, the states."""
    return await _relayed("chat", verb, slug, said, key, registry, live, app)


@router.post("/v1/agents/{slug}/dev/knowledge/{verb}")
async def knowledge(
    slug: str,
    verb: str,
    said: JsonObject,
    key: KnowledgeKeyDep,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None = APP,
) -> JsonObject:
    """The agent's knowledge folder, pushed from where it is, and its golden asked."""
    return await _relayed("knowledge", verb, slug, said, key, registry, live, app)


@router.post("/v1/agents/{slug}/dev/memory/{verb}")
async def memory(
    slug: str,
    verb: str,
    said: JsonObject,
    key: MemoryKeyDep,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None = APP,
) -> JsonObject:
    """The agent's two memory goldens, run from the directory that holds them."""
    return await _relayed("memory", verb, slug, said, key, registry, live, app)


@router.post("/v1/agents/{slug}/dev/view/{verb}")
async def view(
    slug: str,
    verb: str,
    said: JsonObject,
    key: CallsKeyDep,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None = APP,
) -> JsonObject:
    """The panel the agent draws about one conversation, rendered by the app that holds it."""
    return await _relayed("view", verb, slug, said, key, registry, live, app)


@router.post("/v1/agents/{slug}/dev/evals/{verb}")
async def evals(
    slug: str,
    verb: str,
    said: JsonObject,
    key: EvalsKeyDep,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None = APP,
) -> JsonObject:
    """The personas and a simulation, the goldens and a suite, a candidate, drift, reproductions."""
    return await _relayed("evals", verb, slug, said, key, registry, live, app)


# The chat door's question, asked the chat door's way: which of the org's sockets serves this, in
# the key's world. A console — takes_unclaimed false — is skipped unless it is named, because the
# process that mounts the class for a written call is the one a person is standing in.
async def _relayed(
    family: str,
    verb: str,
    slug: str,
    said: JsonObject,
    key: KeyRecord,
    registry: RegistryDep,
    live: LiveDep,
    app: str | None,
) -> JsonObject:
    """One ask down the app's socket, and its answer back, or the refusal in the app's words."""
    if verb not in FAMILIES[family]:
        raise HTTPException(
            404, NO_SUCH_VERB.format(verb=verb, family=family, verbs=sorted(FAMILIES[family]))
        )
    held = registry.serving(key.env, slug, app, is_held_by(key))
    if held is None or held.org != key.org:
        if app is not None:
            raise HTTPException(409, NOT_THAT_APP.format(app=app, slug=slug))
        if registry.of(key.env, slug, is_held_by(key)) is not None:
            raise HTTPException(409, NO_UNCLAIMED.format(slug=slug))
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    id = f"dev_{uuid4().hex[:12]}"
    request = DevRequest(id=id, verb=_a_verb(verb), data=dict(said))
    waiting = live.asked(id)
    if not await live.tell(held.owner, ephemeral_entry("dev.request", request, agent=slug)):
        live.forget_asked(id)
        raise HTTPException(502, APP_LEFT.format(slug=slug, verb=verb))
    try:
        answer = await asyncio.wait_for(waiting, ANSWERED_WITHIN_S)
    except TimeoutError:
        live.forget_asked(id)
        raise HTTPException(
            504, NO_ANSWER.format(slug=slug, verb=verb, seconds=ANSWERED_WITHIN_S)
        ) from None
    if answer.refused is not None:
        raise HTTPException(answer.refused.status, answer.refused.detail)
    return answer.result or {}


# The app answered. Whoever asked is awaiting the future by the id; an answer nobody waits for is
# told so in the protocol's own words, exactly as a tool.result nobody waits for is.
@handles("dev.answer")
async def take_a_dev_answer(socket: Socket, command: Command) -> None:
    """What the verb produced in the app's own process, handed to the door awaiting it."""
    answer = parse_command(command, DevAnswer)
    if socket.live.dev_answered(answer):
        return
    await socket.refuse(
        command.agent,
        NOBODY_ASKED,
        f"no dev.request {answer.id} is waiting: it lapsed, it was answered already, or it was "
        "asked of another gateway",
        command.model_dump(),
    )


def _a_verb(word: str) -> DevVerb:
    """The checked word as the wire's closed type: the door refused anything else above."""
    verbs: dict[str, DevVerb] = {verb: verb for verb in get_args(DevVerb.__value__)}
    return verbs[word]
