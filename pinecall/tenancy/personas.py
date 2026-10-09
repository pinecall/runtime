"""The personas an agent is called by in its simulations: who each caller is and how it talks."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.names import Json, JsonObject
from pinecall.postgres.pool import Pool

NOBODY = "no persona called {name} for {agent}"


TAKEN = "{agent} has a persona called {name} already"


PERSONAS = """
SELECT agent, name, about, goal, style, facts, state, llm, tts, voice, accepts_when,
       declines_when, author, set_at
FROM agent_personas WHERE org = %(org)s AND agent = %(agent)s
ORDER BY name
"""


# Every agent's callers, the org's whole roster: by agent, then by name.
EVERY_PERSONA = """
SELECT agent, name, about, goal, style, facts, state, llm, tts, voice, accepts_when,
       declines_when, author, set_at
FROM agent_personas WHERE org = %(org)s
ORDER BY agent, name
"""


PERSONA = """
SELECT agent, name, about, goal, style, facts, state, llm, tts, voice, accepts_when,
       declines_when, author, set_at
FROM agent_personas WHERE org = %(org)s AND agent = %(agent)s AND name = %(name)s
"""


# One statement, so a rename never leaves both names or neither. A `was` that is the same name,
# or none, deletes nothing.
PUT_PERSONA = """
WITH gone AS (
    DELETE FROM agent_personas
    WHERE org = %(org)s AND agent = %(agent)s AND name = %(was)s AND %(was)s <> %(name)s
    RETURNING name
)
INSERT INTO agent_personas (org, agent, name, about, goal, style, facts, state, author, llm, tts,
                            voice, accepts_when, declines_when)
VALUES (%(org)s, %(agent)s, %(name)s, %(about)s, %(goal)s, %(style)s, %(facts)s, %(state)s,
        %(author)s, %(llm)s, %(tts)s, %(voice)s, %(accepts_when)s, %(declines_when)s)
ON CONFLICT (org, agent, name) DO UPDATE SET
    about = excluded.about, goal = excluded.goal, style = excluded.style,
    facts = excluded.facts, state = excluded.state, llm = excluded.llm, tts = excluded.tts,
    voice = excluded.voice, accepts_when = excluded.accepts_when,
    declines_when = excluded.declines_when, author = excluded.author, set_at = now()
"""


DROP_PERSONA = """
DELETE FROM agent_personas WHERE org = %(org)s AND agent = %(agent)s AND name = %(name)s
RETURNING name
"""


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


@dataclass(frozen=True)
class StoredPersona:
    """A persona as the agent keeps it: whose it is, who wrote it last, and when."""

    agent: str
    persona: Persona
    author: str
    set_at: datetime


@dataclass(frozen=True)
class PersonaEdit:
    """Who writes a persona, and the name it had when the write renames it."""

    author: str
    was: str | None = None


async def put_persona(
    pool: Pool, org: str, agent: str, written: Persona, edit: PersonaEdit
) -> None:
    """Write one of the agent's callers whole: a new one, the same name again, or a rename."""
    was = edit.was
    if was is not None and was != written.name:
        if await persona(pool, org, agent, was) is None:
            raise NotFound(NOBODY.format(name=was, agent=agent))
        if await persona(pool, org, agent, written.name) is not None:
            raise Conflict(TAKEN.format(name=written.name, agent=agent))
    values = {
        **asdict(written),
        "facts": Jsonb(written.facts),
        "state": Jsonb(written.state),
        "org": org,
        "agent": agent,
        "author": edit.author,
        "was": was,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_PERSONA, values)


async def drop_persona(pool: Pool, org: str, agent: str, name: str) -> None:
    """Forget one of the agent's callers."""
    values = {"org": org, "agent": agent, "name": name}
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_PERSONA, values)
        if await dropped.fetchone() is None:
            raise NotFound(NOBODY.format(name=name, agent=agent))


async def persona(pool: Pool, org: str, agent: str, name: str) -> StoredPersona | None:
    """One of the agent's callers, by name."""
    values = {"org": org, "agent": agent, "name": name}
    async with pool.connection() as connection:
        row = await (await connection.execute(PERSONA, values)).fetchone()
    return None if row is None else _persona(row)


async def personas_of(pool: Pool, org: str, agent: str) -> list[StoredPersona]:
    """The agent's callers, by name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(PERSONAS, {"org": org, "agent": agent})).fetchall()
    return [_persona(row) for row in rows]


async def every_persona(pool: Pool, org: str) -> list[StoredPersona]:
    """Every agent's callers, by agent and then by name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(EVERY_PERSONA, {"org": org})).fetchall()
    return [_persona(row) for row in rows]


def _persona(row: DictRow) -> StoredPersona:
    agent = row.pop("agent")
    author = row.pop("author")
    set_at = row.pop("set_at")
    return StoredPersona(agent=agent, persona=Persona(**row), author=author, set_at=set_at)
