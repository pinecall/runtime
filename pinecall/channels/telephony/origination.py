"""Where the Twilio trunks send a world's calls: moved from the world's name to its SIP name."""

import contextlib
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


# Once, when a box's SIP moves off its names (a cluster: the names are the HTTPS load balancer's):
# the runtime never touches a carrier on its own, so the operator asks for it (operator-api.md).
async def repointed(connections: Connections) -> list[Repointed]:
    """Each trunk of the box's and the orgs' Twilio accounts moved from a world's name to SIP's."""
    moves = _moves(connections)
    if not moves:
        return []
    done: list[Repointed] = []
    for account in await _twilio_accounts(connections):
        twilio = twilio_of(connections.http, account)
        for world, (was, now) in moves.items():
            for trunk in await twilio.trunks_pointing_at(was):
                await twilio.repointed(trunk.sid, was, now)
                done.append(Repointed(account.account_sid, trunk.sid, world, was, now))
    return done


def _moves(connections: Connections) -> dict[Env, tuple[str, str]]:
    settings = connections.settings
    moves: dict[Env, tuple[str, str]] = {}
    for world in ENVS:
        name, sip = settings.name_of(world), settings.sip_name_of(world)
        if name and sip and sip != name:
            moves[world] = (origination_uri(name), origination_uri(sip))
    return moves


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
