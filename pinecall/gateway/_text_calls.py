"""Written calls on the gateway: opened on the socket that holds the agent, or taken up again."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from pinecall.domain.agent import AgentConfig, Versions
from pinecall.domain.call import CallContext, Route, today_in
from pinecall.domain.names import CHANNELS_WITH_A_NUMBER, JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals import goldens
from pinecall.gateway._call_setup import keys_of, tuned
from pinecall.gateway._served import Served, Serving, attach, looked_up, served_call
from pinecall.gateway._sockets import Registration
from pinecall.gateway.ending.seal import sealed
from pinecall.log import private, queries
from pinecall.log.store import Claim
from pinecall.providers import catalog
from pinecall.providers.build import Running
from pinecall.providers.credentials import thinking
from pinecall.session import text
from pinecall.session.call import Call, Platform
from pinecall.session.session import Session
from pinecall.session.text import text_session
from pinecall.tenancy import admission
from pinecall.wire.events import (
    TERMINAL_EVENT,
    CallStarted,
)
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import LLMModelUsage, ModelUsage
from pinecall.wire.parts import PlatformTool
from pinecall.wire.rest.calls import LookupRequest, SealCallRequest


@dataclass(frozen=True)
class TextSetup:
    """What a written call runs on: the tuned config, its versions, the model, a golden's facts."""

    config: AgentConfig
    versions: Versions
    model: Running
    # A golden's facts, which recall answers instead of the contact's memory; None on a real call.
    recalled: tuple[str, ...] | None = None


async def open_text(serving: Serving, registration: Registration, context: CallContext) -> Session:
    """A new written call on the socket that holds the agent, admitted and unstarted."""
    pool, scope = serving.connections.pool, registration.scope
    configured = await catalog.providers(pool)
    config, versions = await tuned(pool, registration.config, scope, configured)
    model = thinking(config, configured, await keys_of(pool, serving.connections.vault, scope))
    return await open_text_as(serving, registration, context, TextSetup(config, versions, model))


# Admission before the log is claimed: a refused call leaves nothing behind.
async def open_text_as(
    serving: Serving, registration: Registration, context: CallContext, setup: TextSetup
) -> Session:
    """A new written call on this setup, admitted and unstarted."""
    pool, scope = serving.connections.pool, registration.scope
    await admission.admit_call(
        pool, scope.org, scope.env, running=serving.live.running(scope.org, scope.env)
    )
    await serving.logs.store.claim(
        context.call, registration.slug, scope.org, Claim(scope, setup.versions)
    )
    served = served_call(serving, registration.owner, context, setup.config, scope)
    return _session(serving, served, setup.model, setup.recalled)


# A gateway that restarted forgot the call, not the caller: no admission, no second greeting.
async def resume_text(
    serving: Serving, registration: Registration, call: str, zone: str
) -> Session | None:
    """A written call taken up again from its log; None for one that is over or not this agent's."""
    kept = await queries.scope_of_call(serving.connections.pool, call)
    if kept is None or kept.sealed or kept.scope is None or kept.agent != registration.slug:
        return None
    if kept.scope.org != registration.scope.org:
        return None
    # The model reads the tools' arguments as they were sent: the private ones opened.
    entries = await private.opened_whole(serving.logs.store, serving.connections.vault, call)
    if not entries or any(entry.type == TERMINAL_EVENT for entry in entries):
        return None
    configured = await catalog.providers(serving.connections.pool)
    config, _ = await tuned(serving.connections.pool, registration.config, kept.scope, configured)
    model = thinking(
        config,
        configured,
        await keys_of(serving.connections.pool, serving.connections.vault, kept.scope),
    )
    context = _as_it_opened(call, registration, kept.scope, entries, today_in(zone))
    served = served_call(serving, None, context, config, kept.scope)
    session = _session(serving, served, model, None)
    # The session's batches go on from what the log already took from it, never from zero.
    session.call.writing.after = await serving.logs.store.written(call)
    await text.resume(session, text.taken_up(entries))
    await attach(serving.live, call, registration.owner)
    return session


def tokens_of(usage: Iterable[ModelUsage]) -> int:
    """The model tokens a call used so far: the llm_tokens quota counts these."""
    return sum(
        (used.input_tokens or 0) + (used.output_tokens or 0)
        for used in usage
        if isinstance(used, LLMModelUsage)
    )


def _session(
    serving: Serving, served: Served, model: Running, recalled: tuple[str, ...] | None
) -> Session:
    async def lookup(tool: PlatformTool, arguments: JsonObject, speech: str | None) -> JsonObject:
        request = LookupRequest(tool=tool, input=arguments, speech_id=speech)
        return await looked_up(serving, served, request)

    async def seal(usage: list[ModelUsage], outcome: str) -> None:
        await sealed(serving, served, SealCallRequest(usage=usage, outcome=outcome))

    looking = lookup if recalled is None else goldens.golden_lookup(recalled, lookup)
    platform = Platform(
        append_many=served.log.append_many, tool=served.tools.ran, lookup=looking, seal=seal
    )
    session = text_session(Call(served.context, served.config, platform), model)
    serving.live.calls[served.call] = replace(served, session=session)
    return session


def _as_it_opened(
    call: str, registration: Registration, scope: Scope, entries: Iterable[Entry], today: date
) -> CallContext:
    started = next((entry for entry in entries if entry.type == "call.started"), None)
    data = None if started is None else CallStarted.model_validate(started.data)
    caller = "" if data is None else (data.from_ or "")
    # A WhatsApp conversation comes back on its number; a chat at none.
    channel = "web" if data is None else data.channel
    number = data.to if data is not None and channel in CHANNELS_WITH_A_NUMBER else None
    route = Route(
        org=scope.org, agent=registration.slug, channel=channel, number=number, env=scope.env
    )
    if data is not None:
        today = datetime.fromtimestamp(data.started_at, tz=UTC).date()
    return CallContext(
        call=call,
        channel=channel,
        direction="inbound",
        caller=caller or call,
        route=route,
        today=today,
        holder=scope.holder or None,
    )
