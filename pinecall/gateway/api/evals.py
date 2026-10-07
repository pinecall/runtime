"""The eval doors: suites and their runs, a call replayed or judged, the simulated caller."""

import asyncio
import dataclasses
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Never

from fastapi import APIRouter, Path, Query
from livekit import rtc
from livekit.agents import llm, tts
from pydantic import BaseModel, Field

from pinecall.channels import rooms
from pinecall.domain.agent import AgentConfig, Model, Voice
from pinecall.domain.call import CallContext, Route, new_call_id, today_in
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    QuotaExhausted,
)
from pinecall.domain.names import PRODUCTION, THE_WIDGET
from pinecall.domain.scope import Scope
from pinecall.evals import checks, dataset, goldens, runs, spoken
from pinecall.evals.callers import Spending, heard_in, improvise_line
from pinecall.evals.case import case_of
from pinecall.fleet import worlds
from pinecall.gateway import _deps
from pinecall.gateway._call_setup import exhausted, keys_of, tuned
from pinecall.gateway._deps import EvalsKey, GatewayDep, ScopeDep, check_paced
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import NO_AGENT, Registration
from pinecall.gateway._text_calls import TextSetup, open_text_as
from pinecall.gateway.ending.seal import compliance_of, drifted, judge_of, judged_call
from pinecall.log import queries
from pinecall.log.store import Claim
from pinecall.providers import catalog, credentials
from pinecall.providers.build import Running, llm_of, tts_of
from pinecall.providers.catalog import Providers
from pinecall.providers.credentials import Keyring, thinking
from pinecall.providers.declared import model_of
from pinecall.tenancy import judges, personas, scopes, tokens
from pinecall.tenancy.keys import check_agent
from pinecall.tenancy.scopes import Picked
from pinecall.wire.parts import ModelConfig
from pinecall.wire.rest.evals import (
    CallerPersona,
    EvalRunList,
    EvalRunResponse,
    Golden,
    NextLineRequest,
    NextLineResponse,
    OpenedCall,
    PlaceVoiceCallRequest,
    PlaceVoiceCallResponse,
    ReplayCallRequest,
    ReplayCallResponse,
    RunSuiteRequest,
    ScoreRow,
)
from pinecall.wire.scores import CallScore

router = APIRouter()


A_SCREENFUL = 20


NO_SUCH_RUN = "no eval run {id} in this key's org and world"


STILL_GOING = "call {call} is still going: it is judged when it hangs up"


ALREADY_JUDGED = (
    "call {call} was judged at hang-up: ?again=true judges it again, and pays for it again"
)


# Without the app the rest would run against a bare model: no view, no tools.
THE_APP_LEFT = (
    "the app detached after {done} of {total} goldens ({slug}): the socket holding the agent "
    "closed, and a conversation with no app behind it drives a model with no view and no tools"
)


# The worker builds a spoken session from the declaration: a column per model cannot hold.
ONE_MODEL_OUT_LOUD = (
    "--voice runs the model the agent declares: the worker builds the session, so a run cannot "
    "put a golden through a second model out loud. Drop --model, or drop --voice."
)


# A dispatch carries no date: the worker's clock is the real day's.
NO_PINNED_DAY_OUT_LOUD = (
    "golden {name} pins today={day}, and a spoken call runs on the real day: the worker seeds its "
    "clock when the room opens. Run this golden without --voice."
)


NO_LINE = "the simulated call could not be held: {broke}"


# A call id names a room, a log and a recording: a simulated caller is put on a new one alone.
NOT_A_NEW_CALL = (
    "call {call} exists already: a simulated caller is placed on a call id nobody opened"
)


CASES_IN_THE_SANDBOX = (
    "cases are real callers' words played again: they run in the sandbox, against the app a "
    "developer holds there, never through production's, whose tools act for real"
)


CASES_WRITTEN = "cases are played as written calls: drop --voice, or play only goldens out loud"


# Every call of a run is a simulated caller and an agent the box may pay for: goldens and cases,
# each played once under every model.
CALLS_A_RUN = 200


TOO_MANY_CALLS = (
    "a run plays 200 calls at most, and {played} goldens and cases under {models} models are "
    "{total}: run fewer, or fewer models"
)


NO_SUCH_VERSION = "no version {version} of {slug} in the scope of the app that holds it"


