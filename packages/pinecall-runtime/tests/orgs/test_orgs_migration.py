"""0006 on a box that is already up: every fleet becomes an org, and nothing has to be reissued."""

from collections.abc import AsyncIterator

import pytest

from pinecall.auth.keys import fingerprint
from pinecall.auth.keys_postgres import PostgresKeys
from pinecall.log.store import open_pool
from pinecall.log.store.migrating import (
    apply_migrations,
)
from tests.orgs.boxes import Box, a_box_before
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

# What a box from before orgs held: the default fleet's key, a second fleet with a key, a number
# and a token, and one call already summarised in the log.
THE_FIRST_KEY = "pk_the_key_migrate_up_printed_last_week"
THE_CLINICS_KEY = "pk_the_clinic_next_door"
NUMBER = "+34910000000"
OLD_CALL = "CA_from_before_orgs"


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Five migrations applied by hand, then the rows a box in use would have had."""
    async with a_box_before(postgres, "0006", named="orgs") as box:
        await box.connection.execute(
            "insert into api_keys (id, hash, org, fleet, label) values ($1, $2, $3, $4, $5)",
            "k_1",
            fingerprint(THE_FIRST_KEY),
            "default",
            "default",
            "issued by migrate up",
        )
        await box.connection.execute(
            "insert into api_keys (id, hash, org, fleet, label) values ($1, $2, $3, $4, $5)",
            "k_2",
            fingerprint(THE_CLINICS_KEY),
            "default",
            "madrid",
            "the clinic",
        )
        await box.connection.execute(
            "insert into routes (fleet, number, agent, channel) values ($1, $2, $3, $4)",
            "madrid",
            NUMBER,
            "clinica-norte",
            "phone",
        )
        await box.connection.execute(
            "insert into tokens (call, org, fleet, agent, scope, expires_at)"
            " values ($1, $2, $3, $4, $5, now())",
            "CA_minted",
            "default",
            "madrid",
            "clinica-norte",
            "talk",
        )
        await box.connection.execute(
            "insert into call_log_head (log, agent, call, seq) values ($1, $2, $1, 1)",
            OLD_CALL,
            "clinica-norte",
        )
        await box.connection.execute(
            "insert into call_log (call, seq, ts, agent, type, ephemeral, data)"
            " values ($1, 1, 1.0, $2, 'call.summary', false, '{\"duration_s\": 60}'::jsonb)",
            OLD_CALL,
            "clinica-norte",
        )
        yield box


async def test_every_fleet_the_box_knew_becomes_an_org_and_its_rows_follow_it(
    a_box_from_before: Box,
) -> None:
    """Criterion 4, the second half: a fleet in use migrates with no manual step and no reissue."""
    box = a_box_from_before
    # Every migration this box has not seen, and 0006 is the first of them.
    assert (await apply_migrations(box.dsn, schema=box.schema)).applied[0] == "0006_orgs.sql"
    read = box.connection
    orgs = {row["id"]: row["slug"] for row in await read.fetch("select id, slug from orgs")}
    assert orgs == {"default": "default", "madrid": "madrid"}
    keys = {row["id"]: row["org"] for row in await read.fetch("select id, org from api_keys")}
    assert keys == {"k_1": "default", "k_2": "madrid"}
    routes = [dict(row) for row in await read.fetch("select org, number, agent from routes")]
    assert routes == [{"org": "madrid", "number": NUMBER, "agent": "clinica-norte"}]
    assert await read.fetchval("select org from tokens where call = 'CA_minted'") == "madrid"


async def test_a_key_issued_before_the_migration_still_opens_its_orgs_doors(
    a_box_from_before: Box,
) -> None:
    """The worker on the box keeps the key in its unit file and knocks as the org it was."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    pool = await open_pool(box.dsn, schema=box.schema)
    try:
        keys = PostgresKeys(pool)
        assert (await keys.verify(THE_FIRST_KEY)).org == "default"  # type: ignore[union-attr]
        assert (await keys.verify(THE_CLINICS_KEY)).org == "madrid"  # type: ignore[union-attr]
    finally:
        await pool.close()


async def test_the_logs_written_before_are_the_default_orgs_and_gain_a_position(
    a_box_from_before: Box,
) -> None:
    """A box had one tenant by construction; its history is read by that tenant's key."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    read = box.connection
    assert (
        await read.fetchval("select org from call_log_head where log = $1", OLD_CALL) == "default"
    )
    assert await read.fetchval("select position from call_log where call = $1", OLD_CALL) == 1


async def test_no_table_carries_a_fleet_column_any_more(a_box_from_before: Box) -> None:
    """One word for the tenant: the column that carried the other one is gone from every table."""
    box = a_box_from_before
    await apply_migrations(box.dsn, schema=box.schema)
    left = await box.connection.fetch(
        "select table_name from information_schema.columns"
        " where table_schema = $1 and column_name = 'fleet'",
        box.schema,
    )
    assert [row["table_name"] for row in left] == []
