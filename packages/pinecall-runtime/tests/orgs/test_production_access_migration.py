"""0039 on a box with people: no row opens production, and a person's keys lose their world."""

from collections.abc import AsyncIterator

import pytest

from pinecall.log.store.migrating import (
    apply_migrations,
)
from tests.orgs.boxes import Box, a_box_before
from tests.postgres import Dev

pytestmark = pytest.mark.postgres

THE_ORG = "clinica"


# Four keys as a box held them before: a person's in each world, an operator's visit, a server's.
KEYS = (
    ("k_ana_prod", "m_ana", "production"),
    ("k_ana_sandbox", "m_ana", "sandbox"),
    ("k_visit", "operator:berna@pinecall.io", "production"),
    ("k_server", None, "production"),
)


@pytest.fixture
async def a_box_from_before(postgres: Dev) -> AsyncIterator[Box]:
    """Every migration up to 0038 applied by hand, an admin and a developer, and four keys."""
    async with a_box_before(postgres, "0039", named="access") as box:
        await box.connection.execute(
            "insert into orgs (id, slug, name) values ($1, $1, $1)", THE_ORG
        )
        for id, role in (("m_ana", "admin"), ("m_bruno", "developer")):
            await box.connection.execute(
                "insert into members (id, org, email, name, role, agents, status) "
                "values ($1, $2, $1 || '@x.test', $1, $3, '{}', 'active')",
                id,
                THE_ORG,
                role,
            )
        for id, subject, env in KEYS:
            await box.connection.execute(
                "insert into api_keys (id, hash, org, label, env, scopes, subject, name) "
                "values ($1, $1, $2, null, $3, '{calls}', $4, null)",
                id,
                THE_ORG,
                env,
                subject,
            )
        yield box


async def test_0039_lets_nobody_in_by_a_row_and_takes_the_world_off_a_persons_keys(
    a_box_from_before: Box,
) -> None:
    box = a_box_from_before
    applied = (await apply_migrations(box.dsn, schema=box.schema)).applied
    assert applied[0] == "0039_production_access.sql"
    access = dict(await box.connection.fetch("select id, production from members order by id"))
    # The admin opens production by the role (Member.opens_production), never by the column.
    assert access == {"m_ana": False, "m_bruno": False}
    worlds = dict(await box.connection.fetch("select id, env from api_keys order by id"))
    assert worlds == {
        "k_ana_prod": "sandbox",
        "k_ana_sandbox": "sandbox",
        "k_visit": "production",
        "k_server": "production",
    }
    made = await box.connection.fetch("select created_by, last_used_at from api_keys")
    assert {(row["created_by"], row["last_used_at"]) for row in made} == {(None, None)}
