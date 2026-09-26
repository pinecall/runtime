"""The trunk an org places calls through: whether it can dial yet, and the steps that make it."""

from __future__ import annotations

from fastapi import APIRouter, Query

from pinecall.api.deps import KeptCarriersDep, NumbersKeyDep, RoutesDep, SettingsDep, TwilioDep
from pinecall.api.telephony.deps import GuardsDep, KeptOutboundTrunksDep, OutboundDep
from pinecall.routes.numbers import own_numbers
from pinecall.telephony.provisioning import provision_trunk, steps_missing
from pinecall_protocol.rest import CarrierOutbound, DialGuards, OutboundProvisioned

router = APIRouter()

DRY_RUN = Query(False, description="print the plan and write nothing")


@router.get("/v1/carrier/outbound")
async def outbound(
    key: NumbersKeyDep,
    carriers: KeptCarriersDep,
    trunks: KeptOutboundTrunksDep,
    table: RoutesDep,
    sfu: OutboundDep,
    guards: GuardsDep,
) -> CarrierOutbound:
    """Whether this org can place a call yet, what is missing, and the guards it dials under."""
    carrier = await carriers.of(key.org)
    numbers = await own_numbers(table, key.org)
    kept = await trunks.of(key.org)
    standing = None if sfu is None or kept is None else await sfu.standing(key.org)
    missing = steps_missing(carrier, numbers, kept, sfu, standing)
    policy = await guards.policy_of(key.org)
    return CarrierOutbound(
        ready=not missing,
        kind=None if carrier is None else carrier.kind,
        from_numbers=list(numbers),
        steps_missing=missing,
        guards=DialGuards(
            dial_anywhere=policy.dial_anywhere,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
            max_duration_s=policy.max_duration_s,
        ),
    )


# exclude_unset: a dry run names no trunk and no address, because nothing was made — the answer
# carries the two keys only once the writes happened, as it always has.
@router.post("/v1/carrier/outbound", response_model_exclude_unset=True)
async def provision(
    key: NumbersKeyDep,
    carriers: KeptCarriersDep,
    trunks: KeptOutboundTrunksDep,
    table: RoutesDep,
    sfu: OutboundDep,
    twilio: TwilioDep,
    settings: SettingsDep,
    dry_run: bool = DRY_RUN,
) -> OutboundProvisioned:
    """The org's outbound trunk, made once and repaired after (telephony/provisioning.py)."""
    done = await provision_trunk(
        key.org, settings.fleet, carriers, trunks, table, sfu, twilio, dry=dry_run
    )
    if dry_run:
        return OutboundProvisioned(steps=done.steps, dry_run=True, ready=False)
    return OutboundProvisioned(
        steps=done.steps, dry_run=False, ready=True, trunk=done.trunk, address=done.address
    )
