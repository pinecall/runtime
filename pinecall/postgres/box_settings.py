"""The box_settings rows: what the operator configured, one JSON value per name."""

from psycopg.types.json import Jsonb

from pinecall.domain.names import JsonObject
from pinecall.postgres.pool import Connection

READ = "SELECT value FROM box_settings WHERE name = %(name)s"
WRITE = """
INSERT INTO box_settings (name, value) VALUES (%(name)s, %(value)s)
ON CONFLICT (name) DO UPDATE SET value = excluded.value, set_at = now()
"""


async def read(connection: Connection, name: str) -> JsonObject | None:
    """The row's value, or None when the operator never set it."""
    row = await (await connection.execute(READ, {"name": name})).fetchone()
    return None if row is None else row["value"]


async def write(connection: Connection, name: str, value: JsonObject) -> None:
    """Set the row's value, whole."""
    await connection.execute(WRITE, {"name": name, "value": Jsonb(value)})
