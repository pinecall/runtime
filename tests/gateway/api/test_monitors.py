"""Tests for the monitors doors: listed, set, dropped, and scoped to the evals key."""

from tests.conftest import Knocking, issued, postgres

MONITORS = "/v1/monitors"
SLOW = {"name": "slow answers", "metric": "e2e_median_s", "above": True, "threshold": 2.0}


@postgres
async def test_a_monitor_is_set_listed_with_no_firing_yet_and_dropped(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        empty = await org.get(MONITORS)
        kept = await org.post(MONITORS, json=SLOW)
        listed = await org.get(MONITORS)
        wrong = await org.post(MONITORS, json={**SLOW, "window_days": 3})
        dropped = await org.delete(f"{MONITORS}/{kept.json()['id']}")
        again = await org.delete(f"{MONITORS}/{kept.json()['id']}")
    assert empty.json() == {"monitors": []}
    assert kept.status_code == 201
    [row] = listed.json()["monitors"]
    assert (row["name"], row["metric"], row["window_days"], row["fired_on"]) == (
        "slow answers",
        "e2e_median_s",
        7,
        None,
    )
    assert wrong.status_code == 400
    assert (dropped.status_code, again.status_code) == (204, 404)


@postgres
async def test_a_key_without_the_evals_scope_sets_no_monitor(knocking: Knocking) -> None:
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reads) as reader:
        refused = await reader.post(MONITORS, json=SLOW)
    assert refused.status_code == 403
