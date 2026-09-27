"""Admission: an org's limits in a world against what it used there, before it uses more."""

from dataclasses import dataclass

from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.types import Env, QuotaName, Quotas
from pinecall.log.reduce import Metered, Usage, usage_row
from pinecall.log.store import entry_of
from pinecall.postgres.pool import Pool
from pinecall.tenancy.orgs import quotas_of

REFUSED = "the org has used {used} of its {limit} {quota} in the {env}"

# Every call.summary the org's calls wrote in the world, whenever they were: what the limits are
# counted against. Nothing is kept in memory, so a restart counts what the database holds.
SUMMARIES = """
SELECT entry.call, entry.seq, entry.ts, entry.agent, entry.type, entry.ephemeral, entry.data
FROM call_log_head head JOIN call_log entry ON entry.log = head.log
WHERE head.org = %(org)s AND head.env = %(env)s AND entry.type = 'call.summary'
"""


@dataclass(frozen=True)
class Ceiling:
    """How long a call admitted may last: what is left of the org's minutes."""

    seconds: int
    minutes: int


async def used(pool: Pool, org: str, env: Env) -> Usage:
    """What the org's calls in the world consumed: minutes, turns, tokens, characters, cost."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(SUMMARIES, {"org": org, "env": env})).fetchall()
    total = Usage()
    for row in rows:
        total += usage_row(Metered(position=0, org=org, entry=entry_of(row))).used
    return total


async def admit_call(pool: Pool, org: str, env: Env, *, running: int) -> Ceiling | None:
    """Admit one more call, with its ceiling; None when the org's minutes have no limit."""
    quotas = await quotas_of(pool, org, env)
    _refuse_past(quotas, env, "concurrent_calls", running)
    if quotas.minutes is None and quotas.messages is None and quotas.llm_tokens is None:
        return None
    spent = await used(pool, org, env)
    _refuse_past(quotas, env, "minutes", spent.minutes)
    _refuse_past(quotas, env, "messages", spent.messages)
    _refuse_past(quotas, env, "llm_tokens", spent.input_tokens + spent.output_tokens)
    if quotas.minutes is None:
        return None
    # Never zero: the worker reads a zero ceiling as none.
    seconds = max(1, int((quotas.minutes - spent.minutes) * 60))
    return Ceiling(seconds=seconds, minutes=quotas.minutes)


# A call is counted at its summary, so a long written call adds what it has used so far.
async def admit_turn(pool: Pool, org: str, env: Env, *, turns: int, tokens: int) -> None:
    """Admit one more turn of a written call already this long."""
    quotas = await quotas_of(pool, org, env)
    if quotas.messages is None and quotas.llm_tokens is None:
        return
    spent = await used(pool, org, env)
    _refuse_past(quotas, env, "messages", spent.messages + turns)
    _refuse_past(quotas, env, "llm_tokens", spent.input_tokens + spent.output_tokens + tokens)


async def admit_agent(pool: Pool, org: str, env: Env, *, holding: int) -> None:
    """Admit one more agent held by the org's apps."""
    _refuse_past(await quotas_of(pool, org, env), env, "agents", holding)


async def admit_number(pool: Pool, org: str, env: Env, *, bought: int) -> None:
    """Admit one more number bought on the box's carrier account for the org."""
    _refuse_past(await quotas_of(pool, org, env), env, "numbers", bought)


async def admit_memory(pool: Pool, org: str, env: Env, *, kept: int) -> None:
    """Admit one more fact remembered about a contact."""
    _refuse_past(await quotas_of(pool, org, env), env, "memory_facts", kept)


async def admit_seat(pool: Pool, org: str, env: Env, *, seated: int) -> None:
    """Admit one more person seated in the org."""
    _refuse_past(await quotas_of(pool, org, env), env, "seats", seated)


# Sized up front: a push that would pass the limit is refused whole.
async def admit_push(pool: Pool, org: str, env: Env, *, keeping: int) -> None:
    """Admit a push that leaves the org keeping this many knowledge chunks."""
    quotas = await quotas_of(pool, org, env)
    limit = quotas.exceeded("knowledge_chunks", keeping)
    if limit is not None:
        _refused(env, "knowledge_chunks", keeping, limit)


def _refuse_past(quotas: Quotas, env: Env, quota: QuotaName, spent: float) -> None:
    limit = quotas.reached(quota, spent)
    if limit is not None:
        _refused(env, quota, spent, limit)


def _refused(env: Env, quota: QuotaName, spent: float, limit: int) -> None:
    shown = int(spent) if float(spent).is_integer() else round(spent, 2)
    sentence = REFUSED.format(used=shown, limit=limit, quota=quota.replace("_", " "), env=env)
    raise QuotaExhausted(sentence, quota=quota, used=spent, limit=limit)
