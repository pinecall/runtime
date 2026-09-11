"""The door's half: what a key hashes to, what Postgres is asked, and which Keys we get."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from pinecall._settings import Settings
from pinecall.auth.keys import (
    DEV_KEY_RECORD,
    KEY_PREFIX,
    KeyRecord,
    MemoryKeys,
    PostgresKeys,
    fingerprint,
    keys_for,
    mint,
)
from pinecall.log.store.postgres import MIGRATIONS
from pinecall.types import DEVELOPMENT, ENVS, KEY_SCOPES, PRODUCTION

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


def test_a_minted_key_is_new_every_time_and_says_what_it_is() -> None:
    """A key is 256 bits of CSPRNG under a prefix, so one pasted anywhere is recognisable."""
    assert mint() != mint()
    assert mint().startswith(KEY_PREFIX)


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


async def test_postgres_revoke_reads_the_command_tag_so_a_stranger_is_told_apart() -> None:
    pool = _APoolOfOneRow(None, tag="UPDATE 0")
    assert await PostgresKeys(pool).revoke("a" * 64) is False
    assert await PostgresKeys(_APoolOfOneRow(None, tag="UPDATE 1")).revoke("a" * 64) is True


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
        env=DEVELOPMENT,
        scopes=frozenset({"calls", "talk"}),
        subject="m_1",
        name="Berna",
    )
    assert await keys.verify(issued.key) == issued.record
    assert issued.record.env == DEVELOPMENT
    assert issued.record.scopes == frozenset({"calls", "talk"})
    assert (issued.record.subject, issued.record.name) == ("m_1", "Berna")
    (listed,) = await keys.listed("clinica")
    assert (listed.env, listed.scopes, listed.subject, listed.name) == (
        DEVELOPMENT,
        ("calls", "talk"),
        "m_1",
        "Berna",
    )


async def test_postgres_issue_writes_the_world_the_scopes_sorted_and_the_person() -> None:
    """The columns travel in the INSERT's order; the scopes sorted, so two rows compare."""
    pool = _APoolOfOneRow(None)
    issued = await PostgresKeys(pool).issue(
        org="clinica", env=DEVELOPMENT, scopes=frozenset({"talk", "calls"}), subject="m_1", name="B"
    )
    assert pool.asked == [
        issued.record.key_id,
        fingerprint(issued.key),
        "clinica",
        None,
        DEVELOPMENT,
        ["calls", "talk"],
        "m_1",
        "B",
    ]


async def test_postgres_reads_the_world_and_the_scopes_back_off_the_row() -> None:
    row = _a_row("k_2", "madrid", env=DEVELOPMENT, scopes=["talk"], subject="m_1", name="Berna")
    record = await PostgresKeys(_APoolOfOneRow(row)).verify(A_KEY)
    assert record == KeyRecord(
        key_id="k_2",
        org="madrid",
        env=DEVELOPMENT,
        scopes=frozenset({"talk"}),
        subject="m_1",
        name="Berna",
    )


# The migration backfills every existing row with every scope, and SQL cannot import a Python
# constant: the literal is read back out of the file and compared, so adding a scope to one side
# and not the other fails here and not on a box.
def test_the_migration_backfills_the_very_scopes_and_the_very_worlds_the_runtime_knows() -> None:
    said = (MIGRATIONS / "0013_environments.sql").read_text(encoding="utf-8")
    array = re.search(r"ARRAY\[(.*?)\]", said, re.S)
    assert array is not None
    assert sorted(re.findall(r"'([a-z]+)'", array.group(1))) == sorted(KEY_SCOPES)
    worlds = re.findall(r"CHECK \(env IN \((.*?)\)\)", said)
    assert worlds, "the env column is checked against the two worlds"
    assert all(sorted(re.findall(r"'([a-z]+)'", one)) == sorted(ENVS) for one in worlds)


def test_the_dev_key_opens_development() -> None:
    """A laptop is where things are written: what `pinecall run` registers there is not deployed."""
    assert DEV_KEY_RECORD.env == DEVELOPMENT
    assert DEV_KEY_RECORD.scopes == KEY_SCOPES


async def test_a_key_no_row_answers_to_is_none_and_not_an_error() -> None:
    assert await PostgresKeys(_APoolOfOneRow(None)).verify(A_KEY) is None


def test_the_dev_key_is_the_only_key_when_it_is_set() -> None:
    keys = keys_for(Settings(dev_key="the-dev-key"), pool=None)
    assert isinstance(keys, MemoryKeys)


async def test_the_dev_key_carries_its_own_org() -> None:
    keys = keys_for(Settings(dev_key="the-dev-key"), pool=None)
    assert await keys.verify("the-dev-key") == DEV_KEY_RECORD


def test_a_gateway_with_no_dev_key_and_no_database_says_it_can_verify_nothing() -> None:
    with pytest.raises(RuntimeError, match="verify nothing"):
        keys_for(Settings(dev_key=None), pool=None)


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
    }


class _APoolOfOneRow:
    """A pool that answers every query with the same row, and remembers what it was asked."""

    def __init__(self, row: Mapping[str, Any] | None, tag: str = "SELECT 1") -> None:
        self._row = row
        self._tag = tag
        self.asked: list[Any] = []

    async def fetchrow(self, _query: str, /, *args: Any) -> Mapping[str, Any] | None:
        self.asked.extend(args)
        return self._row

    # The keys door asks for one row and nothing else; the other two verbs of the pool are here
    # because the Protocol has them, and a fake that lied about its shape would type-check alone.
    async def fetch(self, _query: str, /, *args: Any) -> Sequence[Mapping[str, Any]]:
        self.asked.extend(args)
        return [] if self._row is None else [self._row]

    async def execute(self, _query: str, /, *args: Any) -> str:
        self.asked.extend(args)
        return self._tag

    async def close(self) -> None:
        """Nothing to give back."""
