"""The supervisor's desk: listening in, taking a seat, and the verbs sent to a live call."""

from fastapi import APIRouter

from pinecall.domain.errors import (
    Conflict,
    NotAllowed,
    NotFound,
)
from pinecall.domain.person import RoomScope
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, GatewayDep, Reader, ReaderDep
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.calls.commands import SUPERVISOR_VERB, commanded
from pinecall.log import queries
from pinecall.tenancy import keys, reads, tokens
from pinecall.tenancy.reads import Read
from pinecall.wire.commands import SupervisorVerb, Verb
from pinecall.wire.frames import Command
from pinecall.wire.parts import Supervisor
from pinecall.wire.rest.calls import (
    ReadKind,
    SeatResponse,
    VerbResponse,
)

router = APIRouter()


IS_OVER = "call {call} is over: read its log or its recording instead"

READS_ONLY = "this token reads the call and sends no verb: steering it takes a supervise token"

A_KEY = "key:{org}"


@router.post("/v1/calls/{call}/listen")
async def listen(call: str, key: _deps.SuperviseKey, gateway: GatewayDep) -> SeatResponse:
    """A hidden seat that hears one live call."""
    return await _seated(gateway, key, call, "observe", "listen")


# Not hidden: livekit delivers no track of a hidden seat, and a takeover would be silent.
@router.post("/v1/calls/{call}/supervise")
async def supervise(call: str, key: _deps.SuperviseKey, gateway: GatewayDep) -> SeatResponse:
    """A seat that speaks in one live call; its token also sends the verbs."""
    return await _seated(gateway, key, call, "supervise", "supervise")


# The body never says who sent it: the credential does.
@router.post("/v1/calls/{call}/verbs", status_code=202)
async def queue_verb(
    call: str, verb: Verb, reading: ReaderDep, gateway: GatewayDep
) -> VerbResponse:
    """One supervise verb on a live call; the call's log says what it did."""
    if reading.visit is not None and reading.visit.scope != "supervise":
        raise NotAllowed(READS_ONLY)
    if reading.acting is not None:
        keys.check_opens(reading.acting.bearer, "supervise")
    await _deps.check_readable(gateway, reading, call)
    state = await gateway.logs.reading(call).snapshot()
    if state.seq == 0:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if await gateway.logs.store.sealed(call):
        raise Conflict(IS_OVER.format(call=call))
    wanted = SupervisorVerb(by=_who(reading), verb=verb)
    served = gateway.live.calls.get(call)
    if served is not None and served.session is not None:
        await served.session.supervise(wanted)
        return VerbResponse(call=call, verb=verb.verb, seq=None)
    # Whichever gateway runs the call's worker stream or its session takes it.
    sent = Command(type=SUPERVISOR_VERB, agent=state.agent, call=call, data=wanted.written())
    if not await commanded(gateway.connections.signal, sent):
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    return VerbResponse(call=call, verb=verb.verb, seq=None)


# A seat hears the call live: it is recorded as a read of the call, once it is handed out.
async def _seated(
    gateway: Gateway, key: Acting, call: str, scope: RoomScope, what: ReadKind
) -> SeatResponse:
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None or kept.scope.org != key.org:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    keys.check_agent(key.bearer, kept.agent)
    if await gateway.logs.store.sealed(call):
        raise Conflict(IS_OVER.format(call=call))
    seat = tokens.seat(gateway.signer, call, scope, key.bearer)
    read = Read(call, what, _deps.asked_by(key))
    await reads.record(gateway.connections.pool, kept.scope, read)
    return SeatResponse(
        server_url=gateway.connections.settings.browser_livekit_url(kept.scope.env),
        participant_token=seat.token,
        call=call,
        identity=seat.identity,
        org=key.org,
        subject=seat.subject,
        name=seat.name,
    )


def _who(reading: Reader) -> Supervisor:
    if reading.visit is not None:
        if reading.visit.subject is not None:
            return Supervisor(id=reading.visit.subject, name=reading.visit.name)
        return Supervisor(id=reading.visit.identity or "")
    if reading.acting is None:
        return Supervisor(id="")
    bearer = reading.acting.bearer
    if bearer.key.subject is not None:
        name = bearer.member.name if bearer.member is not None else bearer.key.name
        return Supervisor(id=bearer.key.subject, name=name)
    return Supervisor(id=A_KEY.format(org=bearer.key.org))
