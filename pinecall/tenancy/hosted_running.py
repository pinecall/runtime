"""A hosted app while it runs: stopped and started, the logs asked for, the time it served."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import LiteralString

from pinecall.domain.errors import NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy.hosting import NOT_HOSTED, HostedApp

# A gap between two beats longer than this is a runner that was away: it counts this much.
LONGEST_BEAT_S = 30.0


# What a person reads of a process's output: its last lines, and no more than this.
LONGEST_LOGS = 64 * 1024


STOP = """
UPDATE hosted_apps SET stopped_at = now(), stopped_by = %(by)s
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s RETURNING name
"""


START = """
UPDATE hosted_apps SET stopped_at = NULL, stopped_by = ''
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s RETURNING name
"""


ASK_LOGS = """
UPDATE hosted_apps SET logs_asked_at = now()
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
RETURNING logs, logs_host, logs_at
"""


KEEP_LOGS = """
UPDATE hosted_apps SET logs = %(lines)s, logs_host = %(host)s, logs_at = now()
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
"""


# Read under a lock and written in the same transaction, so two beats never count one span twice.
LAST_METERED = """
SELECT metered_at FROM hosted_apps
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s FOR UPDATE
"""


METER = """
UPDATE hosted_apps SET metered_at = %(now)s
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s
"""


ADD_SECONDS = """
INSERT INTO hosted_usage (org, env, name, day, seconds)
VALUES (%(org)s, %(env)s, %(name)s, %(day)s, %(seconds)s)
ON CONFLICT (org, env, name, day) DO UPDATE SET seconds = hosted_usage.seconds + excluded.seconds
"""


UNMETER = """
UPDATE hosted_apps SET metered_at = NULL
WHERE org = %(org)s AND env = %(env)s AND name = %(name)s AND metered_at IS NOT NULL
"""


USAGE = """
SELECT org, env, name, day, seconds FROM hosted_usage
WHERE day >= %(since)s AND day < %(until)s AND (%(org)s::text IS NULL OR org = %(org)s)
  AND (%(env)s::text IS NULL OR env = %(env)s)
ORDER BY org, env, day, name
"""


@dataclass(frozen=True)
class Logs:
    """The last lines of an app's process as the runner read them; empty before the first read."""

    host: str | None
    lines: str
    at: datetime | None


@dataclass(frozen=True)
class Served:
    """The time an app served on one UTC day, in seconds."""

    org: str
    env: str
    name: str
    day: date
    seconds: float


async def stop_app(pool: Pool, app: HostedApp, *, by: str) -> None:
    """Stop running the app: the runner drains it, and its releases and token stay."""
    await _changed(pool, STOP, {**app.columns, "by": by}, app)


async def start_app(pool: Pool, app: HostedApp) -> None:
    """Run a stopped app again, its newest release."""
    await _changed(pool, START, app.columns, app)


async def ask_for_logs(pool: Pool, app: HostedApp) -> Logs:
    """What the runner last sent of the app's output; the runner is told to send it again."""
    async with pool.connection() as connection:
        row = await (await connection.execute(ASK_LOGS, app.columns)).fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
    return Logs(host=row["logs_host"], lines=row["logs"], at=row["logs_at"])


async def keep_logs(pool: Pool, app: HostedApp, *, host: str, lines: str) -> None:
    """Keep what the runner read of the app's output, its last part when it is long."""
    values = {**app.columns, "host": host, "lines": lines[-LONGEST_LOGS:]}
    async with pool.connection() as connection:
        await connection.execute(KEEP_LOGS, values)


async def metered(pool: Pool, app: HostedApp, now: datetime) -> float:
    """Count the app's time serving since it was last counted, on today's UTC row; the seconds."""
    async with pool.connection() as connection, connection.transaction():
        row = await (await connection.execute(LAST_METERED, app.columns)).fetchone()
        await connection.execute(METER, {**app.columns, "now": now})
        previous = None if row is None else row["metered_at"]
        if previous is None:
            return 0.0
        seconds = min(max((now - previous).total_seconds(), 0.0), LONGEST_BEAT_S)
        day = now.astimezone(UTC).date()
        await connection.execute(ADD_SECONDS, {**app.columns, "day": day, "seconds": seconds})
    return seconds


async def unmetered(pool: Pool, app: HostedApp) -> None:
    """The app is not serving: the next time it is, counting starts from then."""
    async with pool.connection() as connection:
        await connection.execute(UNMETER, app.columns)


async def served(
    pool: Pool, since: date, until: date, *, org: str | None = None, env: str | None = None
) -> list[Served]:
    """The time apps served per UTC day in [since, until), one org's or every org's."""
    values = {"since": since, "until": until, "org": org, "env": env}
    async with pool.connection() as connection:
        rows = await (await connection.execute(USAGE, values)).fetchall()
    return [
        Served(
            org=row["org"], env=row["env"], name=row["name"], day=row["day"], seconds=row["seconds"]
        )
        for row in rows
    ]


async def _changed(
    pool: Pool, statement: LiteralString, values: dict[str, str], app: HostedApp
) -> None:
    async with pool.connection() as connection:
        row = await (await connection.execute(statement, values)).fetchone()
    if row is None:
        raise NotFound(NOT_HOSTED.format(name=app.name, env=app.env))
