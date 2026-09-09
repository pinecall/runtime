"""WS /v1/chat: the caller's side of a text call — words in, the call's own log entries out."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from pinecall.api._deps import (
    AdmissionDep,
    KeysDep,
    LlmsDep,
    LogsDep,
    OverridesDep,
    VaultDep,
    a_key_on_a_socket,
)
from pinecall.api._live import Live, LiveDep
from pinecall.api.agents import on_a_call as commands
from pinecall.api.agents.registry import (
    NO_AGENT,
    NO_UNCLAIMED,
    NOT_THAT_APP,
    Registry,
    RegistryDep,
    SocketId,
)
from pinecall.api.calls.opening import a_text_call
from pinecall.auth.bearer import POLICY_VIOLATION
from pinecall.auth.scopes import a_visitor
from pinecall.log.entry import Entry
from pinecall.log.writers import Logs
from pinecall.orgs.admission import QuotaExhausted
from pinecall.providers.models import NoProvider
from pinecall.session.text.session import TextSession, Watcher
from pinecall.types import THE_WIDGET, CallContext, Route, a_call_id
from pinecall_protocol import encode

# Importing the handlers is what registers them: the app socket's table is filled at import time,
# and this router is the one thing the gateway includes to open the text channel at all.
__all__ = ["commands", "router"]

logger = logging.getLogger(__name__)
router = APIRouter()

# A caller who came with the key and named an agent nobody is holding. The socket is accepted only
# so that it can be closed with words: a close before the handshake is an HTTP 403 with no body,
# and the caller could only report "the gateway refused the chat socket:" with nothing after it.
_NOBODY_SERVING = f"{NO_AGENT}: start the app that serves it, then chat again"

# The slug is held, by another org: this key opens none of its calls.
ANOTHER_ORGS = "agent {slug} is another org's: this key opens none of its calls"

# A close frame carries at most 123 bytes of reason (RFC 6455 §5.5), and a longer one is not
# truncated by the library: it raises, the connection dies with no close frame at all, and the
# caller reports "the gateway refused the chat socket:" with nothing after it — the very sentence
# this door exists to avoid. Found by running one refusal whose text was three words too long.
CLOSE_REASON_BYTES = 123


def _as_a_close_reason(said: str) -> str:
    """The refusal as a close frame may carry it: cut to 123 bytes rather than lost whole."""
    # errors="ignore" drops the half character the cut may leave; nothing here ever reaches it.
    return said.encode()[:CLOSE_REASON_BYTES].decode(errors="ignore")


# A chat token exists (POST /v1/tokens, scope=chat) and is a LiveKit room token; this socket is
# the text channel that predates the room, and it takes the org's API key — the same key the app
# socket honours, which is what `pinecall chat`, `test` and `simulate` hold. Verifying a chat
# token here is ms-7's integration.
@router.websocket("/v1/chat")
async def chat(
    websocket: WebSocket,
    keys: KeysDep,
    logs: LogsDep,
    registry: RegistryDep,
    live: LiveDep,
    llms: LlmsDep,
    overrides: OverridesDep,
    admission: AdmissionDep,
    vault: VaultDep,
) -> None:
    """One caller, one text call: they send {text}, they receive every entry of their own call."""
    key = await a_key_on_a_socket(websocket, keys)
    if key is None:
        await websocket.close(code=POLICY_VIOLATION)
        return
    slug = websocket.query_params.get("agent", "")
    # `?app=` is how `pinecall chat` is served by its OWN process, where the tenant's breakpoints
    # are: without it a call takes whichever socket registered last. See docs/decisions/dispatch.md.
    app = websocket.query_params.get("app")
    held = registry.serving(slug, app)
    # An API key IS its org, on this socket as on every door: another org's agent is refused in a
    # sentence that names the agent and not the org that holds it.
    if held is None or held.org != key.org:
        why = ANOTHER_ORGS if held is not None else _why_not(registry, slug, app)
        await websocket.accept()
        await websocket.close(
            code=POLICY_VIOLATION, reason=_as_a_close_reason(why.format(slug=slug))
        )
        return
    # Everything a text call needs before its first word, in the one order both text doors take
    # it in. The refusals are said HERE, because only this door knows a close frame carries 123
    # bytes and that a reason must never reach a stranger as a traceback mid-handshake.
    try:
        opened = await a_text_call(
            held,
            _a_context(websocket, held.org, slug),
            overrides,
            vault,
            llms,
            admission,
            logs,
            live.running(held.org),
        )
    except NoProvider as missing:
        logger.warning("chat refused for %s: %s", slug, missing)
        await websocket.close(code=POLICY_VIOLATION, reason=_as_a_close_reason(str(missing)))
        return
    except QuotaExhausted as refused:
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=_as_a_close_reason(str(refused)))
        return
    await websocket.accept()
    session = opened.session
    await logs.owned(session.call, slug, held.org)
    await _talk(websocket, session, live, logs, held.owner, held.org)


# Three reasons a chat cannot open, and they are three different things to do about it: the caller
# named an app that is not there, nobody at all is holding the agent, or the only apps holding it
# are consoles serving their own calls. A single sentence for all three would name none of them.
def _why_not(registry: Registry, slug: str, app: SocketId | None) -> str:
    """Why this caller gets no call, in words the person who ran the command can act on."""
    if app is not None:
        return NOT_THAT_APP.format(app=app, slug=slug)
    if registry.of(slug) is not None:
        return NO_UNCLAIMED.format(slug=slug)
    return _NOBODY_SERVING.format(slug=slug)


async def _talk(
    websocket: WebSocket, session: TextSession, live: Live, logs: Logs, app: SocketId, org: str
) -> None:
    """The call, from call.started to the hangup: every frame the caller sends is one turn."""
    # Every entry, unprojected, to both sides: the caller's socket, watched from here, and the
    # app's, which is the one delivery a worker-run call is put on too (api/_live.py). The
    # public and tenant projections are the sink's, and the state card of this milestone owns them.
    session.watch(_sending(websocket))
    live.serve(session.call, session.agent, org, logs.writing(session.call, session.agent), app)
    live.open(session)
    try:
        await session.start()
        await _every_turn(websocket, session)
        await session.hangup("caller_hung_up", "caller")
    finally:
        live.close(session.call)
        # The log is sealed and its readers have finished; nothing more will ever be appended.
        logs.forget(session.call)


async def _every_turn(websocket: WebSocket, session: TextSession) -> None:
    """Every frame the caller sends is one turn, until the caller is gone."""
    # A send to a caller who already left flips starlette's application_state under us — the
    # session drops that watcher — and the next receive would be a RuntimeError instead of a
    # disconnect. So the state is read before every receive, and either way of leaving ends here.
    try:
        while websocket.application_state is WebSocketState.CONNECTED:
            text = _said(await websocket.receive_json())
            if text:
                await session.hears(text)
    except WebSocketDisconnect:
        return


def _a_context(websocket: WebSocket, org: str, slug: str) -> CallContext:
    """One call, minted here: the id, who the caller is, and the door they came through."""
    # A web caller is nobody yet: the visitor id travels as the calling side, which is what
    # call.started carries as `from`. Both ids are the shapes the token door mints too.
    return CallContext(
        call=a_call_id(),
        channel=THE_WIDGET,
        direction="inbound",
        caller=websocket.query_params.get("caller") or a_visitor(),
        route=Route(org=org, agent=slug, channel=THE_WIDGET, number=None),
        today=date.today(),
    )


def _sending(websocket: WebSocket) -> Watcher:
    """The caller's socket as a watcher of its own call."""

    async def send(entry: Entry) -> None:
        await websocket.send_json(encode(entry))

    return send


# A caller's frame carries one field. A frame that carries anything else is not a turn, and the
# call goes on: a stray ping from a browser must not end somebody's conversation.
def _said(frame: Any) -> str:
    """What the caller wrote, off a frame that may not be a frame at all."""
    if not isinstance(frame, dict):
        return ""
    text: Any = cast("dict[str, Any]", frame).get("text")
    return text if isinstance(text, str) else ""
