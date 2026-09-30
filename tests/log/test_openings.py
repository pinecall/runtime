"""What a call is, kept at its open: read back whole, kept once, a config shared by its calls."""

from datetime import date

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext, Route
from pinecall.log import openings
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import create
from tests.conftest import postgres

pytestmark = postgres


def a_context(call: str, org: str) -> CallContext:
    return CallContext(
        call=call,
        channel="phone",
        direction="inbound",
        caller="+34600111222",
        route=Route(org=org, agent="recepcion", channel="phone", number="+34900000000"),
        today=date(2026, 9, 30),
    )


async def test_a_call_is_read_back_as_it_opened_and_a_second_keep_changes_nothing(
    pool: Pool,
) -> None:
    org = (await create(pool, "clinica-norte", "Clínica Norte")).id
    config = AgentConfig(slug="recepcion", language="es")
    await openings.kept(pool, org, a_context("CA_1", org), config)
    await openings.kept(pool, org, a_context("CA_1", org), AgentConfig(slug="otra"))
    kept = await openings.opening_of(pool, "CA_1")
    assert kept == openings.Opening(a_context("CA_1", org), config)
    assert await openings.opening_of(pool, "CA_nobody") is None


async def test_the_calls_of_one_config_keep_it_once(pool: Pool) -> None:
    org = (await create(pool, "clinica-norte", "Clínica Norte")).id
    config = AgentConfig(slug="recepcion")
    for n in range(3):
        await openings.kept(pool, org, a_context(f"CA_{n}", org), config)
    async with pool.connection() as connection:
        row = await (
            await connection.execute(
                "select count(*) as n from call_configs where org = %s", (org,)
            )
        ).fetchone()
    assert row is not None
    assert row["n"] == 1
