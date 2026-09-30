"""Each distinct block of prompt an org's calls were told, kept once under its hash, read back."""

from collections import OrderedDict
from collections.abc import Collection

from pinecall.domain.agent import block_hash
from pinecall.postgres.pool import Pool

# The texts this process wrote, remembered so a block is written once and not per call; past it
# the oldest is forgotten and, met again, costs one insert that finds its row.
REMEMBERED = 10_000


KEPT = """
INSERT INTO prompts (org, hash, text) VALUES (%(org)s, %(hash)s, %(text)s)
ON CONFLICT (org, hash) DO NOTHING
"""


TEXTS = "SELECT hash, text FROM prompts WHERE org = %(org)s AND hash = ANY(%(hashes)s)"


# Not durable on purpose: the table is the record, this spares it a round trip.
class Prompts:
    """The blocks this gateway process has kept already, by org and hash."""

    def __init__(self) -> None:
        """Nothing kept yet."""
        self.kept: OrderedDict[tuple[str, str], None] = OrderedDict()

    async def keep(self, pool: Pool, org: str, text: str) -> None:
        """Keep the text under its hash for the org, once; an empty block is nothing to keep."""
        key = (org, block_hash(text))
        if not text or key in self.kept:
            return
        async with pool.connection() as connection:
            await connection.execute(KEPT, {"org": org, "hash": key[1], "text": text})
        self.kept[key] = None
        if len(self.kept) > REMEMBERED:
            self.kept.popitem(last=False)


async def texts_of(pool: Pool, org: str, hashes: Collection[str]) -> dict[str, str]:
    """The org's texts of these hashes, those it kept."""
    async with pool.connection() as connection:
        found = await connection.execute(TEXTS, {"org": org, "hashes": list(hashes)})
        rows = await found.fetchall()
    return {str(row["hash"]): str(row["text"]) for row in rows}
