"""Who may open a socket: API keys as sha256 hashes — issued once, listed, revoked, never read."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from pinecall._settings import Settings
from pinecall.log.store import Pool
from pinecall.types import (
    KEY_SCOPES,
    PRODUCTION,
    THE_FLEET,
    THE_TEAM,
    Env,
    an_env,
    is_a_deployment,
)

# What a key looks like when it is read out loud: a prefix nobody else uses, so a key pasted into
# an issue or a log line is recognised for what it is, and 256 bits of CSPRNG after it.
KEY_PREFIX = "pk_"
KEY_BYTES = 32

# The row's own name, short enough to read in a table and long enough that no box ever sees two.
KEY_ID_PREFIX = "k_"
KEY_ID_BYTES = 8


# An API key IS the org: every door that takes one reads the org off this record and nothing else,
# which is why a key that could name another org would be a key that could read another's log.
# And it knows WHERE and WHO: the world it opens, what it may do there, and whose it is.
@dataclass(frozen=True)
class KeyRecord:
    """Whose key this is: the org that owns it, the world it opens, what it may do, who holds it."""

    key_id: str
    org: str
    label: str | None = None
    # Which of the two worlds: the agents registered on this key, the doors they claim and every
    # call they take are that world's. A key issued before the field existed is production's.
    env: Env = PRODUCTION
    # What the key may do there, as the doors are grouped (types/key.py). Every scope is what a
    # key issued before the field existed holds, and what an org's own machine key still gets.
    scopes: frozenset[str] = KEY_SCOPES
    # Whose key it is when it is a person's: the member it was minted for, and their name, so a
    # seat minted from it names who sat down. An org's own key — the worker's, the app's — names
    # nobody, and the label says what it is for.
    subject: str | None = None
    name: str | None = None
    # Whose sandbox corner THIS request looks into, when a key that sees every corner asked for a
    # colleague's (api/_deps.py, the `pinecall-corner` header). Never stored: one request's, and
    # None on every key the table hands back.
    looking_at: str | None = None


@dataclass(frozen=True)
class Issued:
    """A key at the one moment it exists in the clear: copy it now, or issue another one."""

    key: str
    record: KeyRecord

    # The ONE shape a key in the clear ever travels in: the ops door's answer to `keys issue`,
    # and the login door's answer to a person. Built here so neither spells it.
    @property
    def as_json(self) -> dict[str, Any]:
        """The key, once, and the record it was written under."""
        return {
            "key": self.key,
            "key_id": self.record.key_id,
            "org": self.record.org,
            "label": self.record.label,
            "env": self.record.env,
            "scopes": sorted(self.record.scopes),
            "subject": self.record.subject,
            "name": self.record.name,
        }


@dataclass(frozen=True)
class ListedKey:
    """One row as an operator reads it: the fingerprint it hashes to, and never the key."""

    fingerprint: str
    org: str
    label: str | None
    created_at: str
    revoked_at: str | None = None
    env: Env = PRODUCTION
    # Sorted, so two listings of one key read the same and a test can name the whole set.
    scopes: tuple[str, ...] = ()
    subject: str | None = None
    name: str | None = None


# What a door says when the key is real, the org is right, and the key still may not do this. It
# names what the key DOES open, so a person reading it on their terminal knows which role to ask
# for. One sentence for every door and both sockets: the doors read scopes and reason no further.
# A door that opens to either of two scopes names both — "does not open app or calls".
NOT_OPENED = "this key does not open {scope}: it opens {opens}"


# Whose corner of a world a key works in. In production nobody's: what is deployed is the org's,
# held by the key its box runs on. In the sandbox the member the key was minted for, so two
# developers of one tenant each hold, reach and see their own agent; a sandbox key that names
# nobody — CI's — works in the org's own corner, which is what everybody falls back to.
# api/agents/registry.py is where the corners are, and `Held` there says the same thing.
def held_by(record: KeyRecord) -> str | None:
    """The corner of its world this key holds and reads in: nobody's, or a developer's own."""
    if is_a_deployment(record.env):
        return None
    return record.looking_at or record.subject


# An admin opening a developer's copy from the console: the same doors, in that developer's
# corner. Only a key that sees every corner may, only in the sandbox — production has no corners
# — and only into a member of its own org. Said in one sentence for every refusal.
CANNOT_LOOK_THERE = "only a key that sees every corner opens a colleague's, and only in the sandbox"


def looking_into(record: KeyRecord, holder: str) -> KeyRecord:
    """The same key, resolving this request in the corner named. Raises on a key that may not."""
    if is_a_deployment(record.env) or not sees_every_corner(record):
        raise PermissionError(CANNOT_LOOK_THERE)
    return replace(record, looking_at=holder)


# The other half of the same question. `held_by` says which corner this key WORKS in; this says
# whether it may look into everybody else's. Nobody could, which meant a tenant's admin had no way
# to tell what their developers were running and the box operator had none either — and a corner
# nobody can see is a corner nobody can help with.
def sees_every_corner(record: KeyRecord) -> bool:
    """Whether this key is the org's eyes — an admin's, the operator's — and not one person's."""
    return THE_TEAM in record.scopes


# The third question, and the one the worker asks. A tenant's key works in one corner and no door
# lets it name another; the box's worker serves every org's calls with ONE key, so at its doors
# the corner is the call's — what the dispatch said — and never the key's (auth/corner.py).
def is_the_fleets(record: KeyRecord) -> bool:
    """Whether this key is the box's worker's, and may resolve a door by the call it serves."""
    return THE_FLEET in record.scopes


def not_opening(record: KeyRecord, *scopes: str) -> str | None:
    """The refusal when this key holds none of these scopes, or None when it holds one."""
    if any(scope in record.scopes for scope in scopes):
        return None
    wanted = " or ".join(sorted(scopes))
    return NOT_OPENED.format(scope=wanted, opens=" · ".join(sorted(record.scopes)) or "nothing")


# A Pinecall key is 256 bits from a CSPRNG, not a password somebody chose. There is nothing to
# guess and nothing to look up: no dictionary covers 2^256, so a per-key salt would only make two
# identical keys hash differently, which is not a property anybody needs. What a handshake does
# need is speed, and one sha256 is a microsecond. bcrypt is for secrets a person invented.
def fingerprint(key: str) -> str:
    """What the database stores: the key's sha256, hex. The key itself is never written down."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def mint() -> str:
    """A key nobody has held before. It is returned once and hashed everywhere else."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(KEY_BYTES)}"


class Keys(Protocol):
    """Where the gateway asks whether a key is real, and where an operator issues and revokes."""

    async def verify(self, key: str) -> KeyRecord | None:
        """The record behind the key, or None when nothing answers to it."""
        ...

    async def issue(
        self,
        org: str,
        label: str | None = None,
        *,
        env: Env = PRODUCTION,
        scopes: frozenset[str] = KEY_SCOPES,
        subject: str | None = None,
        name: str | None = None,
    ) -> Issued:
        """A new key for this org, in one world. The plaintext is in the answer and nowhere else."""
        ...

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """Every key of one org, revoked ones included, by their fingerprints."""
        ...

    async def revoke(self, hashed: str) -> bool:
        """Stop honouring the key with this fingerprint. False when no row answered to it."""
        ...


class MemoryKeys:
    """Keys in a dict: the dev key in the sandbox, whatever a test or a dev clone issues."""

    def __init__(self, records: Mapping[str, KeyRecord] | None = None) -> None:
        self._records: dict[str, KeyRecord] = dict(records or {})
        self._rows: dict[str, ListedKey] = {
            fingerprint(key): _a_listing(fingerprint(key), record)
            for key, record in self._records.items()
        }

    async def verify(self, key: str) -> KeyRecord | None:
        """A dict lookup, then the same revocation check Postgres makes in its WHERE."""
        record = self._records.get(key)
        if record is None:
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
    ) -> Issued:
        """Mint, remember, hand back. A process that exits forgets every key it issued."""
        key = mint()
        record = KeyRecord(
            key_id=_a_key_id(),
            org=org,
            label=label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
        )
        self._records[key] = record
        self._rows[fingerprint(key)] = _a_listing(fingerprint(key), record)
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


# A revoked key is kept, not deleted: the logs it wrote name it, and a row that vanishes makes
# those unreadable.
_LOOKUP = """
SELECT id, org, label, env, scopes, subject, name
  FROM api_keys
 WHERE hash = $1 AND revoked_at IS NULL