# A caller's line is a model call: a call of forty turns says forty, and several run side by side.
LINES_A_MINUTE = 120


TOO_MANY_LINES = "more than 120 caller lines this minute from this org: try again in a minute"


# The worker writes call.summary and call.score after the caller leaves.
A_SEAL_MAY_TAKE_S = 25.0


NEVER_SEALED = (
    "the spoken call {call} never sealed within {seconds:.0f}s: there is nothing to judge"
)


# Longer than any run, so the caller's token never ends a call first.
A_CALL_MAY_LAST_S = 15 * 60.0


class RunListQuery(BaseModel):
    """What a list of runs asks for: one agent's or every one, since when, how many."""

    agent: str | None = None
    since: float = Field(0.0, ge=0)
    limit: int = Field(A_SCREENFUL, ge=1, le=_deps.LONGEST_LIST)


class JudgeQuery(BaseModel):
    """Whether a call judged already is judged again, and paid for again."""

    again: bool = False


@dataclass(frozen=True)
class Suite:
    """One run as it goes: the socket it drives, what its calls run on, what it opened, judged."""

    body: RunSuiteRequest
    registration: Registration
    setup: TextSetup
    configured: Providers
    keys: Keyring
    run: EvalRunResponse
    calls: list[OpenedCall] = field(default_factory=list[OpenedCall])
    cells: list[ScoreRow] = field(default_factory=list[ScoreRow])

    @property
    def now(self) -> EvalRunResponse:
        """The run as it stands: the calls opened and the matrix of the cells judged."""
        opened = self.run.model_copy(update={"calls": list(self.calls)})
        return runs.judged(opened, self.cells) if self.cells else opened


# One per agent at a time: a run drives the app that answers the agent's real calls.
@router.post("/v1/evals/run", response_model_exclude_unset=True)
async def run_suite(
    body: RunSuiteRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> EvalRunResponse:
    """Every golden under every model through the app that holds the agent, judged and stored."""
    check_agent(key.bearer, body.agent)
    registration = gateway.sockets.serving(scope, body.agent, body.app)
    if registration is None:
        raise NotFound(NO_AGENT.format(slug=body.agent))
    if body.voice:
        _refuse_out_loud(body)
    if body.cases or body.dataset:
        body = await _with_cases(gateway, body, scope)
    _refuse_past_the_ceiling(body)
    suite = await _suite_of(gateway, body, registration)
    pool, where = gateway.connections.pool, registration.scope
    async with gateway.evals.alone(suite.run.id, body.agent):
        await runs.put(pool, where, suite.run)
        why = await _judged_suite(gateway, suite, key.org)
        done = runs.finished(suite.now) if why is None else runs.stopped(suite.now, why)
        await runs.put(pool, where, done)
    return done


@router.get("/v1/evals/runs", response_model_exclude_unset=True)
async def list_runs(
    key: EvalsKey, scope: ScopeDep, gateway: GatewayDep, query: Annotated[RunListQuery, Query()]
) -> EvalRunList:
    """The runs of the key's org in its world, newest first."""
    found = await runs.listed(
        gateway.connections.pool,
        Scope(key.org, scope.env),
        agent=query.agent,
        since=query.since,
        limit=query.limit,
    )
    return EvalRunList(runs=found)


@router.get("/v1/evals/runs/{id}", response_model_exclude_unset=True)
async def get_run(
    run: Annotated[str, Path(alias="id")], key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> EvalRunResponse:
    """One run; another org's, or one nobody ran, is the same 404."""
    found = await runs.of(gateway.connections.pool, Scope(key.org, scope.env), run)
    if found is None:
        raise NotFound(NO_SUCH_RUN.format(id=run))
    return found


# Another org's call and nobody's are one 404: neither is told apart from a typo.
@router.post("/v1/evals/replay/{call}")
async def replay_call(
    call: str,
    key: EvalsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    body: ReplayCallRequest | None = None,
) -> ReplayCallResponse:
    """The six code checks over a finished call, the barge-ins it answered among them."""
    declared = await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), call)
    entries = await gateway.logs.store.whole(call)
    if not entries:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    wanted = body or ReplayCallRequest()
    irreversible = (
        None
        if declared is None
        else frozenset(tool.name for tool in declared.tools if tool.side_effect == "irreversible")
    )
    verdicts = checks.replay(
        entries, banned=wanted.banned, budget=wanted.budget, irreversible=irreversible
    )
    return ReplayCallResponse(
        call=call,
        agent=entries[0].agent,
        passed=not any(verdict.status == "broken" for verdict in verdicts),
        verdicts=verdicts,
    )


