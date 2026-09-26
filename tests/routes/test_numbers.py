"""Every number an org answers at, both worlds, off the one table that holds a door."""

import pytest

from pinecall.routes import numbers
from pinecall.routes.records import MemoryRoutes
from pinecall.types import SANDBOX, Route

pytestmark = pytest.mark.unit

ORG = "clinica"


async def test_both_worlds_are_read_and_production_comes_first() -> None:
    """A number moves between the worlds and the carrier never notices: the trunk reads both."""
    table = MemoryRoutes(
        [
            Route(org=ORG, agent="tienda-sur", channel="phone", number="+59829000002", env=SANDBOX),
            Route(org=ORG, agent="clinica-norte", channel="phone", number="+59829000001"),
        ]
    )

    assert await numbers.own_numbers(table, ORG) == ("+59829000001", "+59829000002")


async def test_only_the_phone_answers_and_only_this_orgs() -> None:
    table = MemoryRoutes(
        [
            Route(org=ORG, agent="clinica-norte", channel="phone", number="+59829000001"),
            Route(org=ORG, agent="clinica-norte", channel="whatsapp", number="+59829000003"),
            Route(org="elsewhere", agent="tienda-sur", channel="phone", number="+59829000004"),
        ]
    )

    assert await numbers.own_numbers(table, ORG) == ("+59829000001",)


async def test_an_org_that_answers_nowhere_has_no_numbers() -> None:
    assert await numbers.own_numbers(MemoryRoutes(), ORG) == ()
