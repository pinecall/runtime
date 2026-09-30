"""The app socket a tenant's process holds, and the org's list of connected apps."""

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from pinecall.domain.errors import (
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
    PinecallError,
    QuotaExhausted,
)
from pinecall.domain.names import Json
from pinecall.domain.person import HOLDING
from pinecall.gateway import _deps
from pinecall.gateway._call_setup import exhausted, tuned
from pinecall.gateway._deps import Acting, AppKey, CallsKey, GatewayDep, ScopeDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import Served, claim_code, declared_for_the_call
from pinecall.gateway._sockets import (
    NOT_REGISTERED,
    Process,
    Registration,
    SocketId,
    new_socket_id,
)
from pinecall.gateway.calls.binding import handed_on, parked_calls_of
from pinecall.gateway.calls.commands import commanded
from pinecall.gateway.calls.inbox import APP_CHANNEL, Bound, ForApp
from pinecall.gateway.calls.known import known_here
from pinecall.providers import catalog
from pinecall.session.call import changed_by, with_app_fields
from pinecall.session.tools import unanswered
from pinecall.tenancy import admission, consents, keys
from pinecall.tenancy.consents import Given
from pinecall.wire.commands import (
    COMMANDS,
    AgentConfigure,
    AgentDrain,
    AgentRegister,
    CallClaim,
    CallDial,
    CallOptOut,
    DevAnswer,
    Ping,
    PromptSet,
    command_of,
)
from pinecall.wire.events import ErrorEvent, Pong
from pinecall.wire.frames import Command, Entry, WireModel
from pinecall.wire.parts import ToolResult
from pinecall.wire.rest.agents import (
    AppList,
    AppRow,
    StopAppResponse,
)

router = APIRouter()


logger = logging.getLogger(__name__)


# Sent on a stop: the SDK exits instead of reconnecting.
STOPPED = "stopped"


REFUSED = "refused"


DIAL = (
    "call.dial is not answered on the app socket: POST /v1/agents/{slug}/dial places a call, with "
    "the talk scope and the org's outbound guards"
)


NO_SESSION = "{kind} names call {call!r}, which is not running here"


# Where an opt-out the agent heard came from, as the do-not-call list says it.
ASKED_THE_AGENT = "the caller asked the agent"

NO_NUMBER_TO_LIST = "call {call} has no phone number at its far end: nothing to put on the list"


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


NO_SUCH_APP = "no app {app} is connected to this box that this key may stop"


# How long a socket waits for the signal before listening again for the calls bound to it.
BOUND_RETRY_S = 1.0


