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
from pinecall.log import queries
from pinecall.tenancy import keys, tokens
from pinecall.wire.commands import SupervisorVerb, Verb
from pinecall.wire.frames import Command
from pinecall.wire.parts import Supervisor
from pinecall.wire.rest.calls import (
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
    return await _seated(gateway, key, call, "observe")


# Not hidden: livekit delivers no track of a hidden seat, and a takeover would be silent.
@router.post("/v1/calls/{call}/supervise")
async def supervise(call: str, key: _deps.SuperviseKey, gateway: GatewayDep) -> SeatResponse:
    """A seat that speaks in one live call; its token also sends the verbs."""
    return await _seated(gateway, key, call, "supervise")


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
    served = gateway.live.calls.get(call)
    if served is None:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    wanted = SupervisorVerb(by=_who(reading), verb=verb)
    if served.session is not None:
        await served.session.supervise(wanted)
    else:
        sent = Command(type="supervisor.verb", agent=served.agent, call=call, data=wanted.written())
        served.commands.put_nowait(sent)
    return VerbResponse(call=call, verb=verb.verb, seq=None)


async def _seated(gateway: Gateway, key: Acting, call: str, scope: RoomScope) -> SeatResponse:
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    if kept is None or kept.scope is None or kept.scope.org != key.org:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    if await gateway.logs.store.sealed(call):
        raise Conflict(IS_OVER.format(call=call))
    seat = tokens.seat(gateway.signer, call, scope, key.bearer)
    return SeatResponse(
        server_url=gateway.connections.settings.livekit_url_for(kept.scope.env),
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
