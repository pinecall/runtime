"""`sandbox seed`: production's personas, sandbox knowledge and tuning, copied only once."""

from collections.abc import AsyncIterator
from io import StringIO
from uuid import uuid4

import pytest

from pinecall.cli.sandbox.seed_postgres import seed
from pinecall.db import Pool, apply_migrations, open_pool
from tests.cli.conftest import Dev

pytestmark = pytest.mark.postgres

ORG = "org_seeded"
SLUG = "clinica-seed"
# A chunk's vector, as a halfvec's text form: what the index holds, whatever its numbers are.
A_VECTOR = "[" + ",".join(["0.5"] * 1024) + "]"

# What production held for the sandbox before the sandbox was an instance: one of each, and a
# production twin of each world-scoped row, which must stay behind.
PRODUCTION_HELD = f"""
INSERT INTO orgs (id, slug, name) VALUES ('{ORG}', '{SLUG}', 'Clínica Seed');
INSERT INTO quotas (org, minutes) VALUES ('{ORG}', 100);
INSERT INTO agent_personas (org, agent, name, goal, style)
     VALUES ('{ORG}', 'clinica', 'impatient', 'book today', 'short');
INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks)
     VALUES ('{ORG}', 'sandbox', 'm_berna', 'faq', 'bge-m3', 1024, 2),
            ('{ORG}', 'production', '', 'faq', 'bge-m3', 1024, 1);
INSERT INTO knowledge_files (org, env, holder, base, path, text, chunks)
     VALUES ('{ORG}', 'sandbox', 'm_berna', 'faq', 'faq.md', 'Abrimos a las 9.', 2);
INSERT INTO knowledge_chunks (org, env, holder, base, path, ordinal, text, embedding)
     VALUES ('{ORG}', 'sandbox', 'm_berna', 'faq', 'faq.md', 0, 'Abrimos', '{A_VECTOR}'),
            ('{ORG}', 'sandbox', 'm_berna', 'faq', 'faq.md', 1, 'a las 9', '{A_VECTOR}'),
            ('{ORG}', 'production', '', 'faq', 'faq.md', 0, 'Abrimos a las 9', '{A_VECTOR}');
INSERT INTO agent_config (org, env, holder, agent, version, config, author)
     VALUES ('{ORG}', 'sandbox', 'm_berna', 'clinica', 1, '{{"voice": "ana"}}', 'm_berna'),
            ('{ORG}', 'production', '', 'clinica', 1, '{{"voice": "eva"}}', 'm_admin');
INSERT INTO lexicon (org, env, holder, version, said, author)
     VALUES ('{ORG}', 'sandbox', '', 1, '{{"Pinecall": "Painecol"}}', 'm_berna');
"""


async def a_database(postgres: Dev) -> AsyncIterator[Pool]:
    """An instance's whole database, this test's own: every migration, in a schema dropped after."""
    schema = f"pinecall_seed_{uuid4().hex[:12]}"
    await apply_migrations(postgres.dsn, schema=schema)
    pool = await open_pool(postgres.dsn, schema=schema)
    try:
        yield pool
    finally:
        await pool.execute(f"drop schema if exists {schema} cascade")
        await pool.close()


@pytest.fixture
async def production(postgres: Dev) -> AsyncIterator[Pool]:
    """Production's database, holding the sandbox's rows the way it did before the cut."""
    async for pool in a_database(postgres):
        await pool.execute(PRODUCTION_HELD)
        yield pool


@pytest.fixture
async def sandbox(postgres: Dev) -> AsyncIterator[Pool]:
    """The new sandbox's database: migrated, and nothing in it but its own default org."""
    async for pool in a_database(postgres):
        yield pool


async def counted(pool: Pool, table: str, where: str = "true") -> int:
    row = await pool.fetchrow(f"SELECT count(*) AS n FROM {table} WHERE org = '{ORG}' AND {where}")
    assert row is not None
    return int(row["n"])


async def test_the_sandbox_starts_with_its_orgs_personas_knowledge_and_tuning_and_nothing_else(
    production: Pool, sandbox: Pool
) -> None:
    said = StringIO()

    assert await seed(production, sandbox, said) == 0

    assert said.getvalue().splitlines() == [
        "orgs: 1 mirrored, 0 refused",
        "agent_personas: 1 copied, 0 already there",
        "knowledge_bases: 1 copied, 0 already there",
        "knowledge_files: 1 copied, 0 already there",
        "knowledge_chunks: 2 copied, 0 already there",
        "agent_config: 1 copied, 0 already there",
        "lexicon: 1 copied, 0 already there",
    ]
    org = await sandbox.fetchrow(f"SELECT slug FROM orgs WHERE id = '{ORG}'")
    assert org is not None and org["slug"] == SLUG
    assert await counted(sandbox, "quotas") == 0
    assert await counted(sandbox, "knowledge_chunks", "env = 'production'") == 0
    assert await counted(sandbox, "knowledge_chunks", "holder = 'm_berna'") == 2
    config = await sandbox.fetchrow(
        f"SELECT config::text AS c FROM agent_config WHERE org = '{ORG}'"
    )
    assert config is not None and '"ana"' in config["c"]
    # The vector crossed as its text form and landed as a halfvec again, number for number.
    assert await counted(sandbox, "knowledge_chunks", f"embedding::text = '{A_VECTOR}'") == 2


async def test_seeding_again_copies_nothing_twice_and_overwrites_nothing(
    production: Pool, sandbox: Pool
) -> None:
    await seed(production, sandbox, StringIO())
    await sandbox.execute(f"UPDATE agent_personas SET goal = 'changed here' WHERE org = '{ORG}'")
    said = StringIO()

    await seed(production, sandbox, said)

    assert "knowledge_chunks: 0 copied, 2 already there" in said.getvalue().splitlines()
    assert await counted(sandbox, "knowledge_chunks") == 2
    kept = await sandbox.fetchrow(f"SELECT goal FROM agent_personas WHERE org = '{ORG}'")
    assert kept is not None and kept["goal"] == "changed here"


async def test_an_org_whose_slug_the_sandbox_holds_under_another_id_is_said_and_left_alone(
    production: Pool, sandbox: Pool
) -> None:
    await sandbox.execute(f"INSERT INTO orgs (id, slug, name) VALUES ('org_local', '{SLUG}', 'x')")
    said = StringIO()

    await seed(production, sandbox, said)

    assert said.getvalue().splitlines()[:2] == [
        f"  {SLUG}: another org holds the slug in the sandbox, and nothing of it is copied",
        "orgs: 0 mirrored, 1 refused",
    ]
    assert await counted(sandbox, "agent_personas") == 0
