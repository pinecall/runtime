"""The trunk an org places calls through: whether it can dial yet, and the steps that make it."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.deps import KeptCarriersDep, NumbersKeyDep, RoutesDep, SettingsDep, TwilioDep
from pinecall.api.telephony.deps import GuardsDep, KeptOutboundTrunksDep, OutboundDep
from pinecall.api.telephony.numbers import NO_CARRIER
from pinecall.orgs.outbound import OutboundTrunks
from pinecall.routes.answering import own_numbers
from pinecall.routes.outbound import NO_LIVEKIT, TRUNK_NAME, Outbound, Placing
from pinecall.routes.twilio import (
    CARRIER_TRUNK,
    TwilioApi,
    TwilioFor,
    termination_host,
    termination_label,
)
from pinecall.types import Carrier, OutboundTrunk, SipPeer, TwilioAccount
from pinecall_protocol.rest import CarrierOutbound, DialGuards, OutboundProvisioned

router = APIRouter()

DRY_RUN = Query(False, description="print the plan and write nothing")

# The password the box authenticates to the carrier with. Minted here, kept sealed, and never
# shown again by either side: Twilio does not read a credential's password back and neither does
# LiveKit read a trunk's. Twenty-four random bytes, thirty-two url-safe characters, is far past
# anything a registrar will guess.
PASSWORD_BYTES = 24

NO_NUMBERS = (
    "this org has imported no phone number: a call back is shown as one of the org's own numbers,"
    " so POST /v1/numbers first"
)
NO_OUTBOUND_HOST = (
    "this org's SIP peer declares no outbound host: PUT /v1/carrier again with outbound_host — the"
    " networks a peer sends calls FROM are not an address it accepts one AT, and guessing is how a"
    " box ends up dialling a stranger"
)
NO_TRUNK_YET = "no outbound trunk has been provisioned: POST /v1/carrier/outbound"
CARRIER_CHANGED = (
    "the outbound trunk was provisioned for a {was} carrier and this org's carrier is {now} now:"
    " POST /v1/carrier/outbound again"
)
NOT_ON_THE_SFU = (
    "the outbound trunk was provisioned and the media plane no longer has it: the gateway rebuilds"
    " it when it starts, or POST /v1/carrier/outbound again"
)
CREDENTIALS_LOST = (
    "credential list {name} already stands on account {account} and this box no longer holds its"
    " password: delete that credential list in Twilio's console and run this again"
)


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
    missing = _what_is_missing(carrier, numbers, kept, sfu, standing)
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
    """The org's outbound trunk, made once and repaired after: the plan, then the writes."""
    carrier = await carriers.of(key.org)
    if carrier is None:
        raise HTTPException(404, NO_CARRIER)
    numbers = await own_numbers(table, key.org)
    if not numbers:
        raise HTTPException(409, NO_NUMBERS)
    if sfu is None:
        raise HTTPException(503, NO_LIVEKIT)
    steps: list[str] = []
    placing = await _reached(carrier, settings.fleet, twilio, trunks, numbers, steps, dry_run)
    on_the_sfu = TRUNK_NAME.format(fleet=settings.fleet, org=carrier.org)
    steps.append(
        f"livekit  outbound trunk {on_the_sfu} → {placing.address} over "
        f"{placing.transport}, showing {len(placing.numbers)} of this org's numbers"
        f"{' with SIP auth' if placing.auth else ''}"
    )
    if dry_run:
        return OutboundProvisioned(steps=steps, dry_run=True, ready=False)
    trunk_id = await sfu.provisioned(carrier.org, placing)
    kept = OutboundTrunk(
        org=carrier.org,
        kind=carrier.kind,
        trunk_id=trunk_id,
        address=placing.address,
        username=None if placing.auth is None else placing.auth[0],
        password=None if placing.auth is None else placing.auth[1],
    )
    await trunks.put(kept)
    steps.append(f"kept     {trunk_id} is this org's outbound trunk")
    return OutboundProvisioned(
        steps=steps, dry_run=False, ready=True, trunk=trunk_id, address=placing.address
    )


# `standing` is the SFU's own answer for the org's trunk, asked because the row alone lied once:
# a media plane that lost its trunks read `ready` for a week nobody could dial through.
def _what_is_missing(
    carrier: Carrier | None,
    numbers: tuple[str, ...],
    kept: OutboundTrunk | None,
    sfu: Outbound | None,
    standing: str | None = None,
) -> list[str]:
    """One sentence per thing still to do, in the order somebody would do them."""
    missing: list[str] = []
    if carrier is None:
        missing.append(NO_CARRIER)
    elif isinstance(carrier.account, SipPeer) and carrier.account.dialled_at is None:
        missing.append(NO_OUTBOUND_HOST)
    if not numbers:
        missing.append(NO_NUMBERS)
    if sfu is None:
        missing.append(NO_LIVEKIT)
    if kept is None:
        missing.append(NO_TRUNK_YET)
    elif carrier is not None and kept.kind != carrier.kind:
        missing.append(CARRIER_CHANGED.format(was=kept.kind, now=carrier.kind))
    elif sfu is not None and standing is None:
        missing.append(NOT_ON_THE_SFU)
    return missing


