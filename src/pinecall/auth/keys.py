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
    HOLDING,
    KEY_SCOPES,
    PRODUCTION,
    THE_FLEET,
    THE_TEAM,
    Env,
    is_a_deployment,
)

# What a key looks like when it is read out loud: a prefix nobody else uses, so a key pasted into
# an issue or a log line is recognised for what it is, and 256 bits of CSPRNG after it. A server's
# token says its world in the prefix, as Stripe's do, so a `.env` read at a glance says which one
# it holds; a person's key has no world to say. Keys minted before (`pk_…`) still answer: a key is
# found by its sha256, never by its shape.
PERSONS_PREFIX = "pc_"
PRODUCTION_PREFIX = "pc_live_"
SANDBOX_PREFIX = "pc_test_"
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
    # Who made a server's token — the person, or the key that asked — which stays when they leave.
    created_by: str | None = None
    # When the key last opened the app socket or asked /v1/whoami: what a token list shows.
    last_used_at: str | None = None


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


# The other half of the same question. `held_by` says which corner this key WORKS in; this says
# whether it may look into everybody else's. Nobody could, which meant a tenant's admin had no way
# to tell what their developers were running and the box operator had none either — and a corner
# nobody can see is a corner nobody can help with. It takes BOTH the team's door and the agent's
# own (`app`): `team` alone is a manager's, who runs the floor and holds no agent, and a
# developer's sandbox — their calls, their memory, their copy — is not the floor's to open.
def sees_every_corner(record: KeyRecord) -> bool:
    """Whether this key is the org's eyes — an admin's, the box's own — and not one person's."""
    return THE_TEAM in record.scopes and HOLDING in record.scopes


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


def mint(env: Env, subject: str | None) -> str:
    """A key nobody has held before, its prefix saying whose. Returned once, hashed elsewhere."""
    prefix = PERSONS_PREFIX if subject is not None else None
    prefix = prefix or (PRODUCTION_PREFIX if is_a_deployment(env) else SANDBOX_PREFIX)
    return f"{prefix}{secrets.token_urlsafe(KEY_BYTES)}"


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
        created_by: str | None = None,
    ) -> Issued:
        """A new key for this org, in one world. The plaintext is in the answer and nowhere else."""
        ...

    async def listed(self, org: str) -> tuple[ListedKey, ...]:
        """Every key of one org, revoked ones included, by their fingerprints."""
        ...

    async def revoke(self, hashed: str) -> bool:
        """Stop honouring the key with this fingerprint. False when no row answered to it."""
        ...

    # Written at the two moments a token list needs — the app socket opening, /v1/whoami — and
    # at no other door, so a busy key is not a write per request.
    async def touch(self, key_id: str) -> None:
        """Say this key was used now."""
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
        created_by: str | None = None,
    ) -> Issued:
        """Mint, remember, hand back. A process that exits forgets every key it issued."""
        key = mint(env, subject)
        record = KeyRecord(
            key_id=a_key_id(),
            org=org,
            label=label,
            env=env,
            scopes=scopes,
            subject=subject,
            name=name,
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
    if pool is None:
        return None
    # Imported here: auth/visiting.py and the Postgres twin import this module for the protocol.
    from pinecall.auth.keys_postgres import PostgresKeys
    from pinecall.auth.members import members_for
    from pinecall.auth.visiting import StandingKeys

    return StandingKeys(PostgresKeys(pool), members_for(pool))


def a_key_id() -> str:
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
