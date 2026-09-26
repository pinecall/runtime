"""Calling somebody back: the one door that places a call, and every guard it passes first."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from starlette.status import HTTP_202_ACCEPTED

from pinecall.api.calls.deps import ServingDep
from pinecall.api.deps import (
    AdmissionDep,
    DeclarationKeyDep,
    LogsDep,
    RegistryDep,
    RoutesDep,
    SettingsDep,
    TalkKeyDep,
)
from pinecall.api.scope.request_scope import AnAgentHeld, CornerDep
from pinecall.api.telephony.deps import DispatchesDep, GuardsDep, KeptOutboundTrunksDep, OutboundDep
from pinecall.auth.keys import is_held_by
from pinecall.auth.scopes import mint_log_token, secret_for
from pinecall.orgs.outbound_guards import Asking
from pinecall.telephony.placing import Placers, Placing, place_call
from pinecall.types.today import today_in
from pinecall_protocol import WireModel
from pinecall_protocol.defs import Projection
from pinecall_protocol.rest import Dialled

router = APIRouter()

NO_LIVEKIT = (
    "this gateway has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it cannot start a job for a call"
    " it places"
)

# What the worker names when it asks for the trunk: the number the verb is about to dial, and the
# call it is dialling into. Both ride the ledger row, so a leg is read back like any other dial.
DIALLING = Query(description="the number this leg will dial, E.164")
ON_THE_CALL = Query(description="the call the leg is dialled into")


class WantedCall(WireModel):
    """What POST /v1/agents/{slug}/dial takes: who to call, and which of our numbers to show."""

    to: str
    # One of the agent's own numbers in this world. Unsaid, the first door it answers at.
    shown: str | None = Field(default=None, alias="from")
    # What the answer's log_token reads the call through, as POST /v1/tokens takes it.
    log: Projection = "public"


class TrunkNamed(WireModel):
    """What the worker is answered: the SFU's id for the org's outbound trunk, or none."""

    trunk: str | None


@router.post("/v1/agents/{slug}/dial", status_code=HTTP_202_ACCEPTED)
async def dial(
    slug: str,
    said: WantedCall,
    key: TalkKeyDep,
    registry: RegistryDep,
    table: RoutesDep,
    trunks: KeptOutboundTrunksDep,
    dispatches: DispatchesDep,
    guards: GuardsDep,
    admission: AdmissionDep,
    live: ServingDep,
    logs: LogsDep,
    settings: SettingsDep,
) -> Dialled:
    """Place a call as this agent (telephony/placing.py), and the token to read it by."""
    if dispatches is None:
        raise HTTPException(503, NO_LIVEKIT)
    placing = Placing(
        org=key.org,
        env=key.env,
        agent=slug,
        holder=is_held_by(key),
        asked_by=key.subject or key.key_id,
        to=said.to,
        shown=said.shown,
        today=today_in(settings.timezone),
    )
    placers = Placers(table, trunks, registry, guards, admission, logs, dispatches)
    placed = await place_call(placing, live.running(key.org), placers)
    return Dialled.model_validate(
        {
            "call": placed.call,
            "agent": slug,
            "to": placed.to,
            "from": placed.shown,
            "env": key.env,
            "log_token": mint_log_token(placed.call, said.log, secret_for(settings)),
        }
    )


# ── the worker's: which trunk a second leg on a live call is dialled out through ─────────────────


# A warm transfer and room.invite both dial a number INTO the call's room, and the trunk that does
# it is the org's own — the same one this door's neighbour places a call with. Which is why the
# guards are HERE and not only there: the worker cannot dial without a trunk, and it cannot have
# one without the number passing the same shape check, the same two windows and the same ledger a
# cold dial passes. Until 2026-09-22 it could — an agent with a number in its class dialled as
# often as it liked, on the org's own carrier, and no row anywhere said so.
#
# The trunk is asked of the SFU by name and never read off the row: the row is the provisioning's
# memory, the SFU is what exists, and a row naming a trunk the SFU lost is the 404 a caller heard.
@router.get("/v1/agents/{slug}/outbound-trunk", dependencies=[AnAgentHeld])
async def outbound_trunk(
    slug: str,
    key: DeclarationKeyDep,
    corner: CornerDep,
    sfu: OutboundDep,
    guards: GuardsDep,
    to: str = DIALLING,
    call: str = ON_THE_CALL,
) -> TrunkNamed:
    """The SFU's id for this org's outbound trunk, once the number it will dial has passed."""
    if sfu is None:
        return TrunkNamed(trunk=None)
    asking = Asking(
        org=corner.org,
        env=corner.env,
        agent=slug,
        to=to,
        asked_by=key.subject or key.key_id,
        call=call,
    )
    await guards.a_second_leg(asking)
    return TrunkNamed(trunk=await sfu.standing(corner.org))
