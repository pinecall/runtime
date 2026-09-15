"""LiveKit's SIP side, per org: one inbound trunk with the org's numbers, and one dispatch rule."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from livekit import api

from pinecall._settings import Settings
from pinecall.types.dispatch import ORG_KEY, WORKER_NAME

# One inbound trunk per org and one rule on it, named so a person reading the SFU's lists knows
# whose they are. The trunk's `numbers` is the allow-list: an INVITE for a number no trunk declares
# is dropped with no SIP response at all (infra/box/sip.yaml, hide_inbound_port).
TRUNK_NAME = "pinecall-{org}"
RULE_NAME = "pinecall-{org}-one-room-per-caller"
ROOM_PREFIX = "call-"

NO_LIVEKIT = (
    "this gateway has no LIVEKIT_API_KEY and LIVEKIT_API_SECRET: it cannot admit a number on the"
    " media plane"
)


class Trunks(Protocol):
    """What the numbers door does on the media plane: let a number in, and stop letting it in."""

    async def admitted(
        self,
        org: str,
        number: str,
        allowed: Sequence[str],
        auth: tuple[str, str] | None,
    ) -> str:
        """The org's inbound trunk, made once, with this number on it and this fence; its id."""
        ...

    async def released(self, org: str, number: str) -> bool:
        """The number off the org's trunk. False when it was not on one."""
        ...


@dataclass
class _Admitted:
    trunk_id: str
    numbers: set[str] = field(default_factory=set[str])
    allowed: tuple[str, ...] = ()
    auth: tuple[str, str] | None = None


class MemoryTrunks:
    """The media plane of a clone with no LiveKit pair, and of every test: what would be there."""

    def __init__(self) -> None:
        self.trunks: dict[str, _Admitted] = {}

    async def admitted(
        self, org: str, number: str, allowed: Sequence[str], auth: tuple[str, str] | None
    ) -> str:
        trunk = self.trunks.setdefault(org, _Admitted(trunk_id=f"ST_{org}"))
        trunk.numbers.add(number)
        trunk.allowed = tuple(allowed)
        trunk.auth = auth
        return trunk.trunk_id

    async def released(self, org: str, number: str) -> bool:
        trunk = self.trunks.get(org)
        if trunk is None or number not in trunk.numbers:
            return False
        trunk.numbers.discard(number)
        return True


class LivekitTrunks:
    """The real SFU, over livekit-api: looked up by name before anything is made, never doubled."""

    def __init__(self, url: str, api_key: str, api_secret: str) -> None:
        self._url = url
        self._key = api_key
        self._secret = api_secret

    async def admitted(
        self, org: str, number: str, allowed: Sequence[str], auth: tuple[str, str] | None
    ) -> str:
        """Create the trunk when there is none; update its numbers and fence when there is."""
        async with api.LiveKitAPI(self._url, self._key, self._secret) as livekit:
            standing = await _trunk_named(livekit, TRUNK_NAME.format(org=org))
            if standing is None:
                made = await livekit.sip.create_inbound_trunk(
                    api.CreateSIPInboundTrunkRequest(
                        trunk=_a_trunk_info(org, [number], allowed, auth)
                    )
                )
                await _a_rule(livekit, org, made.sip_trunk_id)
                return made.sip_trunk_id
            numbers = sorted({*standing.numbers, number})
            await livekit.sip.update_inbound_trunk(
                standing.sip_trunk_id, _a_trunk_info(org, numbers, allowed, auth)
            )
            await _a_rule(livekit, org, standing.sip_trunk_id)
            return standing.sip_trunk_id

    async def released(self, org: str, number: str) -> bool:
        """The number off the trunk's allow-list; the trunk and the rule stay for the next one."""
        async with api.LiveKitAPI(self._url, self._key, self._secret) as livekit:
            standing = await _trunk_named(livekit, TRUNK_NAME.format(org=org))
            if standing is None or number not in standing.numbers:
                return False
            kept = [one for one in standing.numbers if one != number]
            await livekit.sip.update_inbound_trunk_fields(standing.sip_trunk_id, numbers=kept)
            return True


def _a_trunk_info(
    org: str, numbers: Sequence[str], allowed: Sequence[str], auth: tuple[str, str] | None
) -> api.SIPInboundTrunkInfo:
    """The trunk as LiveKit keeps it: the org's name, its numbers, its fence, its credentials."""
    info = api.SIPInboundTrunkInfo(
        name=TRUNK_NAME.format(org=org), numbers=list(numbers), allowed_addresses=list(allowed)
    )
    if auth is not None:
        info.auth_username, info.auth_password = auth
    return info


async def _trunk_named(livekit: api.LiveKitAPI, name: str) -> api.SIPInboundTrunkInfo | None:
    standing = await livekit.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
    return next((trunk for trunk in standing.items if trunk.name == name), None)


# The rule names the fleet's worker and never a tenant's agent: which agent answers a number is
# one row in the routes table, and moving a number is not a LiveKit change at all. It does name
# the ORG, because a tenant's trunk is one org's: the worker reads it off the dispatch and asks
# for that org's doors, the way a web token's dispatch names its org (tokens/room.py). The box's
# own trunk (infra/tools/twilio_trunk.py) names none, and a call on it is resolved by number.
async def _a_rule(livekit: api.LiveKitAPI, org: str, trunk_id: str) -> None:
    """One room per caller on this trunk, with the worker dispatched into it, made once."""
    standing = await livekit.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
    if any(rule.name == RULE_NAME.format(org=org) for rule in standing.items):
        return
    await livekit.sip.create_dispatch_rule(
        api.CreateSIPDispatchRuleRequest(
            name=RULE_NAME.format(org=org),
            trunk_ids=[trunk_id],
            rule=api.SIPDispatchRule(
                dispatch_rule_individual=api.SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
            ),
            room_config=api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=WORKER_NAME, metadata=json.dumps({ORG_KEY: org})
                    )
                ]
            ),
        )
    )


def trunks_for(settings: Settings) -> Trunks | None:
    """The SFU when the process has the LiveKit pair; None when it has none: the door says so."""
    if settings.livekit_api_key and settings.livekit_api_secret:
        return LivekitTrunks(
            settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
        )
    return None