"""

_ISSUE = """
INSERT INTO api_keys (id, hash, org, label, env, scopes, subject, name)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

_OF_ORG = """
SELECT hash, org, label, env, scopes, subject, name, created_at, revoked_at
  FROM api_keys
 WHERE org = $1
 ORDER BY created_at, id
"""

# Revocation is an UPDATE and never a DELETE, and it is the one that already ran that the WHERE
# filters out: revoking twice must not read as if a live key had just been stopped.
_REVOKE = """
UPDATE api_keys SET revoked_at = now() WHERE hash = $1 AND revoked_at IS NULL
"""

# What asyncpg answers an UPDATE with when the WHERE matched nothing: the command tag, verbatim.
CHANGED_NOTHING = "UPDATE 0"


class PostgresKeys:
    """Keys in Postgres, found by their hash. The key the app sent never leaves this process."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def verify(self, key: str) -> KeyRecord | None:
        """One indexed lookup on the hash. An unknown or revoked key is None, never an error."""
        row = await self._pool.fetchrow(_LOOKUP, fingerprint(key))
        return None if row is None else _a_record(row)

    async def issue(
        self,
        org: str,
        label: str | None = None,
        *,
        env: Env = PRODUCTION,
        scopes: frozenset[str] = KEY_SCOPES,
        subject: str | None = None,
        name: str | None = None,
    ) -> Issued:
        """The only moment a key exists in the clear: it is minted here, hashed, and let go."""
        key = mint()
        record = KeyRecord(
            key_id=_a_key_id(),
            org=org,
            label=label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
        )
        await self._pool.execute(
            _ISSUE, record.key_id, fingerprint(key), org, label, env, sorted(scopes), subject, name
        )
        return Issued(key=key, record=record)

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """Every key of the org, oldest first, revoked ones included and named as revoked."""
        rows = await self._pool.fetch(_OF_ORG, org)
        return tuple(_a_listed_key(row) for row in rows)

    async def revoke(self, hashed: str) -> bool:
        """The command tag says whether a row changed, so revoking a stranger is told apart."""
        tag = await self._pool.execute(_REVOKE, hashed)
        return tag.strip() != CHANGED_NOTHING


# There was a second answer here: PINECALL_DEV_KEY, one key that needed no database, which made a
# clone a gateway of its own with one org, one world and no tenants — a second runtime nobody
# asked for, whose behaviour a box never had. One runtime for everything now, so a gateway reads
# the keys a person issued, and a laptop runs the same Postgres the box does.
#
# With no database there is nowhere to check a key, and None is that said out loud: the lifespan
# logs this line at startup and every door that takes a key answers 503 with it, rather than a
# gateway that comes up looking healthy and refuses each request as though the key were wrong.
NO_KEYS_TABLE = "no database: a key is verified against the api_keys table, and there is none here"


def keys_for(settings: Settings, pool: Pool | None) -> Keys | None:  # noqa: ARG001
    """The keys table, which is the only place a key is ever checked. None with no database."""
    return None if pool is None else PostgresKeys(pool)


def _a_key_id() -> str:
    """The row's name. It is not a secret and it is not the fingerprint: it names the row."""
    return f"{KEY_ID_PREFIX}{secrets.token_hex(KEY_ID_BYTES)}"


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


def _a_record(row: Any) -> KeyRecord:
    """One row of the lookup as the door reads it: whose key knocked, where, and as whom."""
    return KeyRecord(
        key_id=str(row["id"]),
        org=str(row["org"]),
        label=_text(row["label"]),
        env=an_env(str(row["env"])),
        scopes=frozenset(str(scope) for scope in row["scopes"]),
        subject=_text(row["subject"]),
        name=_text(row["name"]),
    )


def _a_listed_key(row: Any) -> ListedKey:
    """One row of the listing. The hash column IS the fingerprint; there is nothing else to show."""
    revoked = row["revoked_at"]
    return ListedKey(
        fingerprint=str(row["hash"]),
        org=str(row["org"]),
        label=_text(row["label"]),
        created_at=str(row["created_at"]),
        revoked_at=None if revoked is None else str(revoked),
        env=an_env(str(row["env"])),
        scopes=tuple(sorted(str(scope) for scope in row["scopes"])),
        subject=_text(row["subject"]),
        name=_text(row["name"]),
    )


def _text(column: Any) -> str | None:
    """A nullable text column as the record holds it: the string, or None when the row has none."""
    return None if column is None else str(column)
