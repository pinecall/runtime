"""How many times a name knocked in the last minute, counted in Postgres for every gateway."""

import time
from collections.abc import Callable

from pinecall.postgres.pool import Pool

WINDOW_S = 60.0

# One name's knocks are counted and taken one at a time, whichever gateway they land on.
ONE_AT_A_TIME = "select pg_advisory_xact_lock(hashtext('knock:' || %(name)s))"

# A refused knock is not counted: the name waits for its oldest knock to leave the window.
KNOCKED = """
insert into knocks (name, at)
select %(name)s, %(now)s
where (select count(*) from knocks where name = %(name)s and at > %(since)s) < %(tries)s
returning at
"""

GONE = "delete from knocks where at <= %(since)s"


class Throttle:
    """So many knocks a minute per name, and the next one waits: across every gateway."""

    def __init__(self, pool: Pool, tries: int, clock: Callable[[], float] = time.time) -> None:
        """A throttle of so many tries a minute."""
        self.pool = pool
        self.tries = tries
        self.clock = clock

    async def allowed(self, name: str) -> bool:
        """Count a knock, and say whether it is within the window's tries."""
        now = self.clock()
        wanted = {"name": name, "now": now, "since": now - WINDOW_S, "tries": self.tries}
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(ONE_AT_A_TIME, wanted)
            await connection.execute(GONE, wanted)
            return await (await connection.execute(KNOCKED, wanted)).fetchone() is not None
