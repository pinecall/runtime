"""Tests for the fleet doors: the heartbeat and the roster."""

from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import (
    Knocking,
    postgres,
)


@postgres
async def test_a_heartbeat_reaches_the_roster_of_its_fleet(knocking: Knocking) -> None:
    beat = HeartbeatRequest(
        fleet="pinecall-sandbox", worker="w1", active=1, max_jobs=4, load=0.2, draining=False
    )
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        existing = (await worker.post("/v1/fleet/heartbeat", json=beat.written())).json()
        totals = (await worker.get("/v1/fleet/standing")).json()
    assert existing == {"cordoned": False, "full": False}
    assert (totals["fleet"], totals["workers"], totals["free"]) == ("pinecall-sandbox", 1, 3)


@postgres
async def test_a_tenants_key_opens_no_fleet_door(knocking: Knocking) -> None:
    beat = HeartbeatRequest(
        fleet="pinecall", worker="w1", active=0, max_jobs=None, load=0.1, draining=False
    )
    async with knocking.http(knocking.app["production"]) as tenant:
        refused = await tenant.post("/v1/fleet/heartbeat", json=beat.written())
    assert refused.status_code == 403
