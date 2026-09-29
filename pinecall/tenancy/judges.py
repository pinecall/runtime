"""The judges an org writes for one agent: a question each, asked of its calls at hang-up."""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import DictRow

from pinecall.domain.agent import AgentJudge
from pinecall.domain.errors import NotFound
from pinecall.postgres.pool import Pool

NOBODY = "{agent} has no judge called {name}"


JUDGES = """
SELECT name, question, runs_on, author, set_at
FROM agent_judges WHERE org = %(org)s AND agent = %(agent)s
ORDER BY name
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
    """An agent's judge as the org keeps it: who wrote it last, and when."""

    judge: AgentJudge
    author: str
    set_at: datetime


async def put_judge(pool: Pool, org: str, agent: str, written: AgentJudge, *, author: str) -> None:
    """Write one of the agent's judges whole: a new one, or the same name again."""
    values = {
        "org": org,
        "agent": agent,
        "name": written.name,
        "question": written.question,
        "runs_on": written.runs_on,
        "author": author,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_JUDGE, values)


async def drop_judge(pool: Pool, org: str, agent: str, name: str) -> None:
    """Forget one of the agent's judges; NotFound for a name nobody wrote."""
    async with pool.connection() as connection:
        dropped = await connection.execute(DROP_JUDGE, {"org": org, "agent": agent, "name": name})
        if await dropped.fetchone() is None:
            raise NotFound(NOBODY.format(agent=agent, name=name))


async def judges_of(pool: Pool, org: str, agent: str) -> list[StoredJudge]:
    """The agent's own judges, by name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(JUDGES, {"org": org, "agent": agent})).fetchall()
    return [_judge(row) for row in rows]


def _judge(row: DictRow) -> StoredJudge:
    judge = AgentJudge(name=row["name"], question=row["question"], runs_on=row["runs_on"])
    return StoredJudge(judge=judge, author=row["author"], set_at=row["set_at"])
