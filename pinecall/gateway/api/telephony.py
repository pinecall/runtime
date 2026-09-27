"""The org's numbers and the accounts they live in, dialling out, and the worker's leg."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from pinecall.channels import routes, telephony
from pinecall.channels.telephony import Asking, Import, Placing, Purchase
from pinecall.domain.errors import Conflict
from pinecall.domain.types import Corner, parse_e164, today_in
from pinecall.gateway.deps import Acting, CornerDep, NumbersKey, TalkKey, WiredDep, WorkerKey
from pinecall.tenancy import keys
from pinecall.wire.rest import (
    CallWanted,
    CarrierBrought,
    CarrierOutbound,
    CarriersBrought,
    DialGuards,
    Dialled,
    LegDialled,
    NumberAnswering,
    NumberMoved,
    NumberOwned,
    NumberRouted,
    NumbersAvailable,
    NumberWanted,
    OutboundProvisioned,
    PurchaseWanted,
)

router = APIRouter()

NOBODY_HOLDING = (
    "nobody holds agent {agent} in this corner: a phone would ring with no app to serve it"
)

# A person or a server acts in the org's own corner of its world: numbers are the org's.
AccountAsked = Annotated[str | None, Query()]
DryRun = Annotated[bool, Query()]


# ── the accounts ──


@router.get("/v1/carrier")
async def carrier(key: NumbersKey, box: WiredDep, account: AccountAsked = None) -> CarrierBrought:
    """The org's account, named, never its secret; the only one unless one is asked for."""
    held = await telephony.carrier(box.pool, box.vault, key.org, account)
    return CarrierBrought(kind=held.account.kind, account=held.id, label=held.account.label)


@router.get("/v1/carriers")
async def carriers(key: NumbersKey, box: WiredDep) -> CarriersBrought:
    """Every account of the org, oldest first."""
    held = await telephony.carriers_of(box.pool, box.vault, key.org)
    return CarriersBrought(
        carriers=[
            CarrierBrought(kind=one.account.kind, account=one.id, label=one.account.label)
            for one in held
        ]
    )


# Bringing an account the org already holds replaces its secret; a Twilio pair is tried first.
@router.put("/v1/carrier")
async def bring(said: telephony.Account, key: NumbersKey, box: WiredDep) -> CarrierBrought:
    """Keep an account of the org: a Twilio account, a SIP peer, a WhatsApp number at Meta."""
    held = await telephony.bring(box.exchange, key.org, said)
    return CarrierBrought(kind=held.account.kind, account=held.id, label=held.account.label)


@router.delete("/v1/carrier", status_code=204)
async def take_back(key: NumbersKey, box: WiredDep, account: AccountAsked = None) -> None:
    """Forget an account; its numbers stay routed until each is let go."""
    await telephony.take_back(box.pool, box.vault, key.org, account)


# ── the numbers ──


@router.get("/v1/numbers")
async def numbers(key: NumbersKey, box: WiredDep) -> list[NumberAnswering]:
    """The org's numbers in the key's world, and the agent each reaches."""
    return [NumberAnswering(route=one) for one in await routes.of_org(box.pool, key.org, key.env)]


@router.get("/v1/numbers/available")
async def available(
    key: NumbersKey, box: WiredDep, account: AccountAsked = None
) -> NumbersAvailable:
    """What the org's Twilio accounts own, and which of it this world imported."""
    kind, owned = await telephony.available(box.exchange, _orgs(key), account)
    return NumbersAvailable(
        kind=kind,
        numbers=[
            NumberOwned(
                number=one.number, name=one.name, imported=one.imported, account=one.account
            )
            for one in owned
        ],
    )


@router.post("/v1/numbers")
async def import_number(
    said: NumberWanted, key: NumbersKey, box: WiredDep, *, dry_run: DryRun = False
) -> NumberRouted:
    """Hook a number at its account, admit it on the SFU in the key's world, route it."""
    wanted = Import(
        corner=_orgs(key),
        agent=said.agent,
        number=said.number,
        channel=said.channel,
        account=said.account,
        hooked=said.hooked,
        networks=tuple(said.networks),
        move=said.move,
    )
    plan = (
        await telephony.plan_import(box.exchange, wanted)
        if dry_run
        else await telephony.import_number(box.exchange, wanted)
    )
    return NumberRouted(route=plan.route, steps=plan.steps, dry_run=plan.dry_run)


