"""A call placed as an agent: the number shown, the trunk, who answers, the guards, the log."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

from pinecall.errors import PinecallError
from pinecall.log.writers import Logs
from pinecall.orgs.admission import Admission
from pinecall.orgs.outbound_credentials import OutboundTrunks
from pinecall.orgs.outbound_guards import Asking, Guards
from pinecall.routes.dispatch import Dialling, Dispatches, Job
from pinecall.routes.records import Routes
from pinecall.session.first_entries import arrival_entry
from pinecall.types import CallContext, Env, Route, new_call_id, parse_e164
from pinecall_protocol import encode
from pinecall_protocol.events import CallEnded

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
DID_NOT_DIAL = "the media plane refused the call: {why}"


class NoPhoneDoor(PinecallError):
    """The agent answers no phone number in this world, so there is nothing to show the far end."""


class NotOurNumber(PinecallError):
    """The number asked to be shown is not one this agent answers at."""


class NoTrunk(PinecallError):
    """The org has no outbound trunk to place the call through yet."""


class NobodyHolding(PinecallError):
    """No app holds the agent in this world: the call would ring for a conversation nobody has."""


class DidNotDial(PinecallError):
    """The media plane refused the job; the call's log says it never rang, and is sealed."""


@dataclass(frozen=True)
class Placing:
    """Who asks for the call, as whom, to whom: what the door read off the request and the key."""

    org: str
    env: Env
    agent: str
    holder: str | None
    asked_by: str
    to: str
    shown: str | None
    today: date
    # Whether an app holds the agent in this world right now: a fact of this process's live
    # memory, read by the door, so the verb judges it without reaching into the gateway.
    held: bool


@dataclass(frozen=True)
class Placed:
    """A call on its way: its id, the number it rings, and the number the far end sees."""

    call: str
    to: str
    shown: str


# Every store placing a call reads or writes, handed in as the door got them: the call is the
# same whichever process asks, and none of them is this module's to open.
@dataclass(frozen=True)
class Placers:
    """The stores a dial is judged and written against."""

    routes: Routes
    trunks: OutboundTrunks
    guards: Guards
    admission: Admission
    logs: Logs
    dispatches: Dispatches


async def place_call(placing: Placing, running: int, placers: Placers) -> Placed:
    """Place it: shown as one of the agent's numbers, through the org's trunk, past every guard."""
    doors = await _phone_doors(placers.routes, placing)
    shown = _shown_as(placing, doors)
    trunk = await placers.trunks.of(placing.org)
    if trunk is None:
        raise NoTrunk(NO_TRUNK)
    # A call this box places is answered by whatever holds the agent in that world, and in
    # production a person's key holds nothing — the box's own process does. So the question is
    # not whose key this is but whether anybody is there: a dialled call whose tools go out to
    # nobody is a stranger's phone ringing for a conversation that cannot happen. In the sandbox
    # `holder` is the person's own corner, so a developer's dial reaches their own copy.
    if not placing.held:
        raise NobodyHolding(NOBODY_HOLDING.format(slug=placing.agent, env=placing.env))
    call = new_call_id()
    asking = Asking(
        org=placing.org,
        env=placing.env,
        agent=placing.agent,
        to=placing.to,
        asked_by=placing.asked_by,
        call=call,
        shown=shown,
    )
    allowed = await placers.guards.judged(asking)
    await placers.admission.a_call(placing.org, placing.agent, running)
    context = CallContext(
        call=call,
        channel="phone",
        direction="outbound",
        # The far end is the contact: it is who the call is with, and what memory files it under.
        caller=allowed.destination.number,
        route=next(door for door in doors if door.number == shown),
        today=placing.today,
        holder=placing.holder,
    )
    await _opened(placers.logs, context, placing.agent, shown, placing.asked_by)
    dialling = Dialling(
        trunk=trunk.trunk_id,
        to=allowed.destination.number,
        shown=shown,
        max_duration_s=allowed.policy.max_duration_s,
    )
    await _started(placers.dispatches, placers.logs, context, placing.agent, dialling)
    return Placed(call=call, to=allowed.destination.number, shown=shown)


async def _phone_doors(routes: Routes, placing: Placing) -> list[Route]:
    """Every phone door this agent answers in its world: what a call back may be shown as."""
    doors = await routes.of_org(placing.org, placing.env)
    mine = [door for door in doors if door.agent == placing.agent and door.channel == "phone"]
    if not mine:
        raise NoPhoneDoor(NO_PHONE_DOOR.format(slug=placing.agent, env=placing.env))
    return mine


def _shown_as(placing: Placing, doors: list[Route]) -> str:
    """Which of the org's numbers the far end sees: one this agent answers, or its first."""
    theirs = [door.number for door in doors if door.number is not None]
    if placing.shown is None:
        return theirs[0]
    wanted = parse_e164(placing.shown)
    if wanted not in theirs:
        raise NotOurNumber(
            NOT_OUR_NUMBER.format(number=wanted, slug=placing.agent, env=placing.env)
        )
    return wanted


# The log opens HERE and not in the worker, because this is where both numbers and the name of
# whoever asked are known, and because the door hands back a call id a console starts reading at
# once. POST /v1/calls writes no second first entry for an outbound call.
async def _opened(logs: Logs, context: CallContext, slug: str, shown: str, asked_by: str) -> None:
    """The call's head row, claimed for this corner, and call.dialing on top of it."""
    await logs.owned(context.call, slug, context.route.org, context.env, context.holder)
    type, event = arrival_entry(context, shown, asked_by)
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
    except Exception as refused:
        await _never_rang(logs, context, slug)
        raise DidNotDial(DID_NOT_DIAL.format(why=refused)) from refused


async def _never_rang(logs: Logs, context: CallContext, slug: str) -> None:
    """call.ended with dial_failed, and the log sealed: nothing else will be written to it."""
    log = logs.writing(context.call, slug)
    ended = CallEnded(
        reason="dial_failed", ended_by="platform", ended_at=time.time(), duration_s=0.0
    )
    await log.append("call.ended", encode(ended))
    await log.seal()
    logs.forget(context.call)
