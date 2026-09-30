"""What a call's log keeps of the values its agent declared private: masked, and sealed aside."""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.fernet import InvalidToken, MultiFernet

from pinecall.domain.agent import AgentConfig
from pinecall.domain.names import Json, JsonObject
from pinecall.log.store import Store
from pinecall.wire.frames import Entry

logger = logging.getLogger(__name__)


# The key stays, so a reader knows a value exists; the value goes whole, so its type does not leak.
MASK = "***"


# The entries whose data carries the app's state, masked against the state fields declared pii.
CARRY_STATE = frozenset({"state.changed", "call.attached"})


# A tool's arguments, masked against the names its declaration lists under pii.
CARRY_ARGUMENTS = "tool.call"


# One row per entry that held a private value: the values, sealed under the box's vault key.
KEEP = """
INSERT INTO call_private (log, seq, sealed)
SELECT %(log)s, seq, sealed FROM unnest(%(seqs)s::bigint[], %(sealed)s::text[]) AS kept(seq, sealed)
ON CONFLICT (log, seq) DO NOTHING
"""

OF_LOG = "SELECT seq, sealed FROM call_private WHERE log = %(log)s"


@dataclass(frozen=True)
class Privacy:
    """What a call's writer masks by: the vault it seals with, the declaration it reads."""

    vault: MultiFernet
    config: AgentConfig


@dataclass(frozen=True)
class Split:
    """An entry's data as the log keeps it, and the values masked in it, by their field."""

    kept: JsonObject
    private: JsonObject


def split(kind: str, data: JsonObject, config: AgentConfig) -> Split:
    """The entry with every value its agent declared private masked, and those values apart."""
    marked = _marked(kind, data, config)
    if marked is None:
        return Split(data, {})
    field, names = marked
    values = data.get(field)
    if not isinstance(values, dict):
        return Split(data, {})
    private: JsonObject = {name: value for name, value in values.items() if name in names}
    if not private:
        return Split(data, {})
    return Split({**data, field: {**values, **dict.fromkeys(private, MASK)}}, {field: private})


def masked_state(app_state: JsonObject, config: AgentConfig) -> JsonObject:
    """The app's state with every field declared pii masked."""
    return {
        name: MASK if config.visibility_of(name) == "pii" else value
        for name, value in app_state.items()
    }


def restored(entry: Entry, private: JsonObject | None) -> Entry:
    """The entry as its writer wrote it: the private values back where they were masked."""
    if not private:
        return entry
    data = dict(entry.data)
    for field, values in private.items():
        kept = data.get(field)
        if isinstance(kept, dict) and isinstance(values, dict):
            data[field] = {**kept, **values}
    return entry.model_copy(update={"data": data})


async def kept_aside(
    store: Store, vault: MultiFernet, log: str, private: Sequence[tuple[int, JsonObject]]
) -> None:
    """Seal the private values of these entries of the log, by seq; one kept already stays."""
    if not private:
        return
    rows = {
        "log": log,
        "seqs": [seq for seq, _ in private],
        "sealed": [vault.encrypt(json.dumps(values).encode()).decode() for _, values in private],
    }
    async with store.pool.connection() as connection:
        await connection.execute(KEEP, rows)


# Read only where a call is taken up: an app taking it over, a written call after a restart.
async def opened_whole(store: Store, vault: MultiFernet, log: str) -> list[Entry]:
    """Every entry of the log as its writer wrote it, the private values opened."""
    entries = await store.whole(log)
    async with store.pool.connection() as connection:
        rows = await (await connection.execute(OF_LOG, {"log": log})).fetchall()
    sealed = {int(row["seq"]): str(row["sealed"]) for row in rows}
    return [restored(entry, _opened(vault, sealed.get(entry.seq))) for entry in entries]


# A value sealed under a key PINECALL_VAULT_KEY no longer lists stays masked: the call goes on.
def _opened(vault: MultiFernet, token: str | None) -> JsonObject | None:
    if token is None:
        return None
    try:
        values: Json = json.loads(vault.decrypt(token.encode()))
    except InvalidToken:
        logger.warning("a private value sealed under a key the vault no longer lists stays masked")
        return None
    return values if isinstance(values, dict) else None


def _marked(kind: str, data: JsonObject, config: AgentConfig) -> tuple[str, frozenset[str]] | None:
    if kind == CARRY_ARGUMENTS:
        spec = config.tools_by_name.get(str(data.get("name")))
        return None if spec is None else ("arguments", spec.pii)
    if kind in CARRY_STATE:
        declared = frozenset(name for name, seen in config.state_fields.items() if seen == "pii")
        return "state", declared
    return None
