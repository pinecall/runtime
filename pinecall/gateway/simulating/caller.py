"""A simulated caller: a new call claimed for it, its model, its voice, and its next line."""

import dataclasses
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Never

from livekit import rtc
from livekit.agents import llm, tts

from pinecall.channels import rooms
from pinecall.domain.agent import AgentConfig, Voice
from pinecall.domain.call import CallContext, Route, today_in
from pinecall.domain.errors import Conflict, NotAvailable
from pinecall.domain.names import THE_WIDGET, Json, JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals import simulated, spoken
from pinecall.evals.callers import Spending, heard_in, improvise_line
from pinecall.evals.turns import NextLine, is_call_over
from pinecall.fleet import worlds
from pinecall.gateway._call_setup import keys_of, tuned
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import Registration
from pinecall.gateway._text_calls import open_text
from pinecall.log import queries
from pinecall.log.store import Claim
from pinecall.providers import catalog, credentials
from pinecall.providers.build import Running, llm_of, tts_of
from pinecall.providers.declared import model_of
from pinecall.session.session import Session
from pinecall.tenancy import tokens
from pinecall.wire.rest.evals import CallerPersona, NextLineRequest

NO_LINE = "the simulated call could not be held: {broke}"


# A call id names a room, a log and a recording: a simulated caller is put on a new one alone.
NOT_A_NEW_CALL = (
    "call {call} exists already: a simulated caller is placed on a call id nobody opened"
)


# Longer than any run, so the caller's token never ends a call first.
A_CALL_MAY_LAST_S = 15 * 60.0


@dataclass(frozen=True)
class Caller:
    """Who plays a simulated call: the persona, its model, what it has spent, its voice spoken."""

    persona: CallerPersona
    model: llm.LLM[Never]
    spending: Spending
    speech: tts.TTS[Never] | None = None


@dataclass(frozen=True)
class Placed:
    """A simulated call to place: its id, the agent, who plays it, how long, on what line."""

    call: str
    agent: str
    persona: CallerPersona
    turns: int
    line: spoken.Line = field(default_factory=spoken.Line)
    state: JsonObject = field(default_factory=dict[str, Json])


# The head is claimed in the caller's scope before the room is offered, so the worker opens it
# there or not at all (api/calls.py _unclaimed_or_in), and another org's call can never be named.
async def a_new_call(gateway: Gateway, call: str, agent: str, scope: Scope) -> None:
    """Claim a new call in the caller's scope; refused for an id somebody already opened."""
    await new_call(gateway, call)
    await gateway.logs.store.claim(call, agent, scope.org, Claim(scope))


# ── the caller ──


@asynccontextmanager
async def caller_model(
    gateway: Gateway, scope: Scope, persona: CallerPersona
) -> AsyncGenerator[llm.LLM[Never]]:
    """The model that improvises the caller's lines: the persona's, else the box's default."""
    pool = gateway.connections.pool
    configured = await catalog.providers(pool)
    declared = model_of(persona.llm or None, "llm", in_use=configured.defaults["llm"].vendor)
    keys = await keys_of(pool, gateway.connections.vault, scope)
    model = llm_of(credentials.stage("llm", declared, configured, keys))
    try:
        yield model
    finally:
        await model.aclose()


# The caller speaks in the agent's language and never in the agent's own voice.
async def caller_voice(
    gateway: Gateway, scope: Scope, agent: str, persona: CallerPersona | None
) -> Running:
    """The caller's voice: the persona's, else one of the vendor's that is not the agent's."""
    pool = gateway.connections.pool
    configured = await catalog.providers(pool)
    holding = gateway.sockets.of(scope, agent)
    config = (
        AgentConfig(slug=agent)
        if holding is None or holding.scope.org != scope.org
        else (await tuned(pool, holding.config, holding.scope, configured))[0]
    )
    in_use = configured.defaults["tts"].vendor
    named = None if persona is None else model_of(persona.tts or None, "tts", in_use=in_use)
    vendor = in_use if named is None else named.provider
    agents_voice = None if config.voice is None else config.voice.voice_id
    chosen = (persona.voice if persona is not None else None) or spoken.pick_caller_voice(
        configured.voices, vendor, agents_voice, config.language
    )
    model = None if named is None else named.model or None
    wanted = Voice(provider=vendor, model=model, voice_id=chosen)
    keys = await keys_of(pool, gateway.connections.vault, scope)
    stage = credentials.stage("tts", wanted, configured, keys)
    return dataclasses.replace(stage, voice=chosen, language=config.language)


