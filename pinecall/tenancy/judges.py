"""The judges an org writes: the org's, asked of every agent's calls, and one agent's own."""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import DictRow

from pinecall.domain.agent import PANEL_JUDGES, AgentJudge
from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.scope import THE_ORGS_OWN
from pinecall.postgres.pool import Pool

NOBODY = "{whose} has no judge called {name}"


TAKEN = "{name} is {whose} judge already: one name asks one question of a call"


A_PANELS = "{name} is a judge of the panel: a judge of your own takes another name"


NO_QUESTION = "a judge asks a question of a call: write one"


THE_ORG = "the org"


JUDGES = """
SELECT name, question, runs_on, author, set_at
FROM agent_judges WHERE org = %(org)s AND agent = %(agent)s
ORDER BY name
"""


# The org's rows first: their agent is '', which sorts before any slug.
FOR_CALL = """
SELECT name, question, runs_on, author, set_at
FROM agent_judges WHERE org = %(org)s AND agent IN ('', %(agent)s)
ORDER BY agent, name
"""


# A name is one question per call: the org's and an agent's may not share it, since a call of
# that agent would carry two verdicts under one name. Two agents may.
CLASH = """
SELECT agent FROM agent_judges
WHERE org = %(org)s AND name = %(name)s AND agent <> %(agent)s AND '' IN (agent, %(agent)s)
LIMIT 1
"""


PUT_JUDGE = """
INSERT INTO agent_judges (org, agent, name, question, runs_on, author)
VALUES (%(org)s, %(agent)s, %(name)s, %(question)s, %(runs_on)s, %(author)s)
ON CONFLICT (org, agent, name) DO UPDATE SET
    question = excluded.question, runs_on = excluded.runs_on, author = excluded.author,
    set_at = now()
"""


DROP_JUDGE = """
DELETE FROM agent_judges WHERE org = %(org)s AND agent = %(agent)s AND name = %(name)s
RETURNING name
"""


@dataclass(frozen=True)
class StoredJudge:
    """A judge as the org keeps it: who wrote it last, and when."""

    judge: AgentJudge
    author: str
    set_at: datetime


async def put_judge(pool: Pool, org: str, agent: str, written: AgentJudge, *, author: str) -> None:
    """Write a judge whole, the org's (agent '') or one agent's; the same name again replaces it."""
    if not written.question.strip():
        raise DeclarationRefused(NO_QUESTION)
    if written.name in PANEL_JUDGES:
        raise DeclarationRefused(A_PANELS.format(name=written.name))
    values = {
        "org": org,
        "agent": agent,
        "name": written.name,
        "question": written.question,
        "runs_on": written.runs_on,
        "author": author,
    }
    async with pool.connection() as connection, connection.transaction():
        clash = await (await connection.execute(CLASH, values)).fetchone()
        if clash is not None:
            raise Conflict(TAKEN.format(name=written.name, whose=_owner(str(clash["agent"]))))
        await connection.execute(PUT_JUDGE, values)


async def drop_judge(pool: Pool, org: str, agent: str, name: str) -> None:
    """Forget a judge of the org's or of one agent's; NotFound for a name nobody wrote there."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_JUDGE, {"org": org, "agent": agent, "name": name})
        if await dropped.fetchone() is None:
            raise NotFound(NOBODY.format(whose=_owner(agent), name=name))


async def judges_of(pool: Pool, org: str, agent: str) -> list[StoredJudge]:
    """The judges written at one level, the org's (agent '') or an agent's own, by name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(JUDGES, {"org": org, "agent": agent})).fetchall()
    return [_judge(row) for row in rows]


async def for_call(pool: Pool, org: str, agent: str) -> list[StoredJudge]:
    """Every judge a call of the agent is held to beside the panel: the org's, then its own."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(FOR_CALL, {"org": org, "agent": agent})).fetchall()
    return [_judge(row) for row in rows]


def _judge(row: DictRow) -> StoredJudge:
    judge = AgentJudge(name=row["name"], question=row["question"], runs_on=row["runs_on"])
    return StoredJudge(judge=judge, author=row["author"], set_at=row["set_at"])


def _owner(agent: str) -> str:
    """The owner of a judge as a sentence names it: the org, or the agent's slug."""
    return THE_ORG if agent == THE_ORGS_OWN else agent
