"""Calling somebody back: the one door that places a call, and every guard it passes first."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api._corner import CornerDep
from pinecall.api._deps import AdmissionDep, DeclarationKeyDep, LogsDep, RoutesDep, TalkKeyDep
from pinecall.api._placing import DispatchesDep, GuardsDep, KeptOutboundTrunksDep, OutboundTrunksDep
from pinecall.api._serving import ServingDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.auth.keys import KeyRecord, held_by
from pinecall.log.writers import Logs
from pinecall.orgs.admission import QuotaExhausted
from pinecall.orgs.guards import Asking, DialRefused
from pinecall.routes.dispatching import Dialling, Dispatches, Job
from pinecall.routes.table import Routes
from pinecall.session.first_entries import arrived
from pinecall.types import CallContext, DeclarationRefused, Route, a_call_id, an_e164
from pinecall_protocol import WireModel, encode
from pinecall_protocol.events import CallEnded

router = APIRouter()

PLACED = 202

NO_PHONE_DOOR = (
    "agent {slug} answers no phone number in {env}: a call back is shown as one of the org's own"
    " numbers, so there has to be one"
)
NOT_OUR_NUMBER = "{number} is not a number agent {slug} answers in {env}"
NO_TRUNK = (
    "this org has no outbound trunk yet: POST /v1/carrier/outbound provisions one, and"
    " GET /v1/carrier/outbound says what is still missing"
)
NOBODY_HOLDING = (
    "nobody is holding {slug} in {env}: a call this box placed would be answered by no app at"
    " all, so it is not placed"
)
NO_LIVEKIT = (
    "this gateway has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it cannot start a job for a call"
    " it places"
)
DID_NOT_DIAL = "the media plane refused the call: {why}"


class WantedCall(WireModel):
    """What POST /v1/agents/{slug}/dial takes: who to call, and which of our numbers to show."""

    to: str
    # One of the agent's own numbers in this world. Unsaid, the first door it answers at.
    shown: str | None = Field(default=None, alias="from")


@router.post("/v1/agents/{slug}/dial", status_code=PLACED)
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
) -> dict[str, Any]:
    """Place a call as this agent: the guards, the log, and a job in a room named by the call."""
    if dispatches is None:
        raise HTTPException(503, NO_LIVEKIT)
    holder = held_by(key)
    doors = await _doors_of(table, key, slug)
    shown = _shown_as(said, doors, key, slug)
    trunk = await trunks.of(key.org)
    if trunk is None:
        raise HTTPException(409, NO_TRUNK)
    # A call this box places is answered by whatever holds the agent in that world, and in
    # production a person's key holds nothing — the box's own process does. So the question is
    # not whose key this is but whether anybody is there: a dialled call whose tools go out to
    # nobody is a stranger's phone ringing for a conversation that cannot happen. In the sandbox
    # `holder` is the person's own corner, so a developer's dial reaches their own copy.
    if registry.of(key.env, slug, holder) is None:
        raise HTTPException(409, NOBODY_HOLDING.format(slug=slug, env=key.env))
    call = a_call_id()
    asking = Asking(
        org=key.org,
        env=key.env,
        agent=slug,
        to=said.to,
        asked_by=key.subject or key.key_id,
        call=call,
        shown=shown,
    )
    try:
        allowed = await guards.judged(asking)
    except DialRefused as refused:
        raise HTTPException(refused.refusal.status, str(refused)) from refused
    try:
        await admission.a_call(key.org, slug, live.running(key.org))
    except QuotaExhausted as spent:
        raise HTTPException(429, str(spent)) from spent
    context = CallContext(
        call=call,
        channel="phone",
        direction="outbound",
        # The far end is the contact: it is who the call is with, and what memory files it under.
        caller=allowed.destination.number,
        route=_shown_from(doors, shown),
        today=date.today(),
        holder=holder,
    )
    await _opened(logs, context, slug, shown, asking.asked_by)
    dialling = Dialling(
        trunk=trunk.trunk_id,
        to=allowed.destination.number,
        shown=shown,
        max_duration_s=allowed.policy.max_duration_s,
    )
    await _started(dispatches, logs, context, slug, dialling)
    return {
        "call": call,
        "agent": slug,
        "to": allowed.destination.number,
        "from": shown,
        "env": key.env,
    }


async def _doors_of(table: Routes, key: KeyRecord, slug: str) -> list[Route]:
    """Every phone door this agent answers in the key's world: what a call back may be shown as."""
    doors = await table.of_org(key.org, key.env)
    mine = [door for door in doors if door.agent == slug and door.channel == "phone"]
    if not mine:
        raise HTTPException(404, NO_PHONE_DOOR.format(slug=slug, env=key.env))
    return mine


