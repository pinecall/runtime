"""The door's half: what a key hashes to, what Postgres is asked, and which Keys we get."""

import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pinecall.auth.keys import (
    PERSONS_PREFIX,
    PRODUCTION_PREFIX,
    SANDBOX_PREFIX,
    KeyRecord,
    fingerprint,
    keys_for,
    mint,
)
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.keys_postgres import PostgresKeys
from pinecall.auth.visitor_keys import StandingKeys
from pinecall.db import MIGRATIONS
from pinecall.types import ENVS, KEY_SCOPES, PRODUCTION, SANDBOX
from tests.pools import Held, acquired

pytestmark = pytest.mark.unit

A_KEY = "pk_live_0123456789abcdef"


def test_the_fingerprint_is_the_keys_sha256_and_nothing_of_the_key_survives_it() -> None:
    hashed = fingerprint(A_KEY)
    assert len(hashed) == 64
    assert hashed == fingerprint(A_KEY)
    assert A_KEY not in hashed


def test_two_keys_that_differ_by_one_character_hash_apart() -> None:
    assert fingerprint(A_KEY) != fingerprint(A_KEY + "0")


async def test_memory_keys_answers_only_what_it_issued() -> None:
    keys = MemoryKeys()
    issued = await keys.issue(org="clinica")
    assert await keys.verify(issued.key) == issued.record
    assert await keys.verify("pk_live_something_else") is None


def test_a_minted_key_is_new_every_time_and_says_whose_it_is() -> None:
    """A key is 256 bits of CSPRNG under a prefix, so one pasted anywhere is recognisable: a
    person's, or a server's token and the world it was made for."""
    assert mint(PRODUCTION, None) != mint(PRODUCTION, None)
    assert mint(PRODUCTION, None).startswith(PRODUCTION_PREFIX)
    assert mint(SANDBOX, None).startswith(SANDBOX_PREFIX)
    assert mint(SANDBOX, "m_1").startswith(PERSONS_PREFIX)
    assert not mint(SANDBOX, "m_1").startswith(SANDBOX_PREFIX)


async def test_a_revoked_key_stops_verifying_and_its_row_stays_in_the_listing() -> None:
    """Revoke is not delete: the log entries that name the key must stay readable."""
    keys = MemoryKeys()
    issued = await keys.issue(org="clinica", label="the worker")
    hashed = fingerprint(issued.key)
    assert await keys.revoke(hashed) is True
    assert await keys.verify(issued.key) is None
    listed = await keys.listed("clinica")
    assert [(row.fingerprint, row.label, row.revoked_at is None) for row in listed] == [
        (hashed, "the worker", False)
    ]


# A sandbox person's key lives a day (auth/person_keys.py). Past its moment it reads exactly as a
# revoked key does: nothing answers to it, and the door says nothing about why.
async def test_an_expired_key_is_nothing_and_one_with_time_left_still_opens() -> None:
    keys = MemoryKeys()
    now = datetime.now(UTC)
    gone = await keys.issue(org="clinica", subject="m_1", expires_at=now - timedelta(seconds=1))
    left = await keys.issue(org="clinica", subject="m_1", expires_at=now + timedelta(hours=1))
    assert await keys.verify(gone.key) is None
    assert await keys.verify(left.key) == left.record


async def test_postgres_asks_the_row_for_its_moment_and_refuses_it_in_the_same_where() -> None:
    """The lookup itself refuses a key whose moment passed; an expired row is never handed back."""
    pool = _APoolOfOneRow(None)
    await PostgresKeys(pool).verify(A_KEY)
    assert "expires_at IS NULL OR expires_at > now()" in pool.queries[0]
    moment = datetime.now(UTC) + timedelta(hours=24)
    issued = await PostgresKeys(pool).issue(org="clinica", subject="m_1", expires_at=moment)
    assert pool.asked[-1] == moment and issued.record.expires_at == moment


async def test_revoking_a_fingerprint_nobody_answers_to_is_false_and_not_an_error() -> None:
    keys = MemoryKeys()
    issued = await keys.issue(org="clinica")
    assert await keys.revoke("not-a-fingerprint") is False
    assert await keys.revoke(fingerprint(issued.key)) is True
    assert await keys.revoke(fingerprint(issued.key)) is False, "a second revoke changes nothing"


