"""The fleet's doors: a worker's heartbeat in, the operator's roster out, and the numbers left."""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter

from pinecall.api._deps import AppKeyDep, CallsKeyDep, FleetDep, LogsDep, StoreDep, an_operator
from pinecall.fleet import STALE_AFTER_S, Heartbeat, Seat, Standing, Totals
from pinecall.log.store import DEFAULT_LIMIT
from pinecall.types import DEFAULT_ORG, Channel
from pinecall_protocol import WireModel, encode
from pinecall_protocol.events import CallbackRequested

router = APIRouter()
operator = APIRouter(prefix="/v1/ops/fleet", dependencies=[Depends(an_operator)])

BEAT: TypeAdapter[Heartbeat] = TypeAdapter(Heartbeat)
STANDING: TypeAdapter[Standing] = TypeAdapter(Standing)
SEATS: TypeAdapter[tuple[Seat, ...]] = TypeAdapter(tuple[Seat, ...])
TOTALS: TypeAdapter[Totals] = TypeAdapter(Totals)

# The fleet knocks with the box's own org key — the one pinecall-worker-key.service mints for
# `default`, or the dev key, which IS org default. A tenant's key opens every door of its own
# org and none of the fleet's: a heartbeat it could post would be a seat it could invent.
NOT_THE_FLEETS_KEY = "the fleet's doors take the default org's key, which a worker holds"

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


# ── the worker's two doors ──────────────────────────────────────────────────────


@router.post("/v1/fleet/heartbeat")
async def heartbeat(said: Heartbeat, key: AppKeyDep, fleet: FleetDep) -> dict[str, Any]:
    """A worker says what it holds; the hub says whether it was cordoned and whether all is full."""
    _the_fleets_key(key.org)
    standing = fleet.report(said, time.time())
    dumped: dict[str, Any] = STANDING.dump_python(standing)
    return dumped


@router.get("/v1/fleet/standing")
async def standing(key: AppKeyDep, fleet: FleetDep) -> dict[str, Any]:
    """The fleet's numbers as the overflow agent reads them: full, or not."""
    _the_fleets_key(key.org)
    return _totals(fleet.totals(time.time()))


# ── the callbacks ───────────────────────────────────────────────────────────────


@router.post("/v1/callbacks", status_code=204)
async def callback_requested(
    said: WantedCallback, key: AppKeyDep, store: StoreDep, logs: LogsDep
) -> None:
    """A number to call back, onto the agent's log: the widget before a room, or the overflow."""
    if await store.owner(None, said.agent) != key.org:
        raise HTTPException(404, NOT_THIS_ORGS_AGENT.format(agent=said.agent))
    event = CallbackRequested(
        channel=said.channel, number=said.number, via=said.via, call=said.call, contact=None
    )
    await logs.writing_agent(said.agent).append(CALLBACK, encode(event))


@router.get("/v1/callbacks")
async def callbacks(
    key: CallsKeyDep, store: StoreDep, after: int = AFTER, agent: str | None = OF_AGENT
) -> dict[str, Any]:
    """Every request this org's agents took, oldest first, and where the next page starts."""
    page = await store.across([CALLBACK], after=after, limit=DEFAULT_LIMIT)
    ours = [
        one for one in page if one.org == key.org and (agent is None or one.entry.agent == agent)
    ]
    return {
        "requests": [
            {
                "position": one.position,
                "agent": one.entry.agent,
                "ts": one.entry.ts,
                **one.entry.data,
            }
            for one in ours
        ],
        "next": page[-1].position if len(page) == DEFAULT_LIMIT else None,
    }


# ── the operator's view ─────────────────────────────────────────────────────────


@operator.get("")
async def listed(fleet: FleetDep) -> dict[str, Any]:
    """Every worker heard from, and the fleet's totals over the ones heard from lately."""
    now = time.time()
    return {
        "now": now,
        # The hub's own threshold, in the answer: a page that dims a worker nobody has heard from
        # reads it here rather than keeping a second copy that drifts from fleet/roster.py.
        "stale_after_s": STALE_AFTER_S,
        "workers": list(SEATS.dump_python(fleet.seats(now))),
        "totals": _totals(fleet.totals(now)),
    }


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


# The two derived numbers ride beside the five counted ones: a reader of the wire should not have
# to know the rule that makes a fleet full.
def _totals(totals: Totals) -> dict[str, Any]:
    """The fleet's numbers as JSON, the derived `full` and `busy` included."""
    return {**TOTALS.dump_python(totals), "full": totals.full, "busy": totals.busy}


def _the_fleets_key(org: str) -> None:
    """403 for any org but the box's own: only the fleet writes the fleet's table."""
    if org != DEFAULT_ORG:
        raise HTTPException(403, NOT_THE_FLEETS_KEY)
