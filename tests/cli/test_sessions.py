"""Tests for the sessions verbs: the newest calls, one call whole or folded, the recording."""

import argparse
import asyncio
from functools import partial

import pytest

from pinecall.cli import _sessions
from pinecall.cli._sessions import sessions_list, sessions_recording, sessions_show
from pinecall.domain.errors import NotFound
from pinecall.domain.names import JsonObject
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool, open_pool
from pinecall.process.settings import Settings
from pinecall.tenancy import reads
from tests.conftest import DSN, postgres
from tests.log.conftest import logged_call
from tests.tenancy.conftest import an_org

AGENT = "agenda"


async def a_written_call(store: Store, call: str) -> None:
    """A call with a start, a turn and a summary that names no recording."""
    started: JsonObject = {"channel": "web", "from": "web_1", "to": AGENT, "started_at": 1.0}
    await store.append(call, AGENT, "call.started", started, ephemeral=False)
    spoken: JsonObject = {"speech_id": "sp_1", "text": "Hola", "interrupted": False, "metrics": {}}
    await store.append(call, AGENT, "turn.agent", spoken, ephemeral=False)
    summary: JsonObject = {
        "reason": "caller_hung_up",
        "outcome": "greeted",
        "duration_s": 3.0,
        "turns": 1,
        "usage": [],
        "cost": {"usd": 0.0, "rows": [], "unpriced": []},
    }
    await store.append(call, AGENT, "call.summary", summary, ephemeral=False)


@postgres
async def test_a_call_is_listed_shown_whole_and_folded(
    store: Store,
    call: str,
    schema: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await a_written_call(store, call)
    monkeypatch.setattr(_sessions, "open_pool", partial(open_pool, schema=schema))
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    listed = argparse.Namespace(agent=None, limit=5)
    assert await asyncio.to_thread(sessions_list, settings, listed) == 0
    assert call in capsys.readouterr().out
    whole = argparse.Namespace(call=call, as_json=False)
    assert await asyncio.to_thread(sessions_show, settings, whole) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[3] for line in lines] == ["call.started", "turn.agent", "call.summary"]
    folded = argparse.Namespace(call=call, as_json=True)
    assert await asyncio.to_thread(sessions_show, settings, folded) == 0
    assert '"outcome": "greeted"' in capsys.readouterr().out
    recorded = argparse.Namespace(call=call)
    assert await asyncio.to_thread(sessions_recording, settings, recorded) == 1
    assert "was not recorded" in capsys.readouterr().err
    nobody = argparse.Namespace(call="CA_nobody", as_json=False)
    with pytest.raises(NotFound, match="no call"):
        await asyncio.to_thread(sessions_show, settings, nobody)


@postgres
async def test_the_operators_read_of_an_orgs_call_is_in_its_access_log(
    pool: Pool,
    store: Store,
    schema: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await an_org(pool)
    call = await logged_call(store, org.id)
    monkeypatch.setattr(_sessions, "open_pool", partial(open_pool, schema=schema))
    settings = Settings.model_validate({"DATABASE_URL": DSN})
    assert (
        await asyncio.to_thread(
            sessions_show, settings, argparse.Namespace(call=call, as_json=False)
        )
        == 0
    )
    assert await asyncio.to_thread(sessions_recording, settings, argparse.Namespace(call=call)) == 1
    capsys.readouterr()
    rows = await reads.of_org(pool, org.id)
    assert sorted((row.what, row.reader) for row in rows) == [
        ("log", reads.OPERATOR),
        ("recording", reads.OPERATOR),
    ]
