"""Tests for `facts rebuild` and doctor's question of the facts, against a real Postgres."""

import argparse
import asyncio
from functools import partial

import pytest

from pinecall.cli import _facts
from pinecall.domain.errors import DeclarationRefused
from pinecall.log.store import Store
from pinecall.postgres.pool import open_pool
from pinecall.process.settings import Settings
from tests.conftest import DSN, postgres
from tests.log.conftest import logged_call

WRONG = "update call_facts set outcome = 'wrong' where call = %(call)s"


def rebuilt(**flags: str | None) -> argparse.Namespace:
    """The verb's flags, the ones not given unset."""
    return argparse.Namespace(**{"call": None, "org": None, "since": None} | flags)


@postgres
async def test_the_verb_refolds_what_differs_and_doctor_says_so_before_and_not_after(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    monkeypatch.setattr(_facts, "open_pool", partial(open_pool, schema=schema))
    pool = await open_pool(DSN, schema=schema)
    try:
        call = await logged_call(Store(pool, clock=lambda: 1.0), "org-a")
        async with pool.connection() as connection:
            await connection.execute(WRONG, {"call": call})
    finally:
        await pool.close()
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    trouble = await _facts.examined(settings)
    assert trouble is not None
    assert f"{call} (outcome)" in trouble
    capsys.readouterr()
    ran = await asyncio.to_thread(
        _facts.rebuild, settings, rebuilt(org="org-a", since="1970-01-01")
    )
    assert ran == 0
    assert capsys.readouterr().out == (
        "1 calls refolded, 1 facts rows rewritten, 0 left as they were:"
        " their log holds an entry this release cannot read\n"
    )
    assert await _facts.examined(settings) is None


def test_a_since_that_is_no_day_is_refused_before_the_database_is_asked() -> None:
    settings = Settings.model_validate({"DATABASE_URL": "postgresql://nobody@127.0.0.1:1/none"})
    with pytest.raises(DeclarationRefused, match="a day"):
        _facts.rebuild(settings, rebuilt(since="yesterday"))