class AppSocket:
    """One connected app: its key, its scope, and every frame it sends answered."""

    def __init__(self, websocket: WebSocket, gateway: Gateway, key: Acting) -> None:
        """A socket that holds nothing yet."""
        self.websocket = websocket
        self.gateway = gateway
        self.key = key
        self.scope = keys.scope_of(key.bearer, key.env)
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
        data = ErrorEvent(code=STOPPED, message=why, recoverable=False)
        await self.send(_deps.ephemeral_entry("", data))
        await self.websocket.close(reason=_deps.close_reason(why))

    async def take(self, raw: Json) -> None:
        """One frame: its command applied, or refused in an error that names it."""
        try:
            command = Command.model_validate(raw)
        except ValidationError as refused:
            await self.refuse(_named(raw, "agent"), "bad_shape", f"command: {refused}", raw)
            return
        if command.type not in COMMANDS:
            text = f"the protocol has no command called {command.type!r}"
            await self.refuse(command.agent, "unknown_command", text, raw)
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
                await self._dev_answered(command, model)
            case ToolResult():
                await self._answered(command, model)
            case CallClaim() | CallOptOut():
                await self._on_the_gateway(command, model)
            case CallDial():
                await self.refuse(command.agent, "no_handler", DIAL, command.written())
            case PromptSet():
                await self._prompted(command, model)
            case _:
                await self._on_the_call(command, model)

    async def _register(self, slug: str, wanted: AgentRegister) -> None:
        keys.check_agent(self.key.bearer, slug)
        self.gateway.live.named(self.id, wanted.host)
        scope = self.scope
        others = self.gateway.sockets.slugs(scope.org) - {slug}
        try:
            await admission.admit_agent(
                self.gateway.connections.pool, scope.org, scope.env, holding=len(others)
            )
        except QuotaExhausted as refused:
            await exhausted(self.gateway.logs, scope.org, slug, refused)
            raise
        entry = await self.gateway.sockets.register(
            self.id, scope, slug, sdk=wanted.sdk, takes_unclaimed=wanted.takes_unclaimed
        )
        await self.send(entry)
        # A console takes no call it did not open; an app takes the calls a previous one left.
        if wanted.takes_unclaimed:
            await parked_calls_of(self.gateway.live, scope, slug, self.id)

    # A class that searches with no base attached is refused when declared, not mid-call.
    async def _configure(self, slug: str, wanted: AgentConfigure) -> None:
        found = self._holds(slug)
        config = with_app_fields(found.config, wanted.config)
        configured = await catalog.providers(self.gateway.connections.pool)
        running, _ = await tuned(self.gateway.connections.pool, config, self.scope, configured)
        if config.uses_knowledge and not running.bases:
            raise DeclarationRefused(SEARCHES_WITH_NOTHING.format(slug=slug, world=self.scope.env))
        entry = await self.gateway.sockets.configure(
            self.id, self.scope.env, slug, config, changed_by(wanted.config)
        )
        await self.send(entry)
        # Declared now: what waited on WhatsApp for this agent is answered on this declaration.
        if found.takes_unclaimed:
            self.gateway.threads.answering(self._holds(slug))

    # A deploy: the calls move now, and the socket keeps the agent so its tools still answer.
    async def _drain(self, slug: str) -> None:
        sockets, live = self.gateway.sockets, self.gateway.live
        sockets.drain(self.id, self.scope.env, slug)
        mine = [call for call in live.bound_to(self.id) if live.calls[call].agent == slug]
        handed, parked = await handed_on(live, sockets, mine)
        entry = await sockets.drained(self.id, self.scope.env, slug, handed=handed, parked=parked)
        await self.send(entry)

    # The worker may have asked another gateway: the answer is written on the call's log here,
    # once per call id, and whoever waits reads it there.
    async def _answered(self, command: Command, result: ToolResult) -> None:
        served = None if command.call is None else self.gateway.live.calls.get(command.call)
        if served is not None and served.agent == command.agent and await _written(served, result):
            return
        text = NOBODY_WAITING.format(call_id=result.call_id, call=command.call)
        await self.refuse(command.agent, "no_session", text, command.written())

    # Bound by the gateway whichever process runs the call.
    # A code claimed and an opt-out are the gateway's to answer, whichever process runs the call.
    async def _on_the_gateway(self, command: Command, wanted: CallClaim | CallOptOut) -> None:
        self._holds(command.agent)
        served = None if command.call is None else self.gateway.live.calls.get(command.call)
        if served is None or served.agent != command.agent:
            text = NO_SESSION.format(kind=command.type, call=command.call)
            await self.refuse(command.agent, "no_session", text, command.written())
            return
        if isinstance(wanted, CallOptOut):
            await _opted_out(self.gateway, served, command.agent, wanted)
            return
        if not await claim_code(self.gateway.codes, served, wanted.code, via="agent"):
            text = NO_CODE.format(code=wanted.code, agent=command.agent)
            await self.refuse(command.agent, "no_code", text, command.written())

    async def _dev_answered(self, command: Command, answer: DevAnswer) -> None:
        if not await self.gateway.live.dev_answered(answer):
            text = NOBODY_ASKED.format(id=answer.id)
            await self.refuse(command.agent, "no_session", text, command.written())

    # The block's words are kept once under the hash the log will name, before the call hears it.
    async def _prompted(self, command: Command, wanted: PromptSet) -> None:
        self._holds(command.agent)
        pool = self.gateway.connections.pool
        await self.gateway.serving.prompts.keep(pool, self.scope.org, wanted.text)
        await self._on_the_call(command, wanted)

    # A written call runs here and takes the command at once; a voice call's worker reads it
    # off its command stream and applies it with the same function.
    async def _on_the_call(self, command: Command, model: WireModel) -> None:
        self._holds(command.agent)
        served = None if command.call is None else self.gateway.live.calls.get(command.call)
        if served is not None and served.agent == command.agent and served.session is not None:
            declared_for_the_call(served, model)
            await served.session.apply(model)
            return
        if command.call is not None and await commanded(self.gateway.connections.signal, command):
            return
        text = NO_SESSION.format(kind=command.type, call=command.call)
        await self.refuse(command.agent, "no_session", text, command.written())

    def _holds(self, slug: str) -> Registration:
        registration = self.gateway.sockets.on(self.id, self.scope.env, slug)
        if registration is None:
            raise DeclarationRefused(NOT_REGISTERED.format(slug=slug))
        return registration

    async def _emitted(self, slug: str, kind: str, event: WireModel) -> None:
        entry = await self.gateway.logs.agent(slug).append(kind, event.written())
        await self.send(entry)

    async def refuse(self, slug: str, code: str, message: str, raw: Json) -> None:
        """An error naming the command refused and its id; on the agent's log when it names one."""
        text = ErrorEvent(
            code=code,
            message=message,
            command=_named(raw, "type") or None,
            id=_named(raw, "id") or None,
            recoverable=True,
        )
        if slug:
            await self._emitted(slug, "error", text)
            return
        await self.send(_deps.ephemeral_entry("", text))


