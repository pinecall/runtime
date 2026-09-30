"""An org's secrets in a world: the values its hosted apps are started with, kept sealed."""

import re
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import MultiFernet

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool
from pinecall.tenancy.vault import sealed

# An environment variable's name, as a shell takes it.
A_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


# What the box sets itself on every process it starts.
RESERVED = "PINECALL_"


LONGEST_VALUE = 16 * 1024


NOT_A_NAME = "{name!r} is not a secret's name: capitals, digits and underscores, like CRM_TOKEN"


IS_THE_BOXS = "{name} is the box's to set: a name starting with PINECALL_ is never an org's secret"


TOO_LONG = "a secret is {limit} KB at most"


NOT_SET = "the org has no secret called {name} in {env}"


SECRETS = """
SELECT name, set_by, set_at FROM org_secrets
WHERE org = %(org)s AND env = %(env)s ORDER BY name
"""


WRITE_ONE = """
INSERT INTO org_secrets (org, env, name, sealed, set_by)
VALUES (%(org)s, %(env)s, %(name)s, %(sealed)s, %(set_by)s)
ON CONFLICT (org, env, name) DO UPDATE SET
    sealed = excluded.sealed, set_by = excluded.set_by, set_at = now()
"""


DROP_ONE = """
DELETE FROM org_secrets WHERE org = %(org)s AND env = %(env)s AND name = %(name)s RETURNING name
"""


@dataclass(frozen=True)
class ListedSecret:
    """A secret as the org's list shows it: its name, who set it and when; never its value."""

    name: str
    set_by: str
    set_at: datetime


@dataclass(frozen=True)
class Secret:
    """One secret written: the world it is for, its name and its value."""

    env: Env
    name: str
    value: str

    def __post_init__(self) -> None:
        if not A_NAME.match(self.name):
            raise DeclarationRefused(NOT_A_NAME.format(name=self.name))
        if self.name.startswith(RESERVED):
            raise DeclarationRefused(IS_THE_BOXS.format(name=self.name))
        if len(self.value.encode()) > LONGEST_VALUE:
            raise DeclarationRefused(TOO_LONG.format(limit=LONGEST_VALUE >> 10))


async def put_secret(
    pool: Pool, vault: MultiFernet, org: str, secret: Secret, *, set_by: str
) -> None:
    """Keep the org's secret, sealed, replacing the value it had."""
    values = {
        "org": org,
        "env": secret.env,
        "name": secret.name,
        "sealed": sealed(vault, secret.value),
        "set_by": set_by,
    }
    async with pool.connection() as connection:
        await connection.execute(WRITE_ONE, values)


async def secrets_of(pool: Pool, org: str, env: Env) -> list[ListedSecret]:
    """The org's secrets in the world, by name: who set each and when, never a value."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(SECRETS, {"org": org, "env": env})).fetchall()
    return [
        ListedSecret(name=row["name"], set_by=row["set_by"], set_at=row["set_at"]) for row in rows
    ]


async def drop_secret(pool: Pool, org: str, env: Env, name: str) -> None:
    """Forget one of the org's secrets; NotFound for a name nobody set."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_ONE, {"org": org, "env": env, "name": name})
        if await dropped.fetchone() is None:
            raise NotFound(NOT_SET.format(name=name, env=env))
