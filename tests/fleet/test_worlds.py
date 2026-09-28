"""The fleet of each world."""

import pytest
from pydantic import ValidationError

from pinecall.fleet.worlds import Fleets, fleet_of, fleets, set_fleets
from pinecall.postgres.pool import Pool
from tests.conftest import postgres


@postgres
async def test_each_world_has_its_fleet_and_the_operator_may_name_them(pool: Pool) -> None:
    assert fleet_of(await fleets(pool), "production") == "pinecall"
    assert fleet_of(await fleets(pool), "sandbox") == "pinecall-sandbox"
    await set_fleets(pool, Fleets(production="prod-eu", sandbox="test-eu"))
    assert fleet_of(await fleets(pool), "sandbox") == "test-eu"


def test_a_fleet_spelled_unlike_a_slug_is_refused() -> None:
    with pytest.raises(ValidationError):
        Fleets(sandbox="Pine call")
