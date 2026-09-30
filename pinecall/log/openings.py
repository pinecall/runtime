"""What a call is, kept at its open: its context and its config, for any gateway to serve it."""

import json
from dataclasses import dataclass
from hashlib import sha256

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import CallContext
from pinecall.postgres.pool import Pool

KEPT_CONFIG = """
INSERT INTO call_configs (org, hash, config) VALUES (%(org)s, %(hash)s, %(config)s)
ON CONFLICT (org, hash) DO NOTHING
"""

# The first opening stands: a reopen of the same call says it again and changes nothing.
KEPT_OPENING = """
INSERT INTO call_openings (call, org, context, config_hash)
VALUES (%(call)s, %(org)s, %(context)s, %(hash)s)
ON CONFLICT (call) DO NOTHING
"""

OPENING = """
SELECT opening.context, config.config
FROM call_openings opening
JOIN call_configs config ON config.org = opening.org AND config.hash = opening.config_hash
WHERE opening.call = %(call)s
"""

_CONTEXT: TypeAdapter[CallContext] = TypeAdapter(CallContext)
_CONFIG: TypeAdapter[AgentConfig] = TypeAdapter(AgentConfig)


@dataclass(frozen=True)
class Opening:
    """A call as it opened: what the worker said of it, and the config it runs on."""

    context: CallContext
    config: AgentConfig


async def kept(pool: Pool, org: str, context: CallContext, config: AgentConfig) -> None:
    """Keep what the call is, in one transaction: its config once per hash, its opening once."""
    written = _CONFIG.dump_python(config, mode="json")
    hashed = sha256(json.dumps(written, sort_keys=True).encode()).hexdigest()
    opening = {
        "call": context.call,
        "org": org,
        "context": Jsonb(_CONTEXT.dump_python(context, mode="json")),
        "hash": hashed,
    }
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(
            KEPT_CONFIG, {"org": org, "hash": hashed, "config": Jsonb(written)}
        )
        await connection.execute(KEPT_OPENING, opening)


async def opening_of(pool: Pool, call: str) -> Opening | None:
    """What the call is, as it was kept when it opened; None for one opened before it was kept."""
    async with pool.connection() as connection:
        row = await (await connection.execute(OPENING, {"call": call})).fetchone()
    if row is None:
        return None
    return Opening(
        context=_CONTEXT.validate_python(row["context"]),
        config=_CONFIG.validate_python(row["config"]),
    )
