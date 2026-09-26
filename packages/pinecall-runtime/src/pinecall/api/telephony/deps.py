"""What the doors that place a call are handed: the trunk, the SFU, the guards, the ledger."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall.api.deps import CallIndexDep, held
from pinecall.orgs.dial_policies import DialPolicies, Dials
from pinecall.orgs.outbound_credentials import OutboundTrunks
from pinecall.orgs.outbound_guards import Guards
from pinecall.orgs.vault import NO_VAULT_KEY
from pinecall.routes.dispatch import Dispatches
from pinecall.routes.outbound_trunks import Outbound


def get_outbound_trunks(connection: HTTPConnection) -> OutboundTrunks | None:
    """The trunks the org dials through, or None when this runtime was given no vault key."""
    trunks: OutboundTrunks | None = getattr(connection.app.state, "outbound_trunks", None)
    return trunks


def get_outbound(connection: HTTPConnection) -> Outbound | None:
    """The SFU's outbound side, or None when this process has no LiveKit pair to reach it with."""
    sfu: Outbound | None = getattr(connection.app.state, "outbound", None)
    return sfu


def get_dispatches(connection: HTTPConnection) -> Dispatches | None:
    """How a job is started on a call nobody rang; None with no LiveKit pair, as the door says."""
    dispatches: Dispatches | None = getattr(connection.app.state, "dispatches", None)
    return dispatches


def get_dial_policies(connection: HTTPConnection) -> DialPolicies:
    """What each org may dial: the table, or this process's memory on a clone with no Postgres."""
    return held(connection, "dial_policies")


def get_dials(connection: HTTPConnection) -> Dials:
    """The ledger of every dial asked for, which the rate guard counts from."""
    return held(connection, "dials")


# Built per request out of four things this gateway already holds, because the guards judge with
# the call index and the routes table and orgs/ may import neither: they are handed in, exactly as
# Admission is handed its Counting.
def get_guards(
    policies: Annotated[DialPolicies, Depends(get_dial_policies)],
    dials: Annotated[Dials, Depends(get_dials)],
    index: CallIndexDep,
) -> Guards:
    """Whether an org may dial a number right now, over this request's own tables."""
    return Guards(policies, dials, index.ever_reached)


# The password an outbound trunk dials with is one this box MINTED on the tenant's account and can
# read back from nowhere else, so it is a secret exactly as a carrier's credentials are.
async def kept_outbound_trunks(
    trunks: Annotated[OutboundTrunks | None, Depends(get_outbound_trunks)],
) -> OutboundTrunks:
    """The outbound trunks table, or 503: this box has no vault key."""
    if trunks is None:
        raise HTTPException(503, NO_VAULT_KEY)
    return trunks


OutboundDep = Annotated["Outbound | None", Depends(get_outbound)]
DispatchesDep = Annotated["Dispatches | None", Depends(get_dispatches)]
GuardsDep = Annotated[Guards, Depends(get_guards)]
DialPoliciesDep = Annotated[DialPolicies, Depends(get_dial_policies)]
KeptOutboundTrunksDep = Annotated[OutboundTrunks, Depends(kept_outbound_trunks)]
