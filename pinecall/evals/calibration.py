"""The judge judged: calls a person labelled, and each judge's agreement with those labels."""

from dataclasses import dataclass

from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool

# A judge is judged once this many of its verdicts have a label beside them, and trusted while it
# agrees with at least this share of them; under its line it is reported, never dropped silently.
LABELS_TO_JUDGE = 10
AGREES_AT_LEAST = 0.8

LABELLED = """
INSERT INTO judge_labels (call, judge, org, env, agent, held, note, author)
VALUES (%(call)s, %(judge)s, %(org)s, %(env)s, %(agent)s, %(held)s, %(note)s, %(author)s)
ON CONFLICT (call, judge) DO UPDATE SET
    held = excluded.held, note = excluded.note, author = excluded.author, labelled_at = now()
"""

# Each label beside the verdict the seal counted for the same judge on the same call, when one.
AGREEMENT = """
select label.judge,
       count(*) as labelled,
       count(verdict.held) as compared,
       count(*) filter (where verdict.held = label.held) as agreed
from judge_labels label
left join drift_calls counted on counted.call = label.call
left join lateral (
    select (item ->> 'held')::boolean as held
    from jsonb_array_elements(counted.verdicts) as item
    where item ->> 'judge' = label.judge
    limit 1
) verdict on true
where label.org = %(org)s and label.env = %(env)s
  and (%(agent)s::text is null or label.agent = %(agent)s)
group by label.judge
order by label.judge
"""


@dataclass(frozen=True)
class Label:
    """What a person said one judge should have answered on one call, and who said it."""

    call: str
    judge: str
    held: bool
    author: str
    note: str | None = None


@dataclass(frozen=True)
class Where:
    """Whose calls a label is on: the org, the world, the agent."""

    org: str
    env: Env
    agent: str


@dataclass(frozen=True)
class Agreement:
    """One judge against the labels: how many, how many had its verdict beside them, agreed."""

    judge: str
    labelled: int
    compared: int
    agreed: int

    @property
    def rate(self) -> float | None:
        """The share of compared labels its verdict agreed with; None before any compared."""
        return self.agreed / self.compared if self.compared else None

    @property
    def trusted(self) -> bool | None:
        """Whether it agrees at its line; None until enough labels were compared to say."""
        if self.compared < LABELS_TO_JUDGE or self.rate is None:
            return None
        return self.rate >= AGREES_AT_LEAST


async def labelled(pool: Pool, where: Where, label: Label) -> None:
    """Keep the label, replacing the one before it for the same call and judge."""
    params = {
        "call": label.call,
        "judge": label.judge,
        "org": where.org,
        "env": where.env,
        "agent": where.agent,
        "held": label.held,
        "note": label.note,
        "author": label.author,
    }
    async with pool.connection() as connection:
        await connection.execute(LABELLED, params)


async def agreement(pool: Pool, org: str, env: Env, agent: str | None) -> list[Agreement]:
    """Each judge against the labels on the org's calls in the world, one agent's or all."""
    params = {"org": org, "env": env, "agent": agent}
    async with pool.connection() as connection:
        rows = await (await connection.execute(AGREEMENT, params)).fetchall()
    return [
        Agreement(
            judge=str(row["judge"]),
            labelled=int(row["labelled"]),
            compared=int(row["compared"]),
            agreed=int(row["agreed"]),
        )
        for row in rows
    ]