@router.websocket("/v1/apps")
async def apps_socket(websocket: WebSocket) -> None:
    """An app holds its agents here, answers their tools, and sends their calls' commands."""
    gateway = _deps.gateway_of(websocket)
    try:
        key = await _socket_key(websocket, gateway)
    except PinecallError as refused:
        await websocket.accept()
        await websocket.close(code=_deps.POLICY_VIOLATION, reason=_deps.close_reason(str(refused)))
        return
    await websocket.accept()
    socket = AppSocket(websocket, gateway, key)
    client = websocket.client
    gateway.live.connect(
        Process(
            app=socket.id,
            scope=socket.scope,
            address=None if client is None else client.host,
            connected_at=time.time(),
            stop=socket.stopped,
        ),
        socket.send,
    )
    bound = asyncio.create_task(_inbox(gateway, socket))
    try:
        await socket.serve()
    except WebSocketDisconnect:
        logger.info("app %s went away", socket.id)
    finally:
        bound.cancel()
        await asyncio.gather(bound, return_exceptions=True)
        # Calls outlive their socket: parked, then handed to another holder or the next app.
        gateway.live.disconnect(socket.id)
        calls = gateway.live.bound_to(socket.id)
        for call in calls:
            gateway.live.attach(call, None)
        await gateway.sockets.release(socket.id)
        await handed_on(gateway.live, gateway.sockets, calls)


@router.get("/v1/apps")
async def list_apps(key: CallsKey, where: ScopeDep, gateway: GatewayDep) -> AppList:
    """The app sockets of the org in the world this key may see, oldest first."""
    every = _deps.sees_every_scope(key)
    listed: list[AppRow] = []
    for process in gateway.live.processes_of(where.org, where.env):
        if not every and process.scope.holder not in ("", where.holder):
            continue
        registration = gateway.sockets.owned_by(process.app)
        listed.append(
            AppRow(
                app=process.app,
                agents=[holder.slug for holder in registration],
                env=process.scope.env,
                host=process.host,
                address=process.address,
                sdk=next((holder.sdk for holder in registration if holder.sdk is not None), None),
                holder=await _deps.named_holder(gateway, process.scope),
                connected_at=process.connected_at,
            )
        )
    return AppList(apps=listed)


