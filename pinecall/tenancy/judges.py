"""The judges an org writes, the org's and one agent's own, and which of the library's it runs."""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.scope import THE_ORGS_OWN
from pinecall.postgres.pool import Pool

NOBODY = "{whose} has no judge called {name}"


TAKEN = "{name} is {whose} judge already: one name asks one question of a call"


THE_ORG = "the org"


JUDGES = """
SELECT agent, name, question, answer, choices, runs_when, trigger, reads, author, set_at
FROM agent_judges WHERE org = %(org)s AND agent = %(agent)s
ORDER BY name
"""


# The org's rows first: their agent is '', which sorts before any slug.
FOR_CALL = """
SELECT agent, name, question, answer, choices, runs_when, trigger, reads, author, set_at
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
INSERT INTO agent_judges
    (org, agent, name, question, answer, choices, runs_when, trigger, reads, author)
VALUES (%(org)s, %(agent)s, %(name)s, %(question)s, %(answer)s, %(choices)s, %(runs_when)s,
        %(trigger)s, %(reads)s, %(author)s)
ON CONFLICT (org, agent, name) DO UPDATE SET
    question = excluded.question, answer = excluded.answer, choices = excluded.choices,
    runs_when = excluded.runs_when, trigger = excluded.trigger, reads = excluded.reads,
    author = excluded.author, set_at = now()
"""


DROP_JUDGE = """
DELETE FROM agent_judges WHERE org = %(org)s AND agent = %(agent)s AND name = %(name)s
RETURNING name
"""


# The agent's own row after the org's, so it wins when both switched the same judge.
SWITCHES = """
SELECT name, is_on FROM judge_switches
WHERE org = %(org)s AND agent IN ('', %(agent)s)
ORDER BY agent
"""


SWITCHED = """
SELECT name, is_on FROM judge_switches WHERE org = %(org)s AND agent = %(agent)s
"""


SWITCH = """
INSERT INTO judge_switches (org, agent, name, is_on, author)
VALUES (%(org)s, %(agent)s, %(name)s, %(is_on)s, %(author)s)
ON CONFLICT (org, agent, name) DO UPDATE SET
    is_on = excluded.is_on, author = excluded.author, set_at = now()
"""


@dataclass(frozen=True)
class Switched:
    """Library judges turned on or off by one person."""

    names: tuple[str, ...]
    on: bool
    author: str


@dataclass(frozen=True)
class StoredJudge:
    """A judge as the org keeps it: whose it is ('' the org's), who wrote it last, and when."""

    judge: JudgeSpec
    agent: str
    author: str
    set_at: datetime


async def put_judge(pool: Pool, org: str, agent: str, written: JudgeSpec, *, author: str) -> None:
    """Write a judge whole, the org's (agent '') or one agent's; the same name again replaces it."""
    values = {
        "org": org,
        "agent": agent,
        "name": written.name,
        "question": written.question,
        "answer": written.answer,
        "choices": Jsonb(list(written.choices)),
        "runs_when": written.on,
        "trigger": written.trigger,
        "reads": Jsonb(list(written.reads)),
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
    """Every judge of the org's a call of the agent is held to: the org's, then its own."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(FOR_CALL, {"org": org, "agent": agent})).fetchall()
    return [_judge(row) for row in rows]


async def switches_for(pool: Pool, org: str, agent: str) -> dict[str, bool]:
    """Which of the library's judges the agent's calls run, as the org and the agent set them."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(SWITCHES, {"org": org, "agent": agent})).fetchall()
    return {row["name"]: bool(row["is_on"]) for row in rows}


async def switched_at(pool: Pool, org: str, agent: str) -> dict[str, bool]:
    """The switches written at one level alone: the org's (agent '') or an agent's own."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(SWITCHED, {"org": org, "agent": agent})).fetchall()
    return {row["name"]: bool(row["is_on"]) for row in rows}


async def switch(pool: Pool, org: str, agent: str, switched: Switched) -> None:
    """Turn library judges on or off, for the org (agent '') or for one agent."""
    async with pool.connection() as connection, connection.transaction():
        for name in switched.names:
            values = {
                "org": org,
                "agent": agent,
                "name": name,
                "is_on": switched.on,
                "author": switched.author,
            }
            await connection.execute(SWITCH, values)


def _judge(row: DictRow) -> StoredJudge:
    reads = set(row["reads"])
    judge = JudgeSpec(
        name=row["name"],
        question=row["question"],
        answer=row["answer"],
        choices=tuple(row["choices"]),
        on=row["runs_when"],
        trigger=row["trigger"],
        reads_prompt="prompt" in reads,
        reads_evidence="evidence" in reads,
        reads_facts="facts" in reads,
    )
    return StoredJudge(judge=judge, agent=row["agent"], author=row["author"], set_at=row["set_at"])


def _owner(agent: str) -> str:
    """The owner of a judge as a sentence names it: the org, or the agent's slug."""
    return THE_ORG if agent == THE_ORGS_OWN else agent