@router.post("/v1/numbers/buy")
async def buy(
    said: PurchaseWanted, key: NumbersKey, box: WiredDep, *, dry_run: DryRun = False
) -> NumberRouted:
    """Buy a number on the box's account and hook it, counted against the world's stock."""
    wanted = Purchase(
        corner=_orgs(key),
        agent=said.agent,
        country=said.country,
        area_code=said.area_code,
        channel=said.channel,
    )
    plan = (
        await telephony.plan_buy(box.exchange, wanted)
        if dry_run
        else await telephony.buy_number(box.exchange, wanted)
    )
    return NumberRouted(route=plan.route, steps=plan.steps, dry_run=plan.dry_run)


@router.delete("/v1/numbers/{number}", status_code=204)
async def release(number: str, key: NumbersKey, box: WiredDep) -> None:
    """Let the number go: its route and its admission; the account keeps it."""
    await telephony.release(box.exchange, key.org, parse_e164(number), key.env)


@router.put("/v1/numbers/{number}/env")
async def move(number: str, said: NumberMoved, key: NumbersKey, box: WiredDep) -> NumberAnswering:
    """Move the number into the other world: its row and the two rules."""
    moved = await telephony.move(box.exchange, key.org, parse_e164(number), said.env)
    return NumberAnswering(route=moved)


# ── dialling out ──


@router.get("/v1/carrier/outbound")
async def outbound(key: NumbersKey, box: WiredDep, account: AccountAsked = None) -> CarrierOutbound:
    """Whether the org can place a call, one sentence per thing missing, and its guards."""
    standing = await telephony.standing(box.exchange, _orgs(key), account)
    return CarrierOutbound(
        ready=standing.ready,
        kind=standing.kind,
        from_numbers=standing.from_numbers,
        steps_missing=standing.steps_missing,
        guards=DialGuards.model_validate(standing.guards.model_dump()),
    )


@router.post("/v1/carrier/outbound", response_model_exclude_unset=True)
async def provision(
    key: NumbersKey, box: WiredDep, *, account: AccountAsked = None, dry_run: DryRun = False
) -> OutboundProvisioned:
    """Make an account dialable: Twilio's termination and a credential; a peer needs nothing."""
    done = (
        await telephony.plan_outbound(box.exchange, key.org, account)
        if dry_run
        else await telephony.provision_outbound(box.exchange, key.org, account)
    )
    if done.dry_run:
        return OutboundProvisioned(steps=done.steps, dry_run=True, ready=False)
    return OutboundProvisioned(
        steps=done.steps, dry_run=False, ready=done.ready, trunk=done.trunk, address=done.address
    )


@router.post("/v1/agents/{slug}/dial", status_code=202)
async def dial(
    slug: str, said: CallWanted, key: TalkKey, where: CornerDep, box: WiredDep
) -> Dialled:
    """Place a call as the agent, after its guards: the call it became, before anything rings."""
    if box.registry.serving(where, slug, None) is None:
        raise Conflict(NOBODY_HOLDING.format(agent=slug))
    placing = Placing(
        corner=where,
        agent=slug,
        to=said.to,
        shown=said.from_,
        asked_by=_who(key),
        today=today_in(box.settings.timezone),
    )
    placed = await telephony.place(
        box.exchange, box.logs, placing, running=box.live.running(where.org)
    )
    return Dialled.model_validate(
        {
            "call": placed.call,
            "agent": slug,
            "to": placed.to,
            "from": placed.shown,
            "env": where.env,
            "log_token": keys.log_token(box.signer, placed.call, said.log),
        }
    )


class LegAsked(BaseModel):
    """A worker asking how to dial a leg of a call: to whom, from which of the org's numbers."""

    model_config = ConfigDict(populate_by_name=True)

    to: str
    call: str
    shown: str | None = Field(None, alias="from")


# The worker's, for a leg into a live call and for the leg a dial placed.
@router.get("/v1/agents/{slug}/outbound-trunk")
async def leg(
    slug: str,
    key: WorkerKey,
    where: CornerDep,
    box: WiredDep,
    asked: Annotated[LegAsked, Query()],
) -> LegDialled:
    """The leg's trunk inline, after the shape and the pace; a dial's own first leg passes."""
    asking = Asking(where, slug, asked.to, asked.shown, _who(key), asked.call)
    return LegDialled(trunk=await telephony.leg_through(box.exchange, asking))


def _orgs(key: Acting) -> Corner:
    return Corner(key.org, key.env)


# The one record of who placed a call, which is what audits dial spend.
def _who(key: Acting) -> str:
    member = key.bearer.member
    return member.id if member is not None else key.bearer.key.key_id
