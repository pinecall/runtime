"""Tests for an org's secrets: kept sealed per world, listed by name, never read back."""

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy.org_secrets import (
    LONGEST_VALUE,
    Secret,
    drop_secret,
    environment_of,
    put_secret,
    secrets_of,
)
from pinecall.tenancy.vault import opened
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

VAULT = vault_of(Fernet.generate_key().decode())


@pytest.mark.parametrize("name", ["crm_token", "1TOKEN", "CRM-TOKEN", "CRM TOKEN", ""])
def test_a_name_a_shell_would_not_take_is_refused(name: str) -> None:
    with pytest.raises(DeclarationRefused, match="capitals, digits and underscores"):
        Secret(env="production", name=name, value="x")


def test_a_name_the_box_sets_itself_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="the box's to set"):
        Secret(env="production", name="PINECALL_KEY", value="x")


def test_a_value_past_the_ceiling_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="16 KB at most"):
        Secret(env="production", name="CRM_TOKEN", value="x" * (LONGEST_VALUE + 1))


@postgres
async def test_a_secret_is_kept_sealed_and_listed_by_name_without_its_value(pool: Pool) -> None:
    org = await an_org(pool)
    secret = Secret(env="production", name="CRM_TOKEN", value="made-up-by-this-test")
    await put_secret(pool, VAULT, org.id, secret, set_by="m_ana")
    [listed] = await secrets_of(pool, org.id, "production")
    assert (listed.name, listed.set_by) == ("CRM_TOKEN", "m_ana")
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT sealed FROM org_secrets")).fetchone()
    assert row is not None
    assert "made-up-by-this-test" not in row["sealed"]
    assert opened(VAULT, row["sealed"]) == "made-up-by-this-test"


@postgres
async def test_setting_a_name_again_replaces_its_value_and_who_set_it(pool: Pool) -> None:
    org = await an_org(pool)
    await put_secret(
        pool, VAULT, org.id, Secret(env="sandbox", name="CRM_TOKEN", value="a"), set_by="m_ana"
    )
    await put_secret(
        pool, VAULT, org.id, Secret(env="sandbox", name="CRM_TOKEN", value="b"), set_by="m_ben"
    )
    [listed] = await secrets_of(pool, org.id, "sandbox")
    assert listed.set_by == "m_ben"


@postgres
async def test_each_world_keeps_its_own_secrets(pool: Pool) -> None:
    org = await an_org(pool)
    await put_secret(
        pool, VAULT, org.id, Secret(env="sandbox", name="CRM_TOKEN", value="a"), set_by="m_ana"
    )
    assert await secrets_of(pool, org.id, "production") == []


@postgres
async def test_a_secret_dropped_is_gone_and_a_name_nobody_set_is_not_found(pool: Pool) -> None:
    org = await an_org(pool)
    await put_secret(
        pool, VAULT, org.id, Secret(env="sandbox", name="CRM_TOKEN", value="a"), set_by="m_ana"
    )
    await drop_secret(pool, org.id, "sandbox", "CRM_TOKEN")
    assert await secrets_of(pool, org.id, "sandbox") == []
    with pytest.raises(NotFound, match="no secret called CRM_TOKEN"):
        await drop_secret(pool, org.id, "sandbox", "CRM_TOKEN")


@postgres
async def test_the_environment_is_the_worlds_secrets_opened_by_name(pool: Pool) -> None:
    org = await an_org(pool)
    for name, value in (("CRM_TOKEN", "a"), ("CRM_URL", "https://crm.test")):
        await put_secret(
            pool, VAULT, org.id, Secret(env="production", name=name, value=value), set_by="m_ana"
        )
    await put_secret(
        pool, VAULT, org.id, Secret(env="sandbox", name="CRM_TOKEN", value="test"), set_by="m_ana"
    )
    assert await environment_of(pool, VAULT, org.id, "production") == {
        "CRM_TOKEN": "a",
        "CRM_URL": "https://crm.test",
    }


@postgres
async def test_a_secret_sealed_under_a_key_the_vault_no_longer_lists_is_left_out(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    await put_secret(
        pool, VAULT, org.id, Secret(env="production", name="CRM_TOKEN", value="a"), set_by="m_ana"
    )
    another = vault_of(Fernet.generate_key().decode())
    assert await environment_of(pool, another, org.id, "production") == {}
