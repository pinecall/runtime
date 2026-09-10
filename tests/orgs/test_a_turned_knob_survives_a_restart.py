"""The knobs an operator turned, in Postgres: a new process reads back what the last one wrote."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from pinecall.log.store import Pool, open_pool
from pinecall.orgs.table import PostgresOrgs
from pinecall.orgs.turned import KNOBS, PostgresTurned
from pinecall.providers.overrides import Overridden, Overrides
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

A_MODEL = "anthropic/claude-haiku-4-5"
AN_EAR = "soniox/stt-rt-preview"


@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """A tenant of this test's own: an overrides row is one per (org, agent) and outlives a test."""
    slug = f"org-{uuid4().hex[:12]}"
    created = await PostgresOrgs(pool).create(slug, slug)
    assert created is not None
    return created.id


def a_gateway_over(pool: Pool) -> Overrides:
    """One process's holder over the shared table: building a second one IS a restart."""
    return Overrides(PostgresTurned(pool))


async def test_a_gateway_that_starts_reads_what_the_last_one_turned(pool: Pool, org: str) -> None:
    agent = f"clinica-{uuid4().hex[:8]}"
    before = a_gateway_over(pool)
    await before.set(org, agent, Overridden(llm=A_MODEL, stt=AN_EAR))

    after = a_gateway_over(pool)
    await after.loaded()

    assert after.of(agent).llm == A_MODEL
    assert after.of(agent).stt == AN_EAR


async def test_a_knob_given_back_to_the_app_is_gone_after_a_restart_too(
    pool: Pool, org: str
) -> None:
    agent = f"clinica-{uuid4().hex[:8]}"
    turning = a_gateway_over(pool)
    await turning.set(org, agent, Overridden(llm=A_MODEL, stt=AN_EAR))
    # The door replaces the whole set, so leaving `stt` out is how an operator gives it back.
    await turning.set(org, agent, Overridden(llm=A_MODEL))

    after = a_gateway_over(pool)
    await after.loaded()

    assert after.of(agent).llm == A_MODEL
    assert after.of(agent).stt is None


async def test_an_agent_nobody_turned_a_knob_on_reads_as_untouched(pool: Pool) -> None:
    after = a_gateway_over(pool)
    await after.loaded()

    assert after.of("nadie") == Overridden()


async def test_every_knob_the_door_takes_has_a_column_to_land_in(pool: Pool, org: str) -> None:
    agent = f"clinica-{uuid4().hex[:8]}"
    whole = Overridden(
        llm=A_MODEL, stt=AN_EAR, voice="carolina", tts_model="eleven_flash_v2_5", greeting="Buenas."
    )
    await a_gateway_over(pool).set(org, agent, whole)

    after = a_gateway_over(pool)
    await after.loaded()

    assert after.of(agent) == whole
    assert set(KNOBS) == set(Overridden.model_fields)
