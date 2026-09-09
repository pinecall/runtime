"""SIGKILL mid-append: the log ends where the call did, contiguous, with no torn row behind it."""

import asyncio
import signal
import sys
from pathlib import Path

import pytest

from pinecall.log.store.postgres import PostgresStore
from tests.log.conftest import Dev

pytestmark = pytest.mark.postgres

WRITER = Path(__file__).parent / "_sigkill_writer.py"

# Enough appends that the kill lands in the middle of the loop rather than before it starts.
APPENDS_BEFORE_THE_KILL = 30

# A writer on the same laptop answers in milliseconds; this is a wall against hanging the suite.
A_LINE_MUST_ARRIVE_WITHIN_SECONDS = 20.0


async def test_a_killed_writer_leaves_a_contiguous_log_and_no_torn_row(
    postgres: Dev, postgres_store: PostgresStore, agent: str, call: str
) -> None:
    """The strong claim: the head counter and the rows agree, because one statement made both."""
    last_seen = await _write_then_kill(postgres, call, agent)

    entries = await postgres_store.since(call, limit=1000)
    assert [entry.seq for entry in entries] == list(range(1, len(entries) + 1)), "a hole"
    assert len(entries) >= last_seen, "an entry the writer was told it had was not there"
    # The counter is not ahead of the rows: an append that did not finish handed out no number.
    assert await postgres_store.latest_seq(call) == len(entries)
    assert all(entry.data == {"name": "tick", "data": {}} for entry in entries), "a torn row"
    assert all(entry.agent == agent and entry.call == call for entry in entries)


async def _write_then_kill(postgres: Dev, call: str, agent: str) -> int:
    """Run the writer until it has said enough seqs, then SIGKILL it. Answers with the last."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(WRITER),
        postgres.dsn,
        postgres.schema,
        call,
        agent,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        last = 0
        for _ in range(APPENDS_BEFORE_THE_KILL):
            line = await asyncio.wait_for(
                process.stdout.readline(), timeout=A_LINE_MUST_ARRIVE_WITHIN_SECONDS
            )
            last = int(line)
        # Not terminate(): a writer that gets to finish its append proves nothing about a crash.
        process.send_signal(signal.SIGKILL)
        assert await process.wait() == -signal.SIGKILL
        return last
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
