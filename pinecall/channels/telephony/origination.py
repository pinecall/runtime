"""Where the Twilio trunks send a world's calls: to its SIP name, from its name or a former one."""

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pinecall.channels.telephony._twilio import origination_uri, twilio_of
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import ENVS, Env
from pinecall.process.connections import Connections
from pinecall.tenancy import orgs
from pinecall.tenancy.carriers import TwilioAccount, box_twilio, carriers_of


@dataclass(frozen=True)
class Repointed:
    """A trunk of an account that sent a world's calls to one URI and now sends them to another."""

    account: str
    trunk: str
    world: Env
    was: str
    now: str


# Once, when a box's SIP moves off its names (a cluster: the names are the HTTPS load balancer's) or
# a name is retired for another (`former`, the names a world was reached at before): the runtime
# never touches a carrier on its own, so the operator asks for it (operator-api.md).
async def repointed(
    connections: Connections, former: Mapping[Env, Sequence[str]] | None = None
) -> list[Repointed]:
    """Each trunk of the box's and the orgs' Twilio accounts moved to its world's SIP name."""
    moves = _moves(connections, former or {})
    if not moves:
        return []
    done: list[Repointed] = []
    for account in await _twilio_accounts(connections):
        twilio = twilio_of(connections.http, account)
        for world, was, now in moves:
            for trunk in await twilio.trunks_pointing_at(was):
                await twilio.repointed(trunk.sid, was, now)
                done.append(Repointed(account.account_sid, trunk.sid, world, was, now))
    return done


# From the world's own name when SIP has a name of its own, and from every former name given.
def _moves(
    connections: Connections, former: Mapping[Env, Sequence[str]]
) -> list[tuple[Env, str, str]]:
    settings = connections.settings
    moves: list[tuple[Env, str, str]] = []
    for world in ENVS:
        sip = settings.sip_name_of(world)
        if not sip:
            continue
        names = dict.fromkeys([settings.domain or "", *former.get(world, ())])
        moves.extend(_moved_from(world, [name for name in names if name and name != sip], sip))
    return moves


def _moved_from(world: Env, names: Sequence[str], sip: str) -> list[tuple[Env, str, str]]:
    return [(world, origination_uri(name), origination_uri(sip)) for name in names]


# The box's own account, then each org's, an account two orgs brought counted once.
async def _twilio_accounts(connections: Connections) -> list[TwilioAccount]:
    pool, vault = connections.pool, connections.vault
    found: dict[str, TwilioAccount] = {}
    # A box with no Twilio account of its own has only the orgs'.
    with contextlib.suppress(NotAvailable):
        boxs = await box_twilio(pool, vault)
        found[boxs.account_sid] = boxs
    for org in await orgs.listed(pool):
        for carrier in await carriers_of(pool, vault, org.id):
            if isinstance(carrier.account, TwilioAccount):
                found.setdefault(carrier.account.account_sid, carrier.account)
    return list(found.values())
