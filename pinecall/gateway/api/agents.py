"""An agent's doors: the app socket, what a worker asks for, the line, the dev relay, the fleet."""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Annotated, get_args
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from pinecall.channels import routes
from pinecall.domain.errors import (
    AppRefused,
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotFound,
    PinecallError,
    QuotaExhausted,
)
from pinecall.domain.types import (
    HOLDING,
    SANDBOX,
    THE_FLEET,
    THE_TEAM,
    THE_WIDGET,
    AgentConfig,
    Channel,
    Corner,
    Json,
    JsonObject,
    Route,
    parse_e164,
)
from pinecall.gateway import deps
from pinecall.gateway.deps import (
    Acting,
    AppKey,
    CallsKey,
    CornerDep,
    DispatchedDep,
    FleetKey,
    UsageKey,
    Wired,
    WiredDep,
    WorkerKey,
)
from pinecall.gateway.live import (
    NO_AGENT,
    NO_UNCLAIMED,
    NOT_REGISTERED,
    NOT_THAT_APP,
    Process,
    Registration,
    claim_code,
    exhausted,
    handed_on,
    keys_of,
    new_socket_id,
    parked_calls_of,
    tuned,
)
from pinecall.providers import catalog
from pinecall.providers.keys import Pipeline, pipeline
from pinecall.session.call import changed_by, declared
from pinecall.tenancy import admission, agents, keys, orgs, people
from pinecall.wire.commands import (
    COMMANDS,
    AgentConfigure,
    AgentDrain,
    AgentRegister,
    CallClaim,
    CallDial,
    DevAnswer,
    Ping,
    command_of,
)
from pinecall.wire.events import CallbackRequested, DevRequest, ErrorEvent, Pong
from pinecall.wire.frames import Command, Entry, WireModel
from pinecall.wire.parts import DevVerb, ToolResult
from pinecall.wire.rest import (
    AgentList,
    AppList,
    AppProcess,
    AppStopped,
    CallbacksPage,
    CallbackTaken,
    CallbackWanted,
    Calling,
    FleetTotals,
    Handed,
    Heartbeat,
    HeldAgent,
    HoldAudio,
    Judging,
    JudgingWanted,
    LineHolder,
    NumbersToCall,
    NumberToCall,
    Standing,
    TheLine,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Sent on a stop: the SDK exits instead of reconnecting.
STOPPED = "stopped"
REFUSED = "refused"
DIAL = (
    "call.dial is not answered on the app socket: POST /v1/agents/{slug}/dial places a call, with "
    "the talk scope and the org's outbound guards"
)
NO_SESSION = "{kind} names call {call!r}, which is not running here"
NOBODY_WAITING = (
    "no tool call {call_id} is waiting on call {call!r}: it lapsed, it was answered, or the call "
    "is not here"
)
NOBODY_ASKED = (
    "no dev.request {id} is waiting: it lapsed, it was answered, or another gateway asked it"
)
NO_CODE = (
    "no code {code} is waiting for agent {agent}: nobody issued it, it expired, or a call took it"
)
SEARCHES_WITH_NOTHING = (
    "{slug} searches its bases, and none is attached to it in {world}: "
    "pinecall docs attach <base> --agent {slug}"
)
NO_SUCH_APP = "no app {app} is connected here that this key may stop"
NOT_IN_PRODUCTION = "production has one corner and the org holds it: there is nothing to route"
NOBODY_TO_ROUTE_TO = "a phone reaches a person's own corner, and this key names no person"
NO_SUCH_VERB = "no dev verb {verb} in {family}: the verbs are {verbs}"
NO_ANSWER = "the app holding agent {slug} did not answer {verb} within {seconds:.0f}s"
APP_LEFT = "the app holding agent {slug} disconnected before it answered {verb}"
NOT_THIS_ORGS = "agent {agent} is not this org's"
# Longer than any verb takes: a suite waits 20 s on the app's side.
ANSWERED_WITHIN_S = 120.0
EVERY_VERB: frozenset[str] = frozenset(get_args(DevVerb.__value__))
FAMILIES: dict[str, frozenset[str]] = {
    "chat": frozenset({"chat.roster", "chat.start", "chat.say", "chat.end"}),
    "knowledge": frozenset({"knowledge.roster", "knowledge.push", "knowledge.eval"}),
    "memory": frozenset({"memory.roster", "memory.eval", "memory.extraction"}),
    "view": frozenset({"view.render"}),
}
FAMILIES["evals"] = EVERY_VERB - frozenset().union(*FAMILIES.values())
CALLBACK = "callback.requested"
# Every agent answers on the widget; no route row is needed for it.
ON_THE_WEB: frozenset[Channel] = frozenset({THE_WIDGET})
A_PAGE = 500


# ── the app socket ──


@router.websocket("/v1/apps")
async def apps_socket(websocket: WebSocket) -> None:
    """An app holds its agents here, answers their tools, and sends their calls' commands."""
    box = deps.wired(websocket)
    try:
        key = await _socket_key(websocket, box)
    except PinecallError as refused:
        await websocket.accept()
        await websocket.close(code=deps.POLICY_VIOLATION, reason=deps.close_reason(str(refused)))
        return
    await websocket.accept()
    socket = AppSocket(websocket, box, key)
    client = websocket.client
    box.live.connect(
        Process(
            app=socket.id,
            corner=socket.corner,
            address=None if client is None else client.host,
            connected_at=time.time(),
            stop=socket.stopped,
        ),
        socket.send,
    )
    try:
        await socket.serve()
    except WebSocketDisconnect:
        logger.info("app %s went away", socket.id)
    finally:
        # Calls outlive their socket: parked, then handed to another holder or the next app.
        box.live.disconnect(socket.id)
        calls = box.live.bound_to(socket.id)
        for call in calls:
            box.live.attach(call, None)
        await box.registry.release(socket.id)
        await handed_on(box.live, box.logs.store, box.registry, calls)


async def _socket_key(websocket: WebSocket, box: Wired) -> Acting:
    said = deps.bearer_of(websocket.headers)
    verified = None if said is None else await keys.verify(box.pool, said)
    if verified is None:
        raise NotAllowed(deps.TAKES_A_KEY)
    keys.check_opens(verified, HOLDING)
    return Acting(bearer=verified, env=keys.world_of(verified, websocket.headers.get(deps.WORLD)))


class AppSocket:
    """One connected app: its key, its corner, and every frame it sends answered."""

    def __init__(self, websocket: WebSocket, box: Wired, key: Acting) -> None:
        """A socket that holds nothing yet."""
        self.websocket = websocket
        self.box = box
        self.key = key
        self.corner = keys.corner_of(key.bearer, key.env)
        self.id = new_socket_id()

    async def serve(self) -> None:
        """Read frames until the app goes; every frame is answered and none raises."""
        while True:
            raw: Json = await self.websocket.receive_json()
            await self.take(raw)

    async def send(self, entry: Entry) -> None:
        """One entry, as the log holds it."""
        await self.websocket.send_json(entry.written())

    async def stopped(self, why: str) -> None:
        """Tell the app it was stopped, and close: it exits instead of reconnecting."""
        said = ErrorEvent(code=STOPPED, message=why, recoverable=False)
        await self.send(_unnumbered("", said))
        await self.websocket.close(reason=deps.close_reason(why))

    async def take(self, raw: Json) -> None:
        """One frame: its command applied, or refused in an error that names it."""
        try:
            command = Command.model_validate(raw)
        except ValidationError as refused:
            await self.refuse(_named(raw, "agent"), "bad_shape", f"command: {refused}", raw)
            return
        if command.type not in COMMANDS:
            said = f"the protocol has no command called {command.type!r}"
            await self.refuse(command.agent, "unknown_command", said, raw)
            return
        try:
            await self._applied(command)
        except QuotaExhausted as refused:
            await self.refuse(command.agent, REFUSED, str(refused), raw)
        except PinecallError as refused:
            code = "bad_shape" if isinstance(refused, DeclarationRefused) else REFUSED
            await self.refuse(command.agent, code, str(refused), raw)

    # One arm per command, as the reducer folds events; what is about a call goes to the call.
    async def _applied(self, command: Command) -> None:
        model = command_of(command)
        match model:
            case AgentRegister():
                await self._register(command.agent, model)
            case AgentConfigure():
                await self._configure(command.agent, model)
            case AgentDrain():
                await self._drain(command.agent)
            case Ping():
                await self._emitted(command.agent, "pong", Pong(ts=time.time()))
            case DevAnswer():
                if not self.box.live.dev_answered(model):
                    said = NOBODY_ASKED.format(id=model.id)
                    await self.refuse(command.agent, "no_session", said, command.written())
            case ToolResult():
                await self._answered(command, model)
            case CallClaim():
                await self._claimed(command, model)
            case CallDial():
                await self.refuse(command.agent, "no_handler", DIAL, command.written())
            case _:
                await self._on_the_call(command, model)

    async def _register(self, slug: str, wanted: AgentRegister) -> None:
        self.box.live.named(self.id, wanted.host)
        corner = self.corner
        others = self.box.registry.slugs(corner.org) - {slug}
        try:
            await admission.admit_agent(self.box.pool, corner.org, corner.env, holding=len(others))
        except QuotaExhausted as refused:
            await exhausted(self.box.logs, corner.org, slug, refused)
            raise
        entry = await self.box.registry.register(
            self.id, corner, slug, sdk=wanted.sdk, takes_unclaimed=wanted.takes_unclaimed
        )
        await self.send(entry)
        # A console takes no call it did not open; an app takes the calls a previous one left.
        if wanted.takes_unclaimed:
            await parked_calls_of(self.box.live, self.box.logs.store, corner, slug, self.id)

    # A class that searches with no base attached is refused when declared, not mid-call.
    async def _configure(self, slug: str, wanted: AgentConfigure) -> None:
        held = self._holds(slug)
        config = declared(held.config, wanted.config)
        configured = await catalog.providers(self.box.pool)
        running, _ = await tuned(self.box.pool, config, self.corner, configured)
        if config.uses_knowledge and not running.bases:
            raise DeclarationRefused(SEARCHES_WITH_NOTHING.format(slug=slug, world=self.corner.env))
        entry = await self.box.registry.configure(
            self.id, self.corner.env, slug, config, changed_by(wanted.config)
        )
        await self.send(entry)

    # A deploy: the calls move now, and the socket keeps the agent so its tools still answer.
    async def _drain(self, slug: str) -> None:
        registry, live = self.box.registry, self.box.live
        registry.drain(self.id, self.corner.env, slug)
        mine = [call for call in live.bound_to(self.id) if live.calls[call].agent == slug]
        handed, parked = await handed_on(live, self.box.logs.store, registry, mine)
        entry = await registry.drained(self.id, self.corner.env, slug, handed=handed, parked=parked)
        await self.send(entry)

    async def _answered(self, command: Command, result: ToolResult) -> None:
        served = None if command.call is None else self.box.live.calls.get(command.call)
        if served is not None and served.agent == command.agent and served.tools.answered(result):
            return
        said = NOBODY_WAITING.format(call_id=result.call_id, call=command.call)
        await self.refuse(command.agent, "no_session", said, command.written())

    # Bound by the gateway whichever process runs the call.
    async def _claimed(self, command: Command, wanted: CallClaim) -> None:
        self._holds(command.agent)
        served = None if command.call is None else self.box.live.calls.get(command.call)
        if served is None or served.agent != command.agent:
            said = NO_SESSION.format(kind=command.type, call=command.call)
            await self.refuse(command.agent, "no_session", said, command.written())
            return
        if not await claim_code(self.box.codes, served, wanted.code, via="agent"):
            said = NO_CODE.format(code=wanted.code, agent=command.agent)
            await self.refuse(command.agent, "no_code", said, command.written())

    # A written call runs here and takes the command at once; a voice call's worker reads it
    # off its command stream and applies it with the same function.
    async def _on_the_call(self, command: Command, model: WireModel) -> None:
        self._holds(command.agent)
        served = None if command.call is None else self.box.live.calls.get(command.call)
        if served is not None and served.agent == command.agent and served.session is not None:
            await served.session.apply(model)
            return
        if self.box.live.commanded(command.call, command.agent, command):
            return
        said = NO_SESSION.format(kind=command.type, call=command.call)
        await self.refuse(command.agent, "no_session", said, command.written())

    def _holds(self, slug: str) -> Registration:
        held = self.box.registry.on(self.id, self.corner.env, slug)
        if held is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        return held

    async def _emitted(self, slug: str, kind: str, event: WireModel) -> None:
        entry = await self.box.logs.agent(slug).append(kind, event.written())
        await self.send(entry)

    async def refuse(self, slug: str, code: str, message: str, raw: Json) -> None:
        """An error naming the command refused and its id; on the agent's log when it names one."""
        said = ErrorEvent(
            code=code,
            message=message,
            command=_named(raw, "type") or None,
            id=_named(raw, "id") or None,
            recoverable=True,
        )
        if slug:
            await self._emitted(slug, "error", said)
            return
        await self.send(_unnumbered("", said))


# ── what a worker asks about an agent ──


@router.get("/v1/agents/{slug}/config")
async def config(
    slug: str, _key: deps.DeclarationKey, where: CornerDep, box: WiredDep
) -> AgentConfig:
    """The agent as the corner runs it: its declaration under the corner's settings."""
    held = _held(box, where, slug)
    tuned_config, _ = await tuned(box.pool, held.config, where, await catalog.providers(box.pool))
    return tuned_config


# The one answer that carries keys: to the fleet's key, or the org's own worker's.
@router.get("/v1/agents/{slug}/provider-keys")
async def provider_keys(slug: str, _key: WorkerKey, where: CornerDep, box: WiredDep) -> Pipeline:
    """The three stages a call of the agent runs, each on the key it runs on."""
    held = _held(box, where, slug)
    configured = await catalog.providers(box.pool)
    tuned_config, _ = await tuned(box.pool, held.config, where, configured)
    return pipeline(tuned_config, configured, await keys_of(box.pool, box.vault, where))


@router.get("/v1/agents/{slug}/hold-audio")
async def hold_audio(slug: str, _key: WorkerKey, where: CornerDep, box: WiredDep) -> HoldAudio:
    """What a caller of the agent hears while a tool runs."""
    chosen = await agents.hold_of(box.pool, where, slug)
    if chosen is None:
        return HoldAudio(played="default")
    return HoldAudio(
        played=chosen.played, sha256=chosen.sha256, seconds=chosen.seconds, name=chosen.name
    )


@router.get("/v1/agents/{slug}/hold-audio/audio")
async def hold_clip(slug: str, _key: WorkerKey, where: CornerDep, box: WiredDep) -> Response:
    """The org's own clip, Ogg Opus."""
    audio = await agents.hold_audio(box.pool, where, slug)
    if audio is None:
        raise NotFound(f"agent {slug} plays no clip of its own")
    return Response(audio, media_type="audio/ogg")


class RoutesAsked(BaseModel):
    """A worker asking for the route a number rings, across every org."""

    number: str | None = None
    channel: Channel = "phone"


# A route the operator adds answers the next job: nothing is cached.
@router.get("/v1/routes")
async def routes_of(
    key: WorkerKey, named: DispatchedDep, box: WiredDep, asked: Annotated[RoutesAsked, Query()]
) -> list[Route]:
    """The routes of a corner; for the fleet's key and a number, the route that number rings."""
    fleet = THE_FLEET in key.bearer.key.scopes
    if fleet and asked.number is not None:
        found = await routes.at(box.pool, asked.channel, asked.number)
        return [] if found is None or found.env != key.bearer.key.env else [found]
    where = named if fleet and named is not None else keys.corner_of(key.bearer, key.env)
    return await routes.of_org(box.pool, where.org, where.env)


# ── what the org holds ──


@router.get("/v1/agents")
async def held_agents(key: CallsKey, where: CornerDep, box: WiredDep) -> AgentList:
    """The org's held agents in the world: one row per slug, one per corner for a team reader."""
    every = _sees_every_corner(key)
    doors: dict[str, set[Channel]] = {}
    for route in await routes.of_org(box.pool, where.org, where.env):
        doors.setdefault(route.agent, set()).add(route.channel)
    listed: list[HeldAgent] = []
    for held in box.registry.holding(where, every_corner=every):
        channels = sorted(doors.get(held.slug, set[Channel]()) | ON_THE_WEB)
        holder = await _named_holder(box, held.corner)
        listed.append(HeldAgent(slug=held.slug, channels=channels, holder=holder))
    return AgentList(agents=listed)


@router.get("/v1/apps")
async def connected(key: CallsKey, where: CornerDep, box: WiredDep) -> AppList:
    """The app sockets of the org in the world this key may see, oldest first."""
    every = _sees_every_corner(key)
    listed: list[AppProcess] = []
    for process in box.live.processes_of(where.org, where.env):
        if not every and process.corner.holder not in ("", where.holder):
            continue
        held = box.registry.owned_by(process.app)
        listed.append(
            AppProcess(
                app=process.app,
                agents=[one.slug for one in held],
                env=process.corner.env,
                host=process.host,
                address=process.address,
                sdk=next((one.sdk for one in held if one.sdk is not None), None),
                holder=await _named_holder(box, process.corner),
                connected_at=process.connected_at,
            )
        )
    return AppList(apps=listed)


# One 404 for every reason (another org, world or corner, gone): nothing leaks.
@router.post("/v1/apps/{app}/stop")
async def stop(app: str, key: AppKey, where: CornerDep, box: WiredDep) -> AppStopped:
    """Tell the app it was stopped, and close its socket."""
    found = box.live.processes.get(app)
    every = _sees_every_corner(key)
    if found is None or found.corner.org != where.org or found.corner.env != where.env:
        raise NotFound(NO_SUCH_APP.format(app=app))
    if not every and found.corner.holder not in ("", where.holder):
        raise NotFound(NO_SUCH_APP.format(app=app))
    who = key.bearer.key.name or key.bearer.key.label or "a server of the org"
    await found.stop(f"stopped by {who}")
    return AppStopped(app=app, stopped=True)


# ── the line: whose terminal a ring lands in ──


@router.get("/v1/agents/{slug}/line")
async def the_line(slug: str, _key: CallsKey, where: CornerDep, box: WiredDep) -> TheLine:
    """Who holds the agent's line, and who else could take it."""
    return await _line(box, where, slug)


@router.post("/v1/agents/{slug}/line")
async def take_the_line(slug: str, _key: AppKey, where: CornerDep, box: WiredDep) -> TheLine:
    """Take the agent's line for this corner."""
    box.registry.take_the_line(where, slug)
    return await _line(box, where, slug)


@router.delete("/v1/agents/{slug}/line")
async def drop_the_line(slug: str, _key: AppKey, where: CornerDep, box: WiredDep) -> TheLine:
    """Let the line go, to the newest other corner that could take it."""
    box.registry.drop_the_line(where, slug)
    return await _line(box, where, slug)


@router.put("/v1/line/from")
async def calls_from(said: Calling, key: AppKey, box: WiredDep) -> dict[str, list[str]]:
    """Send rings from this phone to the key's person's own corner."""
    holder = _person_of(key)
    box.registry.calls_from(key.env, parse_e164(said.number), holder)
    return {"calling": box.registry.calling(key.env, holder)}


@router.delete("/v1/line/from")
async def forget_calls_from(key: AppKey, box: WiredDep) -> dict[str, list[str]]:
    """Stop sending this person's phones to their corner, and say which were forgotten."""
    return {"forgot": box.registry.forget_calls_from(key.env, _person_of(key))}


# A developer lacks the numbers scope in production: this shows production's numbers and agents.
@router.get("/v1/line/numbers")
async def numbers_to_call(key: AppKey, box: WiredDep) -> NumbersToCall:
    """The production numbers a developer's phone can dial, and the phones that are theirs."""
    holder = _person_of(key)
    doors = await routes.of_org(box.pool, key.org, "production")
    return NumbersToCall(
        calling=box.registry.calling(key.env, holder),
        numbers=[
            NumberToCall(number=one.number, agent=one.agent)
            for one in doors
            if one.channel == "phone" and one.number is not None
        ],
    )


# A developer's own phone dialling the production number reaches their sandbox copy, while they
# hold it; every other caller reaches production.
@router.get("/v1/agents/{slug}/rings-for")
async def rings_for(
    slug: str,
    _key: FleetKey,
    box: WiredDep,
    org: Annotated[str, Query()],
    caller: Annotated[str, Query()],
) -> Handed:
    """Where a production ring from this phone goes: a developer's corner and fleet, or nowhere."""
    taking = box.registry.taking(Corner(org, SANDBOX), slug, caller)
    if taking is None or not taking.corner.holder:
        return Handed()
    if caller not in box.registry.calling(SANDBOX, taking.corner.holder):
        return Handed()
    fleet = orgs.fleet_of(await orgs.fleets(box.pool), SANDBOX)
    return Handed(holder=taking.corner.holder, fleet=fleet)


# ── the console's asks of an app ──


# FastAPI builds it: the body, and the socket the query names.
@dataclass(frozen=True)
class Ask:
    """What a console asks an app, and which of the agent's sockets it asks."""

    said: JsonObject
    app: Annotated[str | None, Query()] = None


AskDep = Annotated[Ask, Depends(Ask)]


@router.post("/v1/agents/{slug}/dev/chat/{verb}")
async def dev_chat(
    slug: str, verb: str, asked: AskDep, key: deps.TalkKey, box: WiredDep
) -> JsonObject:
    """A chat verb, answered by the app holding the agent."""
    return await _relayed(box, key, Relay("chat", slug, verb), asked)


@router.post("/v1/agents/{slug}/dev/knowledge/{verb}")
async def dev_knowledge(
    slug: str, verb: str, asked: AskDep, key: deps.KnowledgeKey, box: WiredDep
) -> JsonObject:
    """A knowledge verb, answered by the app holding the agent."""
    return await _relayed(box, key, Relay("knowledge", slug, verb), asked)


@router.post("/v1/agents/{slug}/dev/memory/{verb}")
async def dev_memory(
    slug: str, verb: str, asked: AskDep, key: deps.MemoryKey, box: WiredDep
) -> JsonObject:
    """A memory verb, answered by the app holding the agent."""
    return await _relayed(box, key, Relay("memory", slug, verb), asked)


@router.post("/v1/agents/{slug}/dev/view/{verb}")
async def dev_view(slug: str, verb: str, asked: AskDep, key: CallsKey, box: WiredDep) -> JsonObject:
    """The side panel beside a conversation, rendered by the app."""
    return await _relayed(box, key, Relay("view", slug, verb), asked)


@router.post("/v1/agents/{slug}/dev/evals/{verb}")
async def dev_evals(
    slug: str, verb: str, asked: AskDep, key: deps.EvalsKey, box: WiredDep
) -> JsonObject:
    """An evals verb, answered by the app holding the agent."""
    return await _relayed(box, key, Relay("evals", slug, verb), asked)


@dataclass(frozen=True)
class Relay:
    """Which family's verb, of which agent."""

    family: str
    slug: str
    verb: str


# dev.request goes down the socket unstored; the app's refusal comes back as its own status.
async def _relayed(box: Wired, key: Acting, relay: Relay, asked: Ask) -> JsonObject:
    if relay.verb not in FAMILIES[relay.family]:
        verbs = sorted(FAMILIES[relay.family])
        raise NotFound(NO_SUCH_VERB.format(verb=relay.verb, family=relay.family, verbs=verbs))
    where = keys.corner_of(key.bearer, key.env)
    held = box.registry.serving(where, relay.slug, asked.app)
    if held is None:
        if asked.app is not None:
            raise Conflict(NOT_THAT_APP.format(app=asked.app, slug=relay.slug))
        if box.registry.of(where, relay.slug) is not None:
            raise Conflict(NO_UNCLAIMED.format(slug=relay.slug))
        raise NotFound(NO_AGENT.format(slug=relay.slug))
    ask_id = f"dev_{uuid4().hex[:12]}"
    request = DevRequest.model_validate({"id": ask_id, "verb": relay.verb, "data": asked.said})
    waiting = box.live.ask(ask_id)
    if not await box.live.tell(held.owner, _unnumbered(relay.slug, request, kind="dev.request")):
        box.live.asked.pop(ask_id, None)
        raise AppRefused(502, APP_LEFT.format(slug=relay.slug, verb=relay.verb))
    try:
        answer = await asyncio.wait_for(waiting, ANSWERED_WITHIN_S)
    except TimeoutError:
        box.live.asked.pop(ask_id, None)
        said_late = NO_ANSWER.format(slug=relay.slug, verb=relay.verb, seconds=ANSWERED_WITHIN_S)
        raise AppRefused(504, said_late) from None
    if answer.refused is not None:
        raise AppRefused(answer.refused.status, answer.refused.detail)
    return answer.result or {}


# ── judging ──


# Per org, not per world: judging is billed to the org across both.
@router.get("/v1/org/judging")
async def judging_standing(key: CallsKey, box: WiredDep) -> Judging:
    """Whether hang-up judging is on, and its ceiling per call."""
    on = await orgs.judged(box.pool, key.org)
    return Judging(on=on, ceiling_eur=box.settings.judge_ceiling_eur)


@router.put("/v1/org/judging")
async def turn_judging(said: JudgingWanted, key: UsageKey, box: WiredDep) -> Judging:
    """Hang-up judging on or off, from the next call."""
    await orgs.set_judging(box.pool, key.org, on=said.on)
    return Judging(on=said.on, ceiling_eur=box.settings.judge_ceiling_eur)


# ── the fleet ──


@router.post("/v1/fleet/heartbeat")
async def heartbeat(said: Heartbeat, _key: FleetKey, box: WiredDep) -> Standing:
    """A worker's report; the answer says whether it is cordoned and its fleet full."""
    return box.roster.report(said, time.time())


class FleetAsked(BaseModel):
    """Which fleet the overflow asks about; unset, the fleet of the key's world."""

    fleet: str | None = None


@router.get("/v1/fleet/standing")
async def fleet_standing(
    key: FleetKey, box: WiredDep, asked: Annotated[FleetAsked, Query()]
) -> FleetTotals:
    """A fleet's workers summed; the overflow opens when it is full."""
    fleet = asked.fleet or orgs.fleet_of(await orgs.fleets(box.pool), key.env)
    return box.roster.totals(fleet, time.time())


# The overflow names any org's agent; an app, only its own.
@router.post("/v1/callbacks", status_code=204)
async def callback_wanted(said: CallbackWanted, key: WorkerKey, box: WiredDep) -> None:
    """Somebody the overflow told to wait for a call back, on the agent's log."""
    owner = await box.logs.store.owner(None, said.agent)
    if owner is None or (owner != key.org and THE_FLEET not in key.bearer.key.scopes):
        raise NotFound(NOT_THIS_ORGS.format(agent=said.agent))
    wanted = CallbackRequested(
        channel=said.channel, number=said.number, via="overflow", call=said.call, contact=None
    )
    await box.logs.agent(said.agent).append(CALLBACK, wanted.written())


class CallbacksAsked(BaseModel):
    """Where the org's list of callbacks continues, and whose."""

    after: int = 0
    agent: str | None = None


@router.get("/v1/callbacks", response_model_exclude_unset=True)
async def callbacks(
    key: CallsKey, box: WiredDep, asked: Annotated[CallbacksAsked, Query()]
) -> CallbacksPage:
    """The org's callbacks, oldest first, a page at a time."""
    page = await box.logs.store.across([CALLBACK], after=asked.after, limit=A_PAGE)
    ours = [
        one
        for one in page
        if one.org == key.org and (asked.agent is None or one.entry.agent == asked.agent)
    ]
    return CallbacksPage(
        requests=[
            CallbackTaken.model_validate(
                {
                    "position": one.position,
                    "agent": one.entry.agent,
                    "ts": one.entry.ts,
                    **one.entry.data,
                }
            )
            for one in ours
        ],
        next=page[-1].position if len(page) == A_PAGE else None,
    )


# ── the rules these doors share ──


# Another org's agent is the same 404 as nobody's: its existence does not leak.
def _held(box: Wired, where: Corner, slug: str) -> Registration:
    held = box.registry.of(where, slug)
    if held is None or held.corner.org != where.org:
        raise NotFound(NO_AGENT.format(slug=slug))
    return held


# A team reader with the holding scope sees every corner of the org; anyone else their own.
def _sees_every_corner(key: Acting) -> bool:
    return {THE_TEAM, HOLDING} <= key.bearer.key.scopes


def _person_of(key: Acting) -> str:
    if key.env != SANDBOX:
        raise Conflict(NOT_IN_PRODUCTION)
    if key.bearer.member is None:
        raise NotAllowed(NOBODY_TO_ROUTE_TO)
    return key.bearer.member.id


async def _named_holder(box: Wired, corner: Corner) -> LineHolder | None:
    if not corner.holder:
        return None
    member = await people.find(box.pool, corner.org, corner.holder)
    return LineHolder(holder=corner.holder, name=None if member is None else member.email)


async def _line(box: Wired, where: Corner, slug: str) -> TheLine:
    holder = box.registry.line_of(where.env, slug)
    waiting = [
        one for one in box.registry.waiting_for_the_line(where, slug) if one.corner.holder != holder
    ]
    holding = None
    if holder is not None:
        holding = await _named_holder(box, Corner(where.org, where.env, holder)) or LineHolder(
            holder=None, name=None
        )
    return TheLine(
        agent=slug,
        env=where.env,
        held=holder is not None,
        holding=holding,
        # In production every holder is the org's own: the line is never "yours" there.
        yours=holder is not None and holder == where.holder and where.env == SANDBOX,
        waiting=[
            await _named_holder(box, one.corner) or LineHolder(holder=None, name=None)
            for one in waiting
        ],
        calling=box.registry.calling(where.env, where.holder) if where.holder else [],
    )


def _unnumbered(slug: str, event: WireModel, *, kind: str = "error") -> Entry:
    return Entry(
        seq=0,
        ts=time.time(),
        call=None,
        agent=slug,
        type=kind,
        ephemeral=True,
        data=event.written(),
    )


def _named(raw: Json, field: str) -> str:
    if not isinstance(raw, dict):
        return ""
    named = raw.get(field)
    return named if isinstance(named, str) else ""