# One 404 for every reason (another org, world or scope, gone): nothing leaks.
@router.post("/v1/apps/{app}/stop")
async def stop_app(app: str, key: AppKey, where: ScopeDep, gateway: GatewayDep) -> StopAppResponse:
    """Tell the app it was stopped, and close its socket, on whichever gateway holds it."""
    connected = gateway.live.processes_of(where.org, where.env)
    found = next((process for process in connected if process.app == app), None)
    every = _deps.sees_every_scope(key)
    if found is None:
        raise NotFound(NO_SUCH_APP.format(app=app))
    if not every and found.scope.holder not in ("", where.holder):
        raise NotFound(NO_SUCH_APP.format(app=app))
    who = key.bearer.key.name or key.bearer.key.label or "a server of the org"
    await found.stop(f"stopped by {who}")
    return StopAppResponse(app=app, stopped=True)


async def _socket_key(websocket: WebSocket, gateway: Gateway) -> Acting:
    data = _deps.bearer_of(websocket.headers)
    verified = None if data is None else await keys.verify(gateway.connections.pool, data)
    if verified is None:
        raise NotAllowed(_deps.TAKES_A_KEY)
    keys.check_opens(verified, HOLDING)
    return Acting(bearer=verified, env=_deps.world_of_request(websocket, verified, gateway))


def _named(raw: Json, field: str) -> str:
    if not isinstance(raw, dict):
        return ""
    named = raw.get(field)
    return named if isinstance(named, str) else ""


# What the call's log masks follows a declaration sent for this call alone, before the call
# writes the state it declares, whichever process runs it.
# The list is the org's, not the call's, and nothing lands in the log: an SDK that predates the
# command would refuse to read an entry it has no shape for.
async def _opted_out(gateway: Gateway, served: Served, agent: str, wanted: CallOptOut) -> None:
    if served.context.channel != "phone":
        raise DeclarationRefused(NO_NUMBER_TO_LIST.format(call=served.call))
    given = Given(
        kind="opt_out",
        source=ASKED_THE_AGENT,
        given_by=f"agent:{agent}",
        text=wanted.note,
        call=served.call,
    )
    await consents.give(gateway.connections.pool, served.scope, served.context.caller, given)


async def _written(served: Served, result: ToolResult) -> bool:
    """Hand the answer to the round trip waiting here, else write it once on the call's log."""
    return served.tools.answered(result) or await served.log.answer(result.written()) is not None


# What other gateways send this socket: a call they bound to it (served here and pumped down it,
# from the seq they said), an entry for it (a console's dev.request), or a stop.
async def _inbox(gateway: Gateway, socket: AppSocket) -> None:
    signal = gateway.connections.signal
    while True:
        try:
            listening = await signal.subscribe(APP_CHANNEL.format(app=socket.id))
        except NotAvailable:
            await asyncio.sleep(BOUND_RETRY_S)
            continue
        try:
            async for data in listening:
                await _for_the_app(gateway, socket, ForApp.model_validate_json(data))
        finally:
            listening.close()
        await asyncio.sleep(BOUND_RETRY_S)


async def _for_the_app(gateway: Gateway, socket: AppSocket, message: ForApp) -> None:
    if message.bound is not None:
        await _taken_here(gateway, socket.id, message.bound)
    if message.entry is not None:
        await socket.send(message.entry)
    if message.stop is not None:
        await socket.stopped(message.stop)


async def _taken_here(gateway: Gateway, app: SocketId, bound: Bound) -> None:
    served = await known_here(gateway.serving, bound.call)
    if served is None or app not in gateway.live.sockets or bound.call in gateway.live.pumps:
        return
    gateway.live.attach(bound.call, app)
    then = unanswered(await served.log.whole()) if bound.after else []
    gateway.live.pumped(bound.call, after=bound.after, then=then)
