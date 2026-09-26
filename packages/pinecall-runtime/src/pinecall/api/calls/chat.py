"""WS /v1/chat: the caller's side of a text call — words in, the call's own log entries out."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from pinecall.api.agents import call_commands as commands
from pinecall.api.calls.opening import open_text_call
from pinecall.api.calls.resume import taken_up
from pinecall.api.deps import (
    AdmissionDep,
    CallIndexDep,
    KeysDep,
    LiveDep,
    LlmsDep,
    LogsDep,
    LookupsDep,
    MembersDep,
    RegistryDep,
    SettingsDep,
    TuningDep,
    VaultDep,
    get_socket_key,
)
from pinecall.api.evals.personas import get_personas
from pinecall.auth.bearer import POLICY_VIOLATION, close_reason
from pinecall.auth.keys import KeyRecord, cannot_open, is_held_by
from pinecall.auth.scopes import new_visitor_identity
from pinecall.live.calls import Live
from pinecall.live.registry import NO_AGENT, NO_UNCLAIMED, NOT_THAT_APP, Registry
from pinecall.live.sockets import Registration, SocketId
from pinecall.log.entry import Entry
from pinecall.log.writers import Logs
from pinecall.orgs.admission import QuotaExhausted
from pinecall.providers.models import NoProvider
from pinecall.session.text.session import TextSession, Watcher
from pinecall.session.text.turn_allowance import TurnRefused
from pinecall.types import THE_WIDGET, CallContext, Contact, Env, Route, new_call_id
from pinecall.types.today import today_in
from pinecall_protocol import encode

# Importing the handlers is what registers them: the app socket's table is filled at import time,
# and this router is the one thing the gateway includes to open the text channel at all.
__all__ = ["commands", "router"]

logger = logging.getLogger(__name__)

# What uvicorn closes every WebSocket with when the server stops (RFC 6455's 1012, "service
# restart"): the one close a caller never sends.
SERVICE_RESTART = 1012

# A caller asked to come back to a call that cannot be taken up: it ended, or it is another agent's.
NOT_TAKEN_UP = "call {call} cannot be taken up: it is over, or not this agent's — open a new one"

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
# this door exists to avoid. Found by running one refusal whose text was three words too long;
# the cut is auth/bearer.py's, so the app socket refuses the same way.


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
    tuning: TuningDep,
    admission: AdmissionDep,
    vault: VaultDep,
    lookups: LookupsDep,
    settings: SettingsDep,
    members: MembersDep,
    index: CallIndexDep,
) -> None:
    """One caller, one text call: they send {text}, they receive every entry of their own call."""
    try:
        key = await get_socket_key(websocket, keys, members, settings)
    except PermissionError as refused:
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(str(refused)))
        return
    if key is None:
        await websocket.close(code=POLICY_VIOLATION)
        return
    # A real key of the right org that may not talk: told so, in the one sentence every door says.
    if (closed := cannot_open(key, "talk")) is not None:
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(closed))
        return
    slug = websocket.query_params.get("agent", "")
    # `?app=` is how `pinecall chat` is served by its OWN process, where the tenant's breakpoints
    # are: without it a call takes whichever socket registered last. See docs/decisions/dispatch.md.
    app = websocket.query_params.get("app")
    held = registry.serving(key.env, slug, app, is_held_by(key))
    # An API key IS its org, on this socket as on every door: another org's agent is refused in a
    # sentence that names the agent and not the org that holds it.
    if held is None or held.org != key.org:
        why = ANOTHER_ORGS if held is not None else _why_not(registry, key, slug, app)
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(why.format(slug=slug)))
        return
    # `?call=` is a caller coming back to a call whose gateway restarted under it: the call is
    # taken up from its log, not opened again, and the caller reads on from where it was.
    again = websocket.query_params.get("call")
    if again:
        await _taken_up(
            websocket,
            again,
            held,
            index,
            logs,
            live,
            tuning,
            vault,
            llms,
            lookups,
            settings,
            admission,
        )
        return
    # Everything a text call needs before its first word, in the one order both text doors take
    # it in. The refusals are said HERE, because only this door knows a close frame carries 123
    # bytes and that a reason must never reach a stranger as a traceback mid-handshake.
    try:
        opened = await open_text_call(
            held,
            build_call_from_socket(
                websocket,
                held.org,
                held.env,
                slug,
                settings.timezone,
                await _the_rule_of(websocket, held.org),
            ),
            tuning,
            vault,
            llms,
            admission,
            logs,
            live.running(held.org),
            lookups,
            settings.budgets,
        )
    except NoProvider as missing:
        logger.warning("chat refused for %s: %s", slug, missing)
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(str(missing)))
        return
    except QuotaExhausted as refused:
        await websocket.accept()
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(str(refused)))
        return
    await websocket.accept()
    session = opened.session
    await logs.owned(session.call, slug, held.org, held.env, held.holder, opened.versions)
    await _talk(websocket, session, live, logs, held.owner, held.org, held.holder)


# Three reasons a chat cannot open, and they are three different things to do about it: the caller
# named an app that is not there, nobody at all is holding the agent, or the only apps holding it
# are consoles serving their own calls. A single sentence for all three would name none of them.
def _why_not(registry: Registry, key: KeyRecord, slug: str, app: SocketId | None) -> str:
    """Why this caller gets no call, in words the person who ran the command can act on."""
    if app is not None:
        return NOT_THAT_APP.format(app=app, slug=slug)
    if registry.of(key.env, slug, is_held_by(key)) is not None:
        return NO_UNCLAIMED.format(slug=slug)
    return _NOBODY_SERVING.format(slug=slug)


async def _talk(
    websocket: WebSocket,
    session: TextSession,
    live: Live,
    logs: Logs,
    app: SocketId,
    org: str,
    holder: str | None,
) -> None:
    """The call, from call.started to the hangup: every frame the caller sends is one turn."""
    # Every entry, unprojected, to both sides: the caller's socket, watched from here, and the
    # app's, which is the one delivery a worker-run call is put on too (live/calls.py). The
    # public and tenant projections are the sink's, and the state card of this milestone owns them.
    session.watch(_sending(websocket))
    live.serve(
        session.call,
        session.agent,
        org,
        logs.writing(session.call, session.agent),
        app,
        context=session.context,
        config=session.config,
        holder=holder,
    )
    live.open(session)
    try:
        await session.start()
    except BaseException:
        live.close(session.call)
        logs.forget(session.call)
        raise
    await _talking(websocket, session, live, logs)


# A call taken up is a call already going: nothing is started, nothing said, and the caller's next
# frame is its next turn. One that is not there to take up — over, or not this agent's — is
# refused in a sentence, and the caller opens a new one.
async def _taken_up(
    websocket: WebSocket,
    call: str,
    held: Registration,
    index: CallIndexDep,
    logs: Logs,
    live: Live,
    tuning: TuningDep,
    vault: VaultDep,
    llms: LlmsDep,
    lookups: LookupsDep,
    settings: SettingsDep,
    admission: AdmissionDep,
) -> None:
    """The caller back on a call its gateway forgot, or a close saying why not."""
    await websocket.accept()
    context = build_call_from_socket(websocket, held.org, held.env, held.slug, settings.timezone)
    try:
        opened = await taken_up(
            call,
            held,
            context,
            index,
            logs,
            live,
            tuning,
            vault,
            llms,
            lookups,
            settings.budgets,
            admission,
            _sending(websocket),
        )
    except NoProvider as missing:
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(str(missing)))
        return
    if opened is None:
        reason = close_reason(NOT_TAKEN_UP.format(call=call))
        await websocket.close(code=POLICY_VIOLATION, reason=reason)
        return
    await _talking(websocket, opened.session, live, logs)


async def _talking(websocket: WebSocket, session: TextSession, live: Live, logs: Logs) -> None:
    """Every frame the caller sends is one turn, until they go; then the call is over."""
    try:
        # A gateway stopping closes every socket with 1012 before it goes: that is not the caller
        # hanging up, and the call is left open, for the next process to take up when they are back.
        if not await _every_turn(websocket, session):
            await session.hangup("caller_hung_up", "caller")
    finally:
        live.close(session.call)
        # The log is sealed and its readers have finished; nothing more will ever be appended.
        logs.forget(session.call)


async def _every_turn(websocket: WebSocket, session: TextSession) -> bool:
    """Every frame the caller sends is one turn, until the caller is gone. True when it was the
    gateway that went — stopping — and not the caller."""
    # A send to a caller who already left flips starlette's application_state under us — the
    # session drops that watcher — and the next receive would be a RuntimeError instead of a
    # disconnect. So the state is read before every receive, and either way of leaving ends here.
    try:
        while websocket.application_state is WebSocketState.CONNECTED:
            text = _said(await websocket.receive_json())
            if text:
                await session.hears(text)
    except WebSocketDisconnect as gone:
        return not hung_up_by(gone.code)
    except TurnRefused as refused:
        # The session already ended the call; the caller is told why in the close, as at the open.
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(str(refused)))
    return False


def hung_up_by(code: int) -> bool:
    """Whether a socket that closed with this code was the caller leaving — anything but 1012."""
    return code != SERVICE_RESTART


def build_call_from_socket(
    websocket: WebSocket,
    org: str,
    env: Env,
    slug: str,
    zone: str,
    rule: tuple[str | None, str | None] = (None, None),
) -> CallContext:
    """One call, minted here: the id, who the caller is, and the door they came through."""
    accepts_when, declines_when = rule
    # A web caller is nobody yet: the visitor id travels as the calling side, which is what
    # call.started carries as `from`. Both ids are the shapes the token door mints too.
    return CallContext(
        call=new_call_id(),
        channel=THE_WIDGET,
        direction="inbound",
        caller=websocket.query_params.get("caller") or new_visitor_identity(),
        contact=_who_they_say_they_are(websocket),
        # `?persona=` is a written simulation saying who is being played on this call: `pinecall
        # simulate` drives the turns from here, and without it nothing on the call named the
        # caller. It rides call.started and is projected into call_facts from there, which is what
        # the Personas screen reads a caller's own runs off. A person's chat names none.
        persona=websocket.query_params.get("persona") or None,
        accepts_when=accepts_when,
        declines_when=declines_when,
        route=Route(org=org, agent=slug, channel=THE_WIDGET, number=None, env=env),
        today=today_in(zone),
    )


# The spoken door is handed the whole persona and puts its rule on the dispatch; this one is handed
# a name, so the rule is read off the org's own list here, in-process, as the call opens. It lands
# on call.started by the same field, and the judge at hang-up cannot tell the two doors apart. A
# name nobody wrote is a call with no rule, as it was: the caller still runs, and nobody judges it.
async def _the_rule_of(websocket: WebSocket, org: str) -> tuple[str | None, str | None]:
    """When the caller being played accepts the call, and when it declines it; none for a person."""
    name = websocket.query_params.get("persona")
    if not name:
        return None, None
    one = await get_personas(websocket).named(org, name)
    if one is None:
        return None, None
    return one["accepts_when"] or None, one["declines_when"] or None


# A number identifies a caller by itself; a web visitor is nobody until somebody says who they
# are. In production that somebody is the token door, which seals a contact id the browser
# cannot forge. Here it is the query string, and it is the same field: without it an agent that
# declares `memory` remembers nothing of a web caller, which is right, and untestable, which is
# not. The key on this socket is the org's own, so what it says about its own contact is its own.
def _who_they_say_they_are(websocket: WebSocket) -> Contact | None:
    """The contact this socket claims to be, when it claims one; memory files the call under it."""
    said = websocket.query_params.get("contact")
    return Contact(id=said) if said else None


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