# Hang-up judging run later: the org judged nothing then, the judge failed, or a judge again.
@router.post("/v1/evals/judge/{call}", response_model_exclude_unset=True)
async def judge_call(
    call: str,
    key: EvalsKey,
    scope: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[JudgeQuery, Query()],
) -> CallScore:
    """Judge a finished call, write the verdict on its log, and answer it."""
    declared = await _deps.check_readable(gateway, _deps.Reader(acting=key, scope=scope), call)
    store = gateway.logs.store
    entries = await store.whole(call)
    if not entries:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if not any(entry.type == "call.ended" for entry in entries):
        raise Conflict(STILL_GOING.format(call=call))
    if not query.again and any(
        entry.type == "call.score" and entry.data.get("passed") is not None for entry in entries
    ):
        raise Conflict(ALREADY_JUDGED.format(call=call))
    own = await judges.for_call(gateway.connections.pool, key.org, entries[0].agent)
    org_facts = await compliance_of(gateway.connections.pool, key.org, call, declared)
    score = await judged_call(gateway.connections, entries, declared, own, org_facts)
    await store.rescored(call, entries[0].agent, score.written())
    await drifted(gateway.connections.pool, call, entries, score)
    return score


# The gateway holds the vendors' keys: the org's own when it brought one, else the box's lent.
@router.post("/v1/evals/caller")
async def next_line(
    body: NextLineRequest, _key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> NextLineResponse:
    """The persona's next line on the call so far, improvised by its model."""
    await check_paced(gateway, f"{scope.org} evals/caller", LINES_A_MINUTE, TOO_MANY_LINES)
    async with _caller_model(gateway, scope, body.persona) as model:
        return (await improvise_line(model, body)).answer


# A persona is the agent's: a name nobody wrote for this agent is refused; one sent without a
# name is played as sent.
@router.post("/v1/evals/voice")
async def place_voice_call(
    body: PlaceVoiceCallRequest, key: EvalsKey, scope: ScopeDep, gateway: GatewayDep
) -> PlaceVoiceCallResponse:
    """Dispatch the agent into a room, play the persona as a spoken caller, and hang up."""
    check_agent(key.bearer, body.agent)
    pool = gateway.connections.pool
    persona = body.persona
    if persona.name and await personas.persona(pool, key.org, body.agent, persona.name) is None:
        raise NotFound(personas.NOBODY.format(name=persona.name, agent=body.agent))
    await _a_new_call(gateway, body.call, body.agent, scope)
    line = spoken.Line(interferer_db=body.interferer_db, packet_loss=body.packet_loss)
    dispatch = rooms.Dispatch(
        agent=body.agent,
        org=scope.org,
        env=scope.env,
        holder=scope.holder or None,
        caller=spoken.A_SIMULATED_CALLER,
        persona=persona.name or None,
        accepts_when=persona.accepts_when or None,
        declines_when=persona.declines_when or None,
    )
    spending = Spending(await catalog.providers(pool))
    stopped = False
    async with _caller_model(gateway, scope, persona) as model:
        speech = tts_of(await _caller_voice(gateway, scope, body.agent, persona))

        # Asked before each line: the line that crosses the ceiling is the last one said.
        async def improvised(turns_left: int) -> tuple[str, bool]:
            nonlocal stopped
            entries = await gateway.logs.store.whole(body.call)
            if spoken.is_call_over(entries):
                return "", True
            if spending.is_over:
                stopped = True
                return "", True
            request = NextLineRequest(
                persona=persona, heard=heard_in(entries), turns_left=turns_left
            )
            next_one = await improvise_line(model, request)
            spending.count(next_one, speech)
            return next_one.answer.say, next_one.answer.hangup

        placed = spoken.SpokenLine("", "", body.call, body.turns, line)
        try:
            turns = await _on_the_line(gateway, dispatch, speech, placed, improvised)
        finally:
            await speech.aclose()
    return PlaceVoiceCallResponse(
        call=body.call,
        turns=turns,
        line=spoken.described(line),
        caller_cost_usd=spending.usd,
        stopped_at_ceiling=stopped,
    )


# ── a suite ──


# The run's calls run on what a real caller's would: the config tuned where the app holds it.
async def _suite_of(gateway: Gateway, body: RunSuiteRequest, registration: Registration) -> Suite:
    pool, where = gateway.connections.pool, registration.scope
    configured = await catalog.providers(pool)
    if body.version is not None:
        candidate = await scopes.tuning_at(pool, where, body.agent, body.version)
        if candidate is None or candidate.holder != where.holder:
            raise NotFound(NO_SUCH_VERSION.format(version=body.version, slug=body.agent))
    picked = Picked(version=body.version)
    config, versions = await tuned(pool, registration.config, where, configured, picked)
    keys = await keys_of(pool, gateway.connections.vault, where)
    setup = TextSetup(config, versions, thinking(config, configured, keys))
    return Suite(body, registration, setup, configured, keys, runs.new_run(body.agent))


# Real callers' words are played in written calls in the sandbox: never through the live app,
# whose tools act for real, and never out loud.
async def _with_cases(gateway: Gateway, body: RunSuiteRequest, scope: Scope) -> RunSuiteRequest:
    if scope.env == PRODUCTION:
        raise NotAllowed(CASES_IN_THE_SANDBOX)
    if body.voice:
        raise DeclarationRefused(CASES_WRITTEN)
    played = await dataset.picked(
        gateway.connections.pool, scope.org, body.agent, body.cases, every=body.dataset
    )
    return body.model_copy(update={"goldens": [*body.goldens, *played]})


async def _judged_suite(gateway: Gateway, suite: Suite, org: str) -> str | None:
    judge = (await judge_of(gateway.connections, suite.configured)).running
    model = None if judge is None else llm_of(judge)
    try:
        async with asyncio.timeout(runs.A_RUN_MAY_TAKE_S):
            return await _every_golden(gateway, suite, model)
    except QuotaExhausted as refused:
        await exhausted(gateway.logs, org, suite.body.agent, refused)
        return str(refused)
    except TimeoutError:
        return runs.TOOK_TOO_LONG.format(minutes=runs.A_RUN_MAY_TAKE_S // 60)
    finally:
        if model is not None:
            await model.aclose()


# The row is written before each call (so it can be tailed) and after it is judged.
async def _every_golden(gateway: Gateway, suite: Suite, judge: llm.LLM[Never] | None) -> str | None:
    body, registration = suite.body, suite.registration
    total = len(body.goldens) * max(len(body.models), 1)
    for model in body.models or [None]:
        column = runs.column_of(model)
        setup = _column_setup(suite, model)
        for golden in body.goldens:
            if not _holds(gateway, registration):
                return THE_APP_LEFT.format(done=len(suite.cells), total=total, slug=body.agent)
            opened = OpenedCall(golden=golden.name, model=column, call=new_call_id())
            suite.calls.append(opened)
            await runs.put(gateway.connections.pool, registration.scope, suite.now)
            played = await (
                _said_out_loud(gateway, suite, golden, opened)
                if body.voice
                else _written(gateway, suite, golden, opened, setup)
            )
            if played is None:
                return THE_APP_LEFT.format(done=len(suite.cells), total=total, slug=body.agent)
            entries = await gateway.logs.store.whole(opened.call)
            case = case_of(entries, setup.config)
            scores = await runs.score(goldens.golden_judges(golden, case), case, judge)
            requests = None if body.voice else played.requests
            suite.cells.append(runs.cell_of(opened, case, scores, requests))
            await runs.put(gateway.connections.pool, registration.scope, suite.now)
    return None


# A column runs the agent's own model, or the one named, on the same tuned config and keys.
def _column_setup(suite: Suite, model: ModelConfig | None) -> TextSetup:
    config = suite.setup.config
    if model is not None:
        declared = Model(provider=model.provider, model=model.model, temperature=model.temperature)
        config = dataclasses.replace(config, llm=declared)
    return dataclasses.replace(
        suite.setup, config=config, model=thinking(config, suite.configured, suite.keys)
    )


async def _written(
    gateway: Gateway, suite: Suite, golden: Golden, opened: OpenedCall, setup: TextSetup
) -> goldens.Played | None:
    registration = suite.registration
    scope = registration.scope
    context = CallContext(
        call=opened.call,
        channel=THE_WIDGET,
        direction="inbound",
        caller=f"web_{new_call_id()[5:17]}",
        route=Route(org=scope.org, agent=registration.slug, channel=THE_WIDGET, env=scope.env),
        today=golden.today or today_in(gateway.connections.settings.timezone),
        run=suite.run.id,
        holder=scope.holder or None,
    )
    session = await open_text_as(
        gateway.serving,
        registration,
        context,
        dataclasses.replace(setup, recalled=tuple(golden.memory)),
    )
    heard = await gateway.logs.writing(opened.call, registration.slug).followed()
    try:
        played = await goldens.drive(
            session, golden, heard, is_held=lambda: _holds(gateway, registration)
        )
    finally:
        heard.close()
    return played if played.held else None


async def _said_out_loud(
    gateway: Gateway, suite: Suite, golden: Golden, opened: OpenedCall
) -> goldens.Played | None:
    registration, body = suite.registration, suite.body
    scope = registration.scope
    lines = [line for line in golden.input if line]
    dispatch = rooms.Dispatch(
        agent=registration.slug,
        org=scope.org,
        env=scope.env,
        holder=scope.holder or None,
        caller=spoken.A_SIMULATED_CALLER,
        app=registration.owner,
        run=suite.run.id,
    )

    async def scripted(turns_left: int) -> tuple[str, bool]:
        return (lines[len(lines) - turns_left], False) if turns_left <= len(lines) else ("", False)

    speech = tts_of(await _caller_voice(gateway, scope, registration.slug, None))
    line = spoken.Line(interferer_db=body.interferer_db, packet_loss=body.packet_loss)
    placed = spoken.SpokenLine("", "", opened.call, len(lines), line)
    try:
        await _on_the_line(gateway, dispatch, speech, placed, scripted)
    finally:
        await speech.aclose()
    await _until_sealed(gateway, opened.call)
    return goldens.Played(held=True, requests=())


# The head is claimed in the caller's scope before the room is offered, so the worker opens it
# there or not at all (api/calls.py _unclaimed_or_in), and another org's call can never be named.
async def _a_new_call(gateway: Gateway, call: str, agent: str, scope: Scope) -> None:
    if await queries.scope_of_call(gateway.connections.pool, call) is not None:
        raise Conflict(NOT_A_NEW_CALL.format(call=call))
    await gateway.logs.store.claim(call, agent, scope.org, Claim(scope))


# ── the caller ──


@asynccontextmanager
async def _caller_model(
    gateway: Gateway, scope: Scope, persona: CallerPersona
) -> AsyncGenerator[llm.LLM[Never]]:
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
async def _caller_voice(
    gateway: Gateway, scope: Scope, agent: str, persona: CallerPersona | None
) -> Running:
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
async def _on_the_line(
    gateway: Gateway,
    dispatch: rooms.Dispatch,
    speech: tts.TTS[Never],
    line: spoken.SpokenLine,
    next_line: spoken.NextLine,
) -> int:
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


async def _until_sealed(gateway: Gateway, call: str) -> None:
    log = gateway.logs.reading(call)
    subscription = await log.followed()
    try:
        async with asyncio.timeout(A_SEAL_MAY_TAKE_S):
            while not await gateway.logs.store.sealed(call):
                await anext(subscription, None)
    except TimeoutError as never:
        raise NotAvailable(NEVER_SEALED.format(call=call, seconds=A_SEAL_MAY_TAKE_S)) from never
    finally:
        subscription.close()


def _refuse_past_the_ceiling(body: RunSuiteRequest) -> None:
    models = max(len(body.models), 1)
    total = len(body.goldens) * models
    if total > CALLS_A_RUN:
        raise DeclarationRefused(
            TOO_MANY_CALLS.format(played=len(body.goldens), models=models, total=total)
        )


def _refuse_out_loud(body: RunSuiteRequest) -> None:
    if body.models:
        raise DeclarationRefused(ONE_MODEL_OUT_LOUD)
    for golden in body.goldens:
        if golden.today is not None:
            raise DeclarationRefused(
                NO_PINNED_DAY_OUT_LOUD.format(name=golden.name, day=golden.today)
            )


def _holds(gateway: Gateway, registration: Registration) -> bool:
    holding = gateway.sockets.on(registration.owner, registration.scope.env, registration.slug)
    return holding is not None
