"""What a new sandbox starts with, from production: personas, sandbox knowledge and tuning."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from pinecall.db import Pool
from pinecall.orgs.records_postgres import PostgresOrgs
from pinecall.types import Org


# The rows the sandbox was, inside production, before it was an instance of its own — and only
# those: what a developer set up to work with. Never a member (they arrive at their first sign-in,
# mirrored from production), a key (the sandbox mints its own), a call or a memory (the log of what
# happened, which stays where it happened), nor an org's quotas (no limit is no row, here as
# everywhere). Personas carry no world: they are the org's, and simulations run in the sandbox.
@dataclass(frozen=True)
class Copied:
    """One table the seed copies, and whether only its sandbox rows are the sandbox's."""

    table: str
    sandbox_only: bool = True

    def where(self) -> str:
        """The rows of one org this table gives the sandbox."""
        return "org = $1" + (" AND env = 'sandbox'" if self.sandbox_only else "")


# In the order the foreign keys want them: a base before its files and its chunks.
TABLES: tuple[Copied, ...] = (
    Copied("agent_personas", sandbox_only=False),
    Copied("knowledge_bases"),
    Copied("knowledge_files"),
    Copied("knowledge_chunks"),
    Copied("agent_config"),
    Copied("lexicon"),
)

ORGS_SAID = "orgs: {mirrored} mirrored, {refused} refused"
REFUSED = "  {slug}: another org holds the slug in the sandbox, and nothing of it is copied"
TABLE_SAID = "{table}: {copied} copied, {kept} already there"


# Every row travels as JSON and lands through the target's own row type, so each column — a
# halfvec, a uuid, a timestamp — is read and written by its own type's text form, with no codec
# of ours in between and no column list to keep in step with the migrations: both databases ran
# the same ones. ON CONFLICT DO NOTHING is the idempotence: a second run copies nothing twice, and
# never overwrites what the sandbox has changed since.
def _rows_of(copied: Copied) -> str:
    return (
        "SELECT count(*) AS total, coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text AS rows "
        f"FROM {copied.table} t WHERE {copied.where()}"
    )


def _added_to(copied: Copied) -> str:
    return (
        f"WITH added AS (INSERT INTO {copied.table} "
        f"SELECT * FROM jsonb_populate_recordset(NULL::{copied.table}, $1::text::jsonb) "
        "ON CONFLICT DO NOTHING RETURNING 1) SELECT count(*) AS added FROM added"
    )


_ORGS_WITH_ANY = " UNION ".join(
    f"SELECT org FROM {copied.table}" + (" WHERE env = 'sandbox'" if copied.sandbox_only else "")
    for copied in TABLES
)
_ORGS = f"SELECT id, slug, name FROM orgs WHERE id IN ({_ORGS_WITH_ANY}) ORDER BY created_at, id"


async def seed(source: Pool, target: Pool, out: TextIO) -> int:
    """Every org that has any of it, mirrored, then its rows, table by table; one line each."""
    orgs = [
        Org(id=row["id"], slug=row["slug"], name=row["name"]) for row in await source.fetch(_ORGS)
    ]
    seeded = await _mirrored(orgs, target, out)
    for copied in TABLES:
        copied_n, kept = 0, 0
        for org in seeded:
            read = await source.fetchrow(_rows_of(copied), org.id)
            if read is None or not read["total"]:
                continue
            total = int(read["total"])
            added = await target.fetchrow(_added_to(copied), str(read["rows"]))
            done = 0 if added is None else int(added["added"])
            copied_n, kept = copied_n + done, kept + total - done
        print(TABLE_SAID.format(table=copied.table, copied=copied_n, kept=kept), file=out)
    return 0


# By production's id, exactly as a sign-in mirrors one (Orgs.mirrored): the slug `pinecall link`
# wrote means the same org on both instances. One the sandbox holds under another id is said and
# left alone, with everything of it.
async def _mirrored(orgs: Sequence[Org], target: Pool, out: TextIO) -> list[Org]:
    """The orgs the sandbox now holds by production's ids; the refused ones said by slug."""
    kept = PostgresOrgs(target)
    seeded: list[Org] = []
    for org in orgs:
        if await kept.mirrored(org) is None:
            print(REFUSED.format(slug=org.slug), file=out)
        else:
            seeded.append(org)
    print(ORGS_SAID.format(mirrored=len(seeded), refused=len(orgs) - len(seeded)), file=out)
    return seeded
