"""The personas an org writes for its simulated callers, and the agents each may call."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.names import Json, JsonObject
from pinecall.postgres.pool import Pool

NOBODY = "no persona called {name} in this org"


TAKEN = "this org has a persona called {name} already"


# An agent reads the personas written for it and the ones written for every agent.
PERSONAS = """
SELECT name, about, goal, style, facts, state, llm, tts, voice, accepts_when, declines_when,
       agents, author, set_at
FROM agent_personas
WHERE org = %(org)s AND (%(agent)s::text IS NULL OR agents = '{}' OR %(agent)s = ANY(agents))
ORDER BY name
"""


PERSONA = """
SELECT name, about, goal, style, facts, state, llm, tts, voice, accepts_when, declines_when,
       agents, author, set_at
FROM agent_personas WHERE org = %(org)s AND name = %(name)s
"""


# One statement, so a rename never leaves both names or neither. A `was` that is the same name,
# or none, deletes nothing.
PUT_PERSONA = """
WITH gone AS (
    DELETE FROM agent_personas WHERE org = %(org)s AND name = %(was)s AND %(was)s <> %(name)s
    RETURNING name
)
INSERT INTO agent_personas (org, name, about, goal, style, facts, state, author, llm, tts,
                            voice, accepts_when, declines_when, agents)
VALUES (%(org)s, %(name)s, %(about)s, %(goal)s, %(style)s, %(facts)s, %(state)s, %(author)s,
        %(llm)s, %(tts)s, %(voice)s, %(accepts_when)s, %(declines_when)s, %(agents)s)
ON CONFLICT (org, name) DO UPDATE SET
    about = excluded.about, goal = excluded.goal, style = excluded.style,
    facts = excluded.facts, state = excluded.state, llm = excluded.llm, tts = excluded.tts,
    voice = excluded.voice, accepts_when = excluded.accepts_when,
    declines_when = excluded.declines_when, agents = excluded.agents, author = excluded.author,
    set_at = now()
"""


DROP_PERSONA = "DELETE FROM agent_personas WHERE org = %(org)s AND name = %(name)s RETURNING name"


@dataclass(frozen=True)
class Persona:
    """A caller an eval plays: who they are, what they want, how they talk, what they know."""

    name: str
    goal: str
    style: str
    about: str = ""
    facts: dict[str, str] = field(default_factory=dict[str, str])
    state: JsonObject = field(default_factory=dict[str, Json])
    # The vendors the caller is played on; None is the runtime's.
    llm: str | None = None
    tts: str | None = None
    voice: str | None = None
    accepts_when: str = ""
    declines_when: str = ""
    # The agents it may call; empty is every agent of the org.
    agents: frozenset[str] = frozenset()

    def calls(self, agent: str) -> bool:
        """Whether this caller may call the agent."""
        return not self.agents or agent in self.agents


@dataclass(frozen=True)
class StoredPersona:
    """A persona as the org keeps it: who wrote it last, and when."""

    persona: Persona
    author: str
    set_at: datetime


async def put_persona(
    pool: Pool, org: str, written: Persona, *, author: str, was: str | None = None
) -> None:
    """Write a caller whole: a new one, the same name again, or a rename from `was`."""
    if was is not None and was != written.name:
        if await persona(pool, org, was) is None:
            raise NotFound(NOBODY.format(name=was))
        if await persona(pool, org, written.name) is not None:
            raise Conflict(TAKEN.format(name=written.name))
    values = {
        **asdict(written),
        "facts": Jsonb(written.facts),
        "state": Jsonb(written.state),
        "agents": sorted(written.agents),
        "org": org,
        "author": author,
        "was": was,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_PERSONA, values)


async def drop_persona(pool: Pool, org: str, name: str) -> None:
    """Forget a caller of the org."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_PERSONA, {"org": org, "name": name})
        if await dropped.fetchone() is None:
            raise NotFound(NOBODY.format(name=name))


async def persona(pool: Pool, org: str, name: str) -> StoredPersona | None:
    """One caller of the org, by name."""
    async with pool.connection() as connection:
        row = await (await connection.execute(PERSONA, {"org": org, "name": name})).fetchone()
    return None if row is None else _persona(row)


async def personas_of(pool: Pool, org: str, *, agent: str | None = None) -> list[StoredPersona]:
    """The org's callers by name: every one, or the ones this agent may be called by."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(PERSONAS, {"org": org, "agent": agent})).fetchall()
    return [_persona(row) for row in rows]


def _persona(row: DictRow) -> StoredPersona:
    author = row.pop("author")
    set_at = row.pop("set_at")
    agents = frozenset(row.pop("agents"))
    return StoredPersona(persona=Persona(**row, agents=agents), author=author, set_at=set_at)