# The room is deleted whatever happens: that ends the agent's job, and its job seals the log.
async def on_the_line(
    gateway: Gateway,
    dispatch: rooms.Dispatch,
    speech: tts.TTS[Never],
    line: spoken.SpokenLine,
    next_line: NextLine,
) -> int:
    """Offer the agent the room, put the caller on it, and play its lines; the lines said."""
    connections = gateway.connections
    world = dispatch.env or "sandbox"
    fleet = worlds.fleet_of(await worlds.fleets(connections.pool), world)
    visitor = tokens.Visitor(
        expires_at=time.time() + A_CALL_MAY_LAST_S, identity=spoken.A_SIMULATED_CALLER
    )
    token = tokens.room_token(gateway.signer, line.call, "talk", visitor)
    joined = dataclasses.replace(line, url=connections.settings.livekit_url_of(world), token=token)
    try:
        await gateway.offering.offer(line.call, fleet, dispatch)
        return await spoken.run_spoken(joined, speech, gateway.logs, next_line)
    except (TimeoutError, rtc.ConnectError) as broke:
        raise NotAvailable(NO_LINE.format(broke=broke)) from broke
    finally:
        await rooms.room_closed(connections.servers[world], line.call)


# Asked before each line: the line that crosses the ceiling is the last one said, and a call the
# agent or a person already ended asks for none. Spoken, the voice is counted with the line.
def improvising(gateway: Gateway, call: str, caller: Caller) -> NextLine:
    """The caller's next line on the call so far, and whether it hangs up after it."""

    async def improvised(turns_left: int) -> tuple[str, bool]:
        entries = await gateway.logs.store.whole(call)
        if is_call_over(entries) or caller.spending.is_over:
            return "", True
        request = NextLineRequest(
            persona=caller.persona, heard=heard_in(entries), turns_left=turns_left
        )
        next_one = await improvise_line(caller.model, request)
        caller.spending.count(next_one, caller.speech)
        return next_one.answer.say, next_one.answer.hangup

    return improvised


async def new_call(gateway: Gateway, call: str) -> None:
    """Refuse a call id somebody already opened: a simulated caller is put on a new one alone."""
    if await queries.scope_of_call(gateway.connections.pool, call) is not None:
        raise Conflict(NOT_A_NEW_CALL.format(call=call))


# The agent is dispatched into a room whoever holds it — a process deployed or a developer's —
# and the caller speaks there in a voice of its own; the room going ends the job, which seals it.
async def spoken_call(gateway: Gateway, scope: Scope, placed: Placed) -> tuple[int, Spending]:
    """Play the persona out loud on the agent's line; the lines said, and what they cost."""
    persona = placed.persona
    dispatch = rooms.Dispatch(
        agent=placed.agent,
        org=scope.org,
        env=scope.env,
        holder=scope.holder or None,
        caller=spoken.A_SIMULATED_CALLER,
        persona=persona.name or None,
        accepts_when=persona.accepts_when or None,
        declines_when=persona.declines_when or None,
        state=dict(placed.state),
    )
    spending = Spending(await catalog.providers(gateway.connections.pool))
    async with caller_model(gateway, scope, persona) as model:
        speech = tts_of(await caller_voice(gateway, scope, placed.agent, persona))
        caller = Caller(persona, model, spending, speech)
        line = spoken.SpokenLine("", "", placed.call, placed.turns, placed.line)
        try:
            turns = await on_the_line(
                gateway, dispatch, speech, line, improvising(gateway, placed.call, caller)
            )
        finally:
            await speech.aclose()
    return turns, spending


# Opened at once, so a refusal — the org's limits, nobody holding the agent — is the request's;
# the conversation runs after, as long as the caller keeps it going.
async def written_call(gateway: Gateway, registration: Registration, placed: Placed) -> Session:
    """A written call to whoever holds the agent, with the persona's rules on it; unstarted."""
    scope, persona = registration.scope, placed.persona
    await new_call(gateway, placed.call)
    context = CallContext(
        call=placed.call,
        channel=THE_WIDGET,
        direction="inbound",
        caller=spoken.A_SIMULATED_CALLER,
        persona=persona.name or None,
        accepts_when=persona.accepts_when or None,
        declines_when=persona.declines_when or None,
        route=Route(org=scope.org, agent=registration.slug, channel=THE_WIDGET, env=scope.env),
        today=today_in(gateway.connections.settings.timezone),
        holder=scope.holder or None,
        state=dict(placed.state),
    )
    return await open_text(gateway.serving, registration, context)


async def written_turns(gateway: Gateway, session: Session, placed: Placed) -> tuple[int, Spending]:
    """The persona's lines said on the written call, until it hangs up; and what they cost."""
    scope = session.call.context.route
    spending = Spending(await catalog.providers(gateway.connections.pool))
    async with caller_model(gateway, Scope(scope.org, scope.env), placed.persona) as model:
        caller = Caller(placed.persona, model, spending)
        lines = await simulated.converse(
            session, gateway.logs, improvising(gateway, placed.call, caller), placed.turns
        )
    return lines, spending
