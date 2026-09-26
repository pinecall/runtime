"""The fleet's doors: a worker's heartbeat in, the operator's roster out, and the numbers left."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from pinecall.api._deps import AppKeyDep, CallsKeyDep, FleetDep, LogsDep, StoreDep
from pinecall.api._operator import an_operator
from pinecall.auth.keys import KeyRecord, is_the_fleets
from pinecall.fleet import STALE_AFTER_S, Heartbeat, Seat, Standing, Totals
from pinecall.log.store import DEFAULT_LIMIT
from pinecall.types import Channel
from pinecall_protocol import WireModel, encode
from pinecall_protocol.defs import Contact
from pinecall_protocol.events import CallbackRequested

router = APIRouter()
operator = APIRouter(prefix="/v1/ops/fleet", dependencies=[Depends(an_operator)])

# The fleet knocks with the key pinecall-worker-key@.service mints for the box's worker — issued
# into org default with the `fleet` scope. A tenant's key opens every door of its own org and
# none of the fleet's: a heartbeat it could post would be a seat it could invent. And a default
# org key WITHOUT the scope is a person's or a machine's of that org, not the box's worker.
NOT_THE_FLEETS_KEY = (
    "the fleet's doors take a key holding the fleet scope, which the box mints for its worker"
)

# `fleet cordon` on a name nobody has knocked with. 404 and not 200: a typo must never read as done.
NO_SUCH_WORKER = "no worker named {worker} has knocked at this gateway"

# A callback names an agent, and the agent must be this org's — the same rule as every door that
# writes into an agent's log. A slug another org holds is a 404: that it exists is not the
# asker's business.
NOT_THIS_ORGS_AGENT = "no agent {agent} in this org"

# The event the request is written as, into the agent's own log, which is the org's.
CALLBACK = "callback.requested"

AFTER = Query(0, ge=0, description="the cursor the last page ended at; 0 reads from the start")
OF_AGENT = Query(None, description="only this agent's requests; every agent's when absent")


class WantedCallback(WireModel):
    """What the widget's backend or the overflow agent sends: whom to call back, and why."""

    agent: str
    number: str
    channel: Channel = "web"
    # The wire's own closed set: a via that is neither is refused by the shape, as a 422.
    via: Literal["overflow", "widget"] = "widget"
    call: str | None = None


# The two derived numbers ride beside the five counted ones: a reader of the wire should not have
# to know the rule that makes a fleet full.
class FleetTotals(WireModel):
    """The fleet's numbers as the wire says them: the five counted, then `full` and `busy`."""

    workers: int
    active: int
    seats: int
    free: int
    accepting: int
    full: bool
    busy: float


class FleetListed(WireModel):
    """What GET /v1/ops/fleet says: the clock, the staleness line, every seat, and the totals."""

    now: float
    stale_after_s: float
    workers: list[Seat]
    totals: FleetTotals


# One row of the page: where it sits in the log, whose agent took it, when, then the event's own
# fields as CallbackRequested writes them — `when` and `note` only when the writer said them.
class CallbackTaken(WireModel):
    """One callback request as the org reads it back off its agents' logs."""

    position: int
    agent: str
    ts: float
    channel: Channel
    number: str
    via: Literal["overflow", "widget", "agent"]
    call: str | None
    when: str | None = None
    note: str | None = None
    contact: Contact | None


class CallbacksPage(WireModel):
    """What GET /v1/callbacks says: the requests, oldest first, and where the next page starts."""

    requests: list[CallbackTaken]
    next: int | None


# ── the worker's two doors ──────────────────────────────────────────────────────


@router.post("/v1/fleet/heartbeat")
async def heartbeat(said: Heartbeat, key: AppKeyDep, fleet: FleetDep) -> Standing:
    """A worker says what it holds; the hub says whether it was cordoned and whether all is full."""
    _the_fleets_key(key)
    return fleet.report(said, time.time())


@router.get("/v1/fleet/standing")
async def standing(key: AppKeyDep, fleet: FleetDep) -> FleetTotals:
    """The fleet's numbers as the overflow agent reads them: full, or not."""
    _the_fleets_key(key)
    return _the_totals_said(fleet.totals(time.time()))


# ── the callbacks ───────────────────────────────────────────────────────────────


@router.post("/v1/callbacks", status_code=204)
async def callback_requested(
    said: WantedCallback, key: AppKeyDep, store: StoreDep, logs: LogsDep
) -> None:
    """A number to call back, onto the agent's log: the widget before a room, or the overflow."""
    # The overflow agent answers every org's callers on the fleet's key, so it may name any
    # agent that exists; a tenant's widget backend may only name its own.
    owner = await store.owner(None, said.agent)
    if owner is None or (owner != key.org and not is_the_fleets(key)):
        raise HTTPException(404, NOT_THIS_ORGS_AGENT.format(agent=said.agent))
    event = CallbackRequested(
        channel=said.channel, number=said.number, via=said.via, call=said.call, contact=None
    )
    await logs.writing_agent(said.agent).append(CALLBACK, encode(event))


# exclude_unset: a request is answered with the fields its writer set, so a row that said no
# `when` and no `note` carries neither — the page says what the log says, not what it could.
@router.get("/v1/callbacks", response_model_exclude_unset=True)
async def callbacks(
    key: CallsKeyDep, store: StoreDep, after: int = AFTER, agent: str | None = OF_AGENT
) -> CallbacksPage:
    """Every request this org's agents took, oldest first, and where the next page starts."""
    page = await store.across([CALLBACK], after=after, limit=DEFAULT_LIMIT)
    ours = [
        one for one in page if one.org == key.org and (agent is None or one.entry.agent == agent)
    ]
    return CallbacksPage(
        requests=[
            CallbackTaken(
                position=one.position, agent=one.entry.agent, ts=one.entry.ts, **one.entry.data
            )
            for one in ours
        ],
        next=page[-1].position if len(page) == DEFAULT_LIMIT else None,
    )


# ── the operator's view ─────────────────────────────────────────────────────────


@operator.get("")
async def listed(fleet: FleetDep) -> FleetListed:
    """Every worker heard from, and the fleet's totals over the ones heard from lately."""
    now = time.time()
    return FleetListed(
        now=now,
        # The hub's own threshold, in the answer: a page that dims a worker nobody has heard from
        # reads it here rather than keeping a second copy that drifts from fleet/roster.py.
        stale_after_s=STALE_AFTER_S,
        workers=list(fleet.seats(now)),
        totals=_the_totals_said(fleet.totals(now)),
    )


@operator.post("/{worker}/cordon", status_code=204)
async def cordon(worker: str, fleet: FleetDep) -> None:
    """The worker takes no new call, finishes what it holds, and leaves. Told on its next beat."""
    if not fleet.cordon(worker):
        raise HTTPException(404, NO_SUCH_WORKER.format(worker=worker))


@operator.delete("/{worker}/cordon", status_code=204)
async def uncordon(worker: str, fleet: FleetDep) -> None:
    """Take the cordon back, for a worker that has not left yet."""
    if not fleet.cordon(worker, cordoned=False):
        raise HTTPException(404, NO_SUCH_WORKER.format(worker=worker))


def _the_totals_said(totals: Totals) -> FleetTotals:
    """The fleet's numbers for the wire, the derived `full` and `busy` included."""
    return FleetTotals(**asdict(totals), full=totals.full, busy=totals.busy)


def _the_fleets_key(key: KeyRecord) -> None:
    """403 for any key but the box's worker's: only the fleet writes the fleet's table."""
    if not is_the_fleets(key):
        raise HTTPException(403, NOT_THE_FLEETS_KEY)