async def test_an_orgs_listing_holds_that_orgs_keys_and_nobody_elses() -> None:
    keys = MemoryKeys()
    await keys.issue(org="clinica")
    await keys.issue(org="tienda")
    assert [row.org for row in await keys.listed("clinica")] == ["clinica"]


async def test_postgres_issue_writes_the_fingerprint_and_never_the_key() -> None:
    """The one moment plaintext exists: it is in the answer, and the statement carries a hash."""
    pool = _APoolOfOneRow(None)
    issued = await PostgresKeys(pool).issue(org="clinica", label="the worker")
    assert fingerprint(issued.key) in pool.asked
    assert issued.key not in pool.asked
    assert (issued.record.org, issued.record.label) == ("clinica", "the worker")


async def test_postgres_revoke_answers_whether_a_row_came_back_so_a_stranger_is_told_apart() -> (
    None
):
    assert await PostgresKeys(_APoolOfOneRow(None)).revoke("a" * 64) is False
    revoked = _APoolOfOneRow(_a_row("k_1", "madrid"))
    assert await PostgresKeys(revoked).revoke("a" * 64) is True
    assert "RETURNING id" in revoked.queries[-1]


async def test_postgres_keys_looks_the_key_up_by_its_hash_and_never_by_the_key() -> None:
    pool = _APoolOfOneRow(_a_row("k_1", "madrid"))
    record = await PostgresKeys(pool).verify(A_KEY)
    assert record == KeyRecord(key_id="k_1", org="madrid", label=None)
    assert pool.asked == [fingerprint(A_KEY)]


# ── the key knows where and who ─────────────────────────────────────────────────


def test_a_key_issued_before_the_field_existed_is_productions_with_every_scope() -> None:
    """What 0013 leaves every existing row as, and what a record built with nothing else means."""
    record = KeyRecord(key_id="k_1", org="madrid")
    assert record.env == PRODUCTION
    assert record.scopes == KEY_SCOPES
    assert (record.subject, record.name) == (None, None)


async def test_a_key_is_issued_into_one_world_with_the_scopes_and_the_person_it_was_asked_for() -> (
    None
):
    keys = MemoryKeys()
    issued = await keys.issue(
        org="clinica",
        label="berna's laptop",
        env=SANDBOX,
        scopes=frozenset({"calls", "talk"}),
        subject="m_1",
        name="Berna",
    )
    assert await keys.verify(issued.key) == issued.record
    assert issued.record.env == SANDBOX
    assert issued.record.scopes == frozenset({"calls", "talk"})
    assert (issued.record.subject, issued.record.name) == ("m_1", "Berna")
    (listed,) = await keys.listed("clinica")
    assert (listed.env, listed.scopes, listed.subject, listed.name) == (
        SANDBOX,
        ("calls", "talk"),
        "m_1",
        "Berna",
    )


async def test_postgres_issue_writes_the_world_the_scopes_sorted_and_the_person() -> None:
    """The columns travel in the INSERT's order; the scopes sorted, so two rows compare."""
    pool = _APoolOfOneRow(None)
    issued = await PostgresKeys(pool).issue(
        org="clinica", env=SANDBOX, scopes=frozenset({"talk", "calls"}), subject="m_1", name="B"
    )
    assert pool.asked == [
        issued.record.key_id,
        fingerprint(issued.key),
        "clinica",
        None,
        SANDBOX,
        ["calls", "talk"],
        "m_1",
        "B",
        None,
        None,
    ]


async def test_postgres_reads_the_world_and_the_scopes_back_off_the_row() -> None:
    row = _a_row("k_2", "madrid", env=SANDBOX, scopes=["talk"], subject="m_1", name="Berna")
    record = await PostgresKeys(_APoolOfOneRow(row)).verify(A_KEY)
    assert record == KeyRecord(
        key_id="k_2",
        org="madrid",
        env=SANDBOX,
        scopes=frozenset({"talk"}),
        subject="m_1",
        name="Berna",
    )


