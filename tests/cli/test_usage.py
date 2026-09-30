"""Tests for `usage rebuild`, against a real Postgres."""

import argparse
import asyncio
from functools import partial

import pytest

from pinecall.cli import _usage
from pinecall.log.store import Store
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from tests.conftest import DSN, postgres
from tests.log.conftest import logged_call

DRIFTED = "update usage_totals set calls = 40"


@postgres
async def test_the_verb_refolds_the_totals_from_the_log_and_says_how_many(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    monkeypatch.setattr(_usage, "open_pool", partial(open_pool, schema=schema))
    pool = await open_pool(DSN, schema=schema)
    try:
        store = Store(pool, clock=lambda: 1.0)
        for org in ("org-a", "org-b"):
            await logged_call(store, org)
        await store.writer.drained()
        async with pool.connection() as connection:
            await connection.execute(DRIFTED)
        settings = Settings.model_validate({"DATABASE_URL": DSN})
        ran = await asyncio.to_thread(_usage.rebuild, settings, argparse.Namespace())
        assert ran == 0
        assert capsys.readouterr().out == "2 summaries refolded into 2 rows of usage_totals\n"
        async with pool.connection() as connection:
            rows = await (await connection.execute("select calls from usage_totals")).fetchall()
    finally:
        await pool.close()
    assert [row["calls"] for row in rows] == [1, 1]