def _shown_as(said: WantedCall, doors: list[Route], key: KeyRecord, slug: str) -> str:
    """Which of the org's numbers the far end sees: one this agent answers, or its first."""
    theirs = [door.number for door in doors if door.number is not None]
    if said.shown is None:
        return theirs[0]
    try:
        wanted = an_e164(said.shown)
    except DeclarationRefused as malformed:
        raise HTTPException(400, str(malformed)) from malformed
    if wanted not in theirs:
        raise HTTPException(400, NOT_OUR_NUMBER.format(number=wanted, slug=slug, env=key.env))
    return wanted


def _shown_from(doors: list[Route], shown: str) -> Route:
    """The door the number belongs to: the call's route is the one it is placed from."""
    return next(door for door in doors if door.number == shown)


# The log opens HERE and not in the worker, because this is where both numbers and the name of
# whoever asked are known, and because the 202 hands back a call id a console starts reading at
# once. POST /v1/calls writes no second first entry for an outbound call.
async def _opened(logs: Logs, context: CallContext, slug: str, shown: str, asked_by: str) -> None:
    """The call's head row, claimed for this corner, and call.dialing on top of it."""
    await logs.owned(context.call, slug, context.route.org, context.env, context.holder)
    type, event = arrived(context, shown, asked_by)
    await logs.writing(context.call, slug).append(type, encode(event))


async def _started(
    dispatches: Dispatches, logs: Logs, context: CallContext, slug: str, dialling: Dialling
) -> None:
    """The job, or a log that says why there was never a call rather than a room billed for one."""
    job = Job(
        call=context.call,
        agent=slug,
        org=context.route.org,
        env=context.env,
        dialling=dialling,
        holder=context.holder,
    )
    try:
        await dispatches.started(job)
    except Exception as refused:  # noqa: BLE001 — every way the SFU says no ends the same way
        await _never_rang(logs, context, slug)
        raise HTTPException(502, DID_NOT_DIAL.format(why=refused)) from refused


async def _never_rang(logs: Logs, context: CallContext, slug: str) -> None:
    """call.ended with dial_failed, and the log sealed: nothing else will be written to it."""
    log = logs.writing(context.call, slug)
    ended = CallEnded(
        reason="dial_failed", ended_by="platform", ended_at=time.time(), duration_s=0.0
    )
    await log.append("call.ended", encode(ended))
    await log.seal()
    logs.forget(context.call)


# ── the worker's: which trunk a second leg on a live call is dialled out through ─────────────────


# A warm transfer and room.invite both dial a number INTO the call's room, and the trunk that does
# it is the org's own — the same one this door's neighbour places a call with. The worker asks for
# it only when a verb wants one, so a box with no vault key and an org with no carrier answer the
# same way they refuse a dial: null, and the verb says so in the call's log by name.
@router.get("/v1/agents/{slug}/outbound-trunk")
async def outbound_trunk(
    slug: str,
    key: DeclarationKeyDep,  # noqa: ARG001 — the scope is asked here; the corner says where
    corner: CornerDep,
    registry: RegistryDep,
    trunks: OutboundTrunksDep,
) -> dict[str, str | None]:
    """The SFU's id for this org's outbound trunk, or null when it has none to dial through."""
    held = registry.of(corner.env, slug, corner.holder)
    if held is None or held.org != corner.org:
        raise HTTPException(404, NO_AGENT.format(slug=slug))
    trunk = None if trunks is None else await trunks.of(corner.org)
    return {"trunk": None if trunk is None else trunk.trunk_id}