# A scope added in Python is a scope no existing row holds until a migration hands it over, and
# SQL cannot import a Python constant. So the words the migrations name are read back out of the
# files and compared: 0013 backfilled the twelve of its day, 0017 handed the thirteenth to the
# rows that had earned it, 0037 the fourteenth to the floor's, and together they are what the
# runtime knows. Adding one on the Python side and not the SQL side fails here, not on a box.
HANDING_OVER = ("0017_provider_scope.sql", "0037_agent_tuning.sql")


def test_the_migrations_hand_over_the_very_scopes_the_runtime_knows() -> None:
    backfilled = (MIGRATIONS / "0013_environments.sql").read_text(encoding="utf-8")
    array = re.search(r"ARRAY\[(.*?)\]", backfilled, re.DOTALL)
    assert array is not None
    handed: set[str] = set()
    for name in HANDING_OVER:
        since = (MIGRATIONS / name).read_text(encoding="utf-8")
        found = set(re.findall(r"array_append\(scopes, '([a-z]+)'\)", since))
        assert found, f"{name} hands a scope to the rows that hold the one it was cut from"
        handed |= found
    assert set(re.findall(r"'([a-z]+)'", array.group(1))) | handed == KEY_SCOPES


# The LAST migration that writes the CHECK, not a named one: an applied migration is never
# edited, so the file that holds today's worlds is whichever one most recently said them. It was
# 0013 until the world things are written in stopped being called `development`.
def test_the_migration_checks_the_very_worlds_the_runtime_knows() -> None:
    wrote = [
        file for file in sorted(MIGRATIONS.glob("0*.sql")) if "CHECK (env IN" in file.read_text()
    ]
    assert wrote, "some migration checks the env column against the worlds"
    worlds = re.findall(r"CHECK \(env IN \((.*?)\)\)", wrote[-1].read_text(encoding="utf-8"))
    assert worlds, "and it names them"
    assert all(sorted(re.findall(r"'([a-z]+)'", one)) == sorted(ENVS) for one in worlds)


async def test_a_key_no_row_answers_to_is_none_and_not_an_error() -> None:
    assert await PostgresKeys(_APoolOfOneRow(None)).verify(A_KEY) is None


# PINECALL_DEV_KEY was the second answer: one key that needed no database and, when it was set,
# the ONLY key the gateway honoured — one org, one world, no tenants, which is a second runtime
# with behaviour a box never had. There is one now, and it reads the table a person writes.
def test_a_gateway_with_no_database_has_nowhere_to_verify_a_key_and_says_so() -> None:
    assert keys_for(None) is None


# With one, what the gateway holds is the table behind the one check no door makes for itself:
# a visitor's key is asked, each time it knocks, whether its person still runs the box.
def test_a_gateway_with_a_database_asks_about_a_visitors_standing_on_every_verify() -> None:
    assert isinstance(keys_for(_APoolOfOneRow(None)), StandingKeys)


def _a_row(
    key_id: str,
    org: str,
    *,
    env: str = PRODUCTION,
    scopes: list[str] | None = None,
    subject: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """One api_keys row as the lookup's SELECT hands it back, every column 0013 added included."""
    return {
        "id": key_id,
        "org": org,
        "label": None,
        "env": env,
        "scopes": sorted(KEY_SCOPES) if scopes is None else scopes,
        "subject": subject,
        "name": name,
        "expires_at": None,
    }


class _APoolOfOneRow:
    """A pool that answers every query with the same row, and remembers what it was asked."""

    def __init__(self, row: Mapping[str, Any] | None) -> None:
        self._row = row
        self.asked: list[Any] = []
        self.queries: list[str] = []

    async def fetchrow(self, query: str, /, *args: Any) -> Mapping[str, Any] | None:
        self.queries.append(query)
        self.asked.extend(args)
        return self._row

    # The keys door asks for one row and nothing else; the other two verbs of the pool are here
    # because the Protocol has them, and a fake that lied about its shape would type-check alone.
    async def fetch(self, _query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        self.asked.extend(args)
        return [] if self._row is None else [self._row]

    async def execute(self, _query: str, /, *args: Any) -> str:
        self.asked.extend(args)
        return "SELECT 1"

    def acquire(self) -> AbstractAsyncContextManager[Held]:
        return acquired(self)

    async def close(self) -> None:
        """Nothing to give back."""
