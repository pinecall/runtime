"""The door's half: what a key hashes to, what Postgres is asked, and which Keys we get."""

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
    pool = _APoolOfOneRow({"id": "k_1", "org": "madrid", "label": None})
    record = await PostgresKeys(pool).verify(A_KEY)
    assert record == KeyRecord(key_id="k_1", org="madrid", label=None)
    assert pool.asked == [fingerprint(A_KEY)]


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
