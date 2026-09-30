"""Tests for the org's prompts: a block kept once under its hash, and read back by the org alone."""

import pytest

from pinecall.domain.agent import block_hash
from pinecall.postgres.pool import Pool
from pinecall.tenancy import prompts
from pinecall.tenancy.orgs import create
from pinecall.tenancy.prompts import Prompts
from tests.conftest import postgres

FORGET = "DELETE FROM prompts"


@postgres
async def test_a_block_is_kept_once_per_process_and_read_back_by_its_hash(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    other = await create(pool, "otra", "Otra")
    kept = Prompts()
    await kept.keep(pool, org.id, "Sos la recepción.")
    await kept.keep(pool, org.id, "")
    hashed = block_hash("Sos la recepción.")
    assert await prompts.texts_of(pool, org.id, [hashed, block_hash("")]) == {
        hashed: "Sos la recepción."
    }
    assert await prompts.texts_of(pool, other.id, [hashed]) == {}, "the org's alone"
    async with pool.connection() as connection:
        await connection.execute(FORGET)
    await kept.keep(pool, org.id, "Sos la recepción.")
    assert await prompts.texts_of(pool, org.id, [hashed]) == {}, "met again, not written again"
    await Prompts().keep(pool, org.id, "Sos la recepción.")
    assert await prompts.texts_of(pool, org.id, [hashed]) == {hashed: "Sos la recepción."}


@postgres
async def test_past_what_a_process_remembers_the_oldest_is_written_again_when_met(
    pool: Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prompts, "REMEMBERED", 1)
    org = await create(pool, "clinica-norte", "Clínica Norte")
    kept = Prompts()
    await kept.keep(pool, org.id, "uno")
    await kept.keep(pool, org.id, "dos")
    assert list(kept.kept) == [(org.id, block_hash("dos"))]
    async with pool.connection() as connection:
        await connection.execute(FORGET)
    await kept.keep(pool, org.id, "uno")
    assert await prompts.texts_of(pool, org.id, [block_hash("uno")]) == {block_hash("uno"): "uno"}
