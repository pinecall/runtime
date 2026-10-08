"""The doctor's line on the WAL archive: Postgres's archiver and the spool the box ships from."""

from dataclasses import dataclass
from datetime import datetime

from pinecall.postgres.pool import Pool

# The timer ships every 10 s: five minutes of segments waiting is a bucket that does not answer.
SHIPPED_WITHIN_S = 300

OFF = "off: no restore to a minute; the postgres chart's backups section turns the archiver on"

NOT_SPOOLED = (
    "segment {wal} could not be spooled at {at:%Y-%m-%d %H:%M:%S}Z: Postgres keeps it in pg_wal "
    "and retries, and pg_wal grows until it can"
)

NOT_SHIPPED = (
    "{waiting} segments waiting since {at:%Y-%m-%d %H:%M:%S}Z: the bucket has not taken them "
    "(the postgres pod's logs say why); they stay on this disk until it does"
)

ON = "on, {waiting} waiting to ship, the last spooled {ago}"

ARCHIVER = """
SELECT current_setting('archive_mode') AS mode, last_archived_time, last_failed_time,
       last_failed_wal
FROM pg_stat_archiver
"""

# Read through Postgres, which has the spool mounted: the doctor's own user reads no WAL.
SPOOLED = """
SELECT count(*) AS waiting, min(file.modification) AS oldest
FROM pg_ls_dir(%(spool)s, true, false) AS name,
     LATERAL pg_stat_file(%(spool)s || '/' || name, true) AS file
WHERE NOT file.isdir AND name NOT LIKE '%%.part'
"""


@dataclass(frozen=True)
class Archive:
    """What Postgres says of its archiving, and what waits in the spool to leave the box."""

    mode: str
    last_archived: datetime | None
    last_failed: datetime | None
    failed_wal: str | None
    waiting: int
    oldest: datetime | None


# A box ships its WAL through a spool (PINECALL_WAL_SPOOL), read here through Postgres; a cluster's
# operator archives with none, and its spool is unset: the archiver's state alone is read.
async def archive_of(pool: Pool, spool: str | None) -> Archive:
    """Read the archiver's state and, when it is on and a spool is named, the spool's backlog."""
    # independent: two reads of the archiver's state, each its own snapshot
    async with pool.connection() as connection:
        row = await (await connection.execute(ARCHIVER)).fetchone()
        if row is None or row["mode"] == "off":
            return Archive("off", None, None, None, 0, None)
        spooled = None
        if spool:
            spooled = await (await connection.execute(SPOOLED, {"spool": spool})).fetchone()
    return Archive(
        mode=row["mode"],
        last_archived=row["last_archived_time"],
        last_failed=row["last_failed_time"],
        failed_wal=row["last_failed_wal"],
        waiting=0 if spooled is None else spooled["waiting"],
        oldest=None if spooled is None else spooled["oldest"],
    )


def archive_finding(archive: Archive, now: datetime) -> tuple[str | None, str]:
    """The trouble with the archive, or None, and the state an ok line says."""
    if archive.mode == "off":
        return None, OFF
    failed, archived = archive.last_failed, archive.last_archived
    if failed is not None and (archived is None or failed > archived):
        return NOT_SPOOLED.format(wal=archive.failed_wal, at=failed), ""
    oldest = archive.oldest
    if oldest is not None and (now - oldest).total_seconds() > SHIPPED_WITHIN_S:
        return NOT_SHIPPED.format(waiting=archive.waiting, at=oldest), ""
    ago = "never" if archived is None else f"{int((now - archived).total_seconds())} s ago"
    return None, ON.format(waiting=archive.waiting, ago=ago)