async def _reached(
    carrier: Carrier,
    fleet: str,
    twilio: TwilioFor,
    trunks: OutboundTrunks,
    numbers: tuple[str, ...],
    steps: list[str],
    dry: bool,
) -> Placing:
    """Where this org's calls go out, by the kind of carrier it brought."""
    if isinstance(carrier.account, TwilioAccount):
        account = carrier.account
        return await _through_twilio(carrier, account, fleet, twilio, trunks, numbers, steps, dry)
    return _through_the_peer(carrier.account, numbers, steps)


# A SIP peer is the tenant's own equipment and this box provisions nothing on it: what it takes is
# the address the tenant declared and the credentials it already registers with. So the plan has
# one step, and the refusal for a peer that declared no address is the read door's own sentence.
def _through_the_peer(peer: SipPeer, numbers: tuple[str, ...], steps: list[str]) -> Placing:
    """The peer, as the tenant declared it: nothing is created on somebody else's switch."""
    host = peer.dialled_at
    if host is None:
        raise HTTPException(409, NO_OUTBOUND_HOST)
    username, _ = peer.dialled_as
    steps.append(f"peer     {host} over {peer.outbound_transport}, authenticating as {username}")
    return Placing(
        address=host, numbers=numbers, transport=peer.outbound_transport, auth=peer.dialled_as
    )


# Twilio's outbound half, and it is a different object from the inbound one: origination is where
# Twilio sends a call that ARRIVES, termination is where the box sends one it PLACES. The trunk
# carries both, which is why this looks up the very trunk the import made.
async def _through_twilio(
    carrier: Carrier,
    account: TwilioAccount,
    fleet: str,
    twilio: TwilioFor,
    trunks: OutboundTrunks,
    numbers: tuple[str, ...],
    steps: list[str],
    dry: bool,
) -> Placing:
    """The trunk's termination label and a credential list on it, each looked up before made."""
    api = twilio(account)
    name = CARRIER_TRUNK.format(fleet=fleet, org=carrier.org)
    label = termination_label(fleet, carrier.org)
    trunk = await api.trunk_named(name)
    if trunk is None:
        steps.append(f"trunk    {name} — created on account {account.account_sid}")
        if not dry:
            trunk = await api.create_trunk(name)
    else:
        steps.append(f"trunk    {trunk.sid} {name} — standing")
    if trunk is not None and trunk.domain == label:
        steps.append(f"terminal {termination_host(label)} — standing")
    else:
        steps.append(f"terminal {termination_host(label)} — set")
        if not dry and trunk is not None:
            await api.terminating(trunk.sid, label)
    auth = await _credentials(api, carrier, trunks, name, trunk, steps, dry)
    return Placing(address=termination_host(label), numbers=numbers, auth=auth)


async def _credentials(
    api: TwilioApi,
    carrier: Carrier,
    trunks: OutboundTrunks,
    name: str,
    trunk: Any,
    steps: list[str],
    dry: bool,
) -> tuple[str, str] | None:
    """The one credential list this box dials as, made once and remembered: Twilio forgets."""
    kept = await trunks.of(carrier.org)
    standing = await api.credential_list_named(name)
    username = name
    if standing is not None and kept is not None and kept.password is not None:
        steps.append(f"login    {standing} {name} — standing")
        password = kept.password
    elif standing is not None:
        # The list is on the account and this box cannot read its password back from anywhere. A
        # second list would leave two logins nobody can tell apart, so this stops and says so.
        raise HTTPException(409, CREDENTIALS_LOST.format(name=name, account=carrier.named))
    else:
        password = secrets.token_urlsafe(PASSWORD_BYTES)
        steps.append(f"login    {name} — created, its password kept under the vault key")
        if not dry:
            standing = await api.create_credential_list(name, username, password)
    if dry or standing is None or trunk is None:
        return (username, password)
    on_it = await api.credential_lists_on(trunk.sid)
    if standing in on_it:
        steps.append(f"trunked  {standing} — on the trunk already")
    else:
        steps.append(f"trunked  {standing} — attached to the trunk")
        await api.with_credentials(trunk.sid, standing)
    return (username, password)
