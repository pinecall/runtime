"""Tests for `drift rebuild`, against a real Postgres."""

import argparse
import asyncio
from functools import partial

import pytest

from pinecall.cli import _drift
from pinecall.domain.errors import DeclarationRefused
from pinecall.log.store import Store
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from tests.conftest import DSN, postgres
from tests.log.conftest import ACall, judgment, logged_call

ORG = "insert into orgs (id, slug, name) values ('org-a', 'org-a', 'Org A')"

JUDGED = "select judge, held from judge_days where org = 'org-a'"


def rebuilt(**flags: str | None) -> argparse.Namespace:
    """The verb's flags, the ones not given unset."""
    return argparse.Namespace(**{"org": None, "since": None} | flags)


@postgres
async def test_the_verb_counts_each_sealed_call_of_the_org_into_its_day_again(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    monkeypatch.setattr(_drift, "open_pool", partial(open_pool, schema=schema))
    pool = await open_pool(DSN, schema=schema)
    try:
        async with pool.connection() as connection:
            await connection.execute(ORG)
        await logged_call(
            Store(pool, clock=lambda: 1.0), "org-a", ACall(judges=(judgment("consent", "held"),))
        )
        settings = Settings.model_validate({"DATABASE_URL": DSN})
        ran = await asyncio.to_thread(
            _drift.rebuild, settings, rebuilt(org="org-a", since="1970-01-01")
        )
        async with pool.connection() as connection:
            rows = await (await connection.execute(JUDGED)).fetchall()
    finally:
        await pool.close()
    assert ran == 0
    assert capsys.readouterr().out == "1 sealed calls read, 1 counted into their day's drift\n"
    assert [(row["judge"], row["held"]) for row in rows] == [("consent", 1)]


def test_a_since_that_is_no_day_is_refused_before_the_database_is_asked() -> None:
    settings = Settings.model_validate({"DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none"})
    with pytest.raises(DeclarationRefused, match="a day"):
        _drift.rebuild(settings, rebuilt(since="yesterday"))
