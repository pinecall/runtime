"""A contact's facts are one DEVELOPER's in development: three of them test the same numbers."""

import pytest

from pinecall.memory import PgvectorMemory
from pinecall.types import PRODUCTION, SANDBOX
from tests.memory.conftest import HUNG_UP

pytestmark = pytest.mark.postgres

ANA = "m_ana"
BETO = "m_beto"


async def test_two_developers_testing_the_same_number_do_not_read_each_others_facts(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    """The point of 0021: before it, the fact one of them planted arrived in the other's call."""
    await memory.hold(org, SANDBOX, ANA, contact, ["dice que prefiere la mañana"], at=HUNG_UP)
    await memory.hold(org, SANDBOX, BETO, contact, ["dice que prefiere la tarde"], at=HUNG_UP)

    anas = await memory.recall(org, SANDBOX, ANA, contact, "cuándo prefiere")
    betos = await memory.recall(org, SANDBOX, BETO, contact, "cuándo prefiere")

    assert [fact.text for fact in anas] == ["dice que prefiere la mañana"]
    assert [fact.text for fact in betos] == ["dice que prefiere la tarde"]


async def test_a_corner_with_no_facts_recalls_nothing_and_never_the_orgs(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    """Unlike a knowledge base there is no fallback: a fact is what a CALL learned, and there is
    no org-wide development call to inherit from."""
    await memory.hold(org, SANDBOX, None, contact, ["lo que aprendió CI"], at=HUNG_UP)

    assert await memory.recall(org, SANDBOX, ANA, contact, "qué sabe") == []


async def test_forgetting_a_contact_takes_your_corner_and_leaves_the_others(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    await memory.hold(org, SANDBOX, ANA, contact, ["lo de ana"], at=HUNG_UP)
    await memory.hold(org, SANDBOX, BETO, contact, ["lo de beto"], at=HUNG_UP)

    assert await memory.forget(org, SANDBOX, ANA, contact) == 1

    assert await memory.recall(org, SANDBOX, ANA, contact, "lo") == []
    assert len(await memory.recall(org, SANDBOX, BETO, contact, "lo")) == 1


async def test_the_quota_counts_every_corner_because_the_rows_are_the_orgs(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    await memory.hold(org, SANDBOX, ANA, contact, ["uno"], at=HUNG_UP)
    await memory.hold(org, SANDBOX, BETO, contact, ["dos"], at=HUNG_UP)
    await memory.hold(org, PRODUCTION, None, contact, ["tres"], at=HUNG_UP)

    assert await memory.kept(org) == 3


async def test_the_history_of_a_contact_is_the_asking_corners(
    memory: PgvectorMemory, org: str, contact: str
) -> None:
    await memory.hold(org, SANDBOX, ANA, contact, ["lo de ana"], at=HUNG_UP)
    await memory.hold(org, SANDBOX, BETO, contact, ["lo de beto"], at=HUNG_UP)

    kept = await memory.history(org, SANDBOX, ANA, contact)

    assert [fact.text for fact in kept] == ["lo de ana"]
