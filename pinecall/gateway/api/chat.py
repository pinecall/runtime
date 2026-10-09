"""A written call over a WebSocket: a message in, every entry of the call out."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketState

from pinecall.domain.call import CallContext, Contact, Route, new_call_id, today_in
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotFound,
    PinecallError,
    QuotaExhausted,
)
from pinecall.domain.names import THE_WIDGET, Json, JsonObject
from pinecall.domain.scope import Scope
from pinecall.gateway import _deps
from pinecall.gateway._call_setup import exhausted
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP, Registration
from pinecall.gateway._text_calls import open_text, resume_text, tokens_of
from pinecall.log.logs import Subscription
from pinecall.session import text
from pinecall.session.session import Session
from pinecall.tenancy import admission, keys, personas

router = APIRouter()


NOT_TAKEN_UP = "call {call} cannot be taken up: it is over, or not this agent's; open a new one"

NOT_A_STATE = "?state= is the state the call opens in: a JSON object of the class's fields"

STATE_TOO_BIG = "?state= is {size} bytes: the state a call opens in is {at_most} bytes at most"

A_STATE_AT_MOST = 16 * 1024

_A_STATE: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

OVER = "the call ended: {reason}"

# What uvicorn closes with on a stop; a caller never sends it, so the call is left for the next
# process to take up.
SERVICE_RESTART = 1012


# The org's key, never a room token (`pinecall chat`, `test`, `simulate`). A refusal accepts the
# socket first: a close before the handshake is a bare 403 with no reason.
@router.websocket("/v1/chat")
async def chat(websocket: WebSocket) -> None:
    """One text call: {text} frames in, every entry of the call out."""
    gateway = _deps.gateway_of(websocket)
    try:
        found, again = await _chatting(websocket, gateway)
        session = (
            await _taken_up(gateway, found, again)
            if again
            else await open_text(
                gateway.serving, found, await _chat_context(websocket, gateway, found)
            )
        )
    except PinecallError as refused:
        await websocket.accept()
        await websocket.close(code=_deps.POLICY_VIOLATION, reason=_deps.close_reason(str(refused)))
        return
    await websocket.accept()
    served = gateway.live.calls.get(session.call.context.call)
    heard = await served.log.followed() if served is not None else None
    sending = asyncio.create_task(_sent(websocket, heard)) if heard is not None else None
    # The call can end from the desk or by the model: the socket is closed under the caller.
    ending = asyncio.create_task(_hung_up(websocket, session))
    try:
        if not again:
            await session.start()
        await _turns(websocket, gateway, session)
    finally:
        ending.cancel()
        if sending is not None:
            sending.cancel()


async def _hung_up(websocket: WebSocket, session: Session) -> None:
    await session.over.wait()
    reason, _ = session.ended or ("error", "platform")
    if websocket.application_state is WebSocketState.CONNECTED:
        await websocket.close(reason=_deps.close_reason(OVER.format(reason=reason)))


async def _chatting(websocket: WebSocket, gateway: Gateway) -> tuple[Registration, str | None]:
    data = _deps.bearer_of(websocket.headers)
    verified = None if data is None else await gateway.keys.verify(data)
    if verified is None:
        raise NotAllowed(_deps.TAKES_A_KEY)
    keys.check_opens(verified, "talk")
    env = _deps.world_of_request(websocket, verified)
    scope = keys.scope_of(verified, env)
    slug = websocket.query_params.get("agent", "")
    keys.check_agent(verified, slug)
    app = websocket.query_params.get("app")
    registration = gateway.sockets.serving(scope, slug, app)
    if registration is None:
        raise NotFound(_why_not(gateway, scope, slug, app))
    return registration, websocket.query_params.get("call")


async def _taken_up(gateway: Gateway, registration: Registration, call: str) -> Session:
    session = await resume_text(
        gateway.serving, registration, call, gateway.connections.settings.timezone
    )
    if session is None:
        raise Conflict(NOT_TAKEN_UP.format(call=call))
    return session


# A web caller is a visitor id; the org's own key may name who it is (`?contact=`) and which of
# the agent's synthetic callers plays it (`?persona=`), whose rules are frozen into the call here.
async def _chat_context(
    websocket: WebSocket, gateway: Gateway, registration: Registration
) -> CallContext:
    scope = registration.scope
    named = websocket.query_params.get("persona") or None
    persona = (
        None
        if named is None
        else await personas.persona(gateway.connections.pool, scope.org, registration.slug, named)
    )
    contact = websocket.query_params.get("contact")
    return CallContext(
        call=new_call_id(),
        channel=THE_WIDGET,
        direction="inbound",
        caller=websocket.query_params.get("caller") or f"web_{new_call_id()[5:17]}",
        contact=Contact(id=contact) if contact else None,
        persona=named,
        accepts_when=None if persona is None else persona.persona.accepts_when or None,
        declines_when=None if persona is None else persona.persona.declines_when or None,
        route=Route(org=scope.org, agent=registration.slug, channel=THE_WIDGET, env=scope.env),
        today=today_in(gateway.connections.settings.timezone),
        holder=scope.holder or None,
        state=_opening_state(websocket),
    )


def _opening_state(websocket: WebSocket) -> JsonObject:
    query = websocket.query_params.get("state")
    if not query:
        return {}
    size = len(query.encode())
    if size > A_STATE_AT_MOST:
        raise DeclarationRefused(STATE_TOO_BIG.format(size=size, at_most=A_STATE_AT_MOST))
    try:
        return _A_STATE.validate_json(query)
    except ValidationError:
        raise DeclarationRefused(NOT_A_STATE) from None


# A failed send flips the application state; a receive after it raises RuntimeError, so the
# state is asked before each receive.
async def _turns(websocket: WebSocket, gateway: Gateway, session: Session) -> None:
    scope = session.call.context.route
    try:
        while websocket.application_state is WebSocketState.CONNECTED:
            frame = await websocket.receive_json()
            if _hangs_up(frame):
                # Ended before the socket closes, so the caller hears the call is over before
                # it stops whatever served it.
                await text.end(session, "caller_hung_up", "caller")
                over = OVER.format(reason="caller_hung_up")
                await websocket.close(reason=_deps.close_reason(over))
                return
            data = _text_of(frame)
            if not data:
                continue
            try:
                await admission.admit_turn(
                    gateway.connections.pool,
                    Scope(scope.org, scope.env),
                    turns=session.call.turns,
                    tokens=tokens_of(session.usage),
                    at=gateway.logs.store.clock(),
                )
            except QuotaExhausted as refused:
                await exhausted(
                    gateway.connections,
                    gateway.logs,
                    Scope(scope.org, scope.env),
                    session.call.config.slug,
                    refused,
                )
                await text.end(session, "timeout", "platform")
                await websocket.close(
                    code=_deps.POLICY_VIOLATION, reason=_deps.close_reason(str(refused))
                )
                return
            await text.hears(session, data)
    except WebSocketDisconnect as gone:
        if gone.code != SERVICE_RESTART and not session.closed:
            await text.end(session, "caller_hung_up", "caller")
        return
    # A send that found the caller gone closed the socket under the loop, with no close to hear.
    if not session.closed:
        await text.end(session, "caller_hung_up", "caller")


async def _sent(websocket: WebSocket, heard: Subscription) -> None:
    async for entry in heard:
        await websocket.send_json(entry.written())


def _hangs_up(data: Json) -> bool:
    return isinstance(data, dict) and data.get("hangup") is True


def _text_of(data: Json) -> str:
    if not isinstance(data, dict):
        return ""
    text_said = data.get("text")
    return text_said if isinstance(text_said, str) else ""


def _why_not(gateway: Gateway, scope: Scope, slug: str, app: str | None) -> str:
    if app is not None:
        return NOT_THAT_APP.format(app=app, slug=slug)
    if gateway.sockets.of(scope, slug) is not None:
        return NO_UNCLAIMED.format(slug=slug)
    return f"{NO_AGENT.format(slug=slug)}: start the app that serves it, then chat again"
