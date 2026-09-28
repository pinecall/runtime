"""Tests for the org's own settings: judging."""

from tests.conftest import (
    Knocking,
    postgres,
)


@postgres
async def test_judging_is_turned_off_for_the_org(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        before = (await tenant.get("/v1/org/judging")).json()
        after = (await tenant.put("/v1/org/judging", json={"on": False})).json()
    assert (before["on"], after["on"]) == (True, False)
