"""API keys in this process's memory: the port's spec by example, and the suite's keys table."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

from pinecall.auth.keys import Issued, KeyRecord, ListedKey, fingerprint, mint, new_key_id
from pinecall.types import (
    KEY_SCOPES,
    PRODUCTION,
    Env,
)


class MemoryKeys:
    """Keys in a dict: the dev key in the sandbox, whatever a test or a dev clone issues."""

    def __init__(self, records: Mapping[str, KeyRecord] | None = None) -> None:
        self._records: dict[str, KeyRecord] = dict(records or {})
        self._rows: dict[str, ListedKey] = {
            fingerprint(key): _a_listing(fingerprint(key), record)
            for key, record in self._records.items()
        }

    async def verify(self, key: str) -> KeyRecord | None:
        """A dict lookup, then the same two checks Postgres makes in its WHERE: revoked, expired."""
        record = self._records.get(key)
        if record is None or has_expired(record):
            return None
        row = self._rows[fingerprint(key)]
        return None if row.revoked_at is not None else record

    async def issue(
        self,
        org: str,
        label: str | None = None,
        *,
        env: Env = PRODUCTION,
        scopes: frozenset[str] = KEY_SCOPES,
        subject: str | None = None,
        name: str | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> Issued:
        """Mint, remember, hand back. A process that exits forgets every key it issued."""
        key = mint(env, subject)
        record = KeyRecord(
            key_id=new_key_id(),
            org=org,
            label=label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
            expires_at=expires_at,
        )
        self._records[key] = record
        self._rows[fingerprint(key)] = replace(
            _a_listing(fingerprint(key), record), created_by=created_by
        )
        return Issued(key=key, record=record)

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """In the order they were issued, which for a dict is the order they were inserted."""
        return tuple(row for row in self._rows.values() if row.org == org)

    async def revoke(self, hashed: str) -> bool:
        """The row stays and grows a timestamp, exactly as the table does."""
        row = self._rows.get(hashed)
        if row is None or row.revoked_at is not None:
            return False
        self._rows[hashed] = replace(row, revoked_at=_now())
        return True

    async def touch(self, key_id: str) -> None:
        """The row whose record carries this id, used now."""
        for key, record in self._records.items():
            if record.key_id == key_id:
                self._rows[fingerprint(key)] = replace(
                    self._rows[fingerprint(key)], last_used_at=_now()
                )


def has_expired(record: KeyRecord) -> bool:
    """Whether this key's moment has passed: the memory twin's half of the lookup's WHERE."""
    return record.expires_at is not None and record.expires_at <= datetime.now(UTC)


def _now() -> str:
    """One clock for the memory twin, in the shape Postgres hands its timestamps back in."""
    return datetime.now(UTC).isoformat()


def _a_listing(hashed: str, record: KeyRecord) -> ListedKey:
    """A record the memory twin was handed, as the operator's listing shows it."""
    return ListedKey(
        fingerprint=hashed,
        org=record.org,
        label=record.label,
        created_at=_now(),
        env=record.env,
        scopes=tuple(sorted(record.scopes)),
        subject=record.subject,
        name=record.name,
    )
