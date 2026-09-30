"""Admission: an org's limits in a world against what it used there, before it uses more."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.names import Env
from pinecall.domain.org import QuotaName, Quotas
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Connection, Pool
from pinecall.process import box_settings
from pinecall.tenancy import usage

REFUSED = "the org has used {used} of its {limit} {quota} in the {env}"

# Dollars are the same in both worlds: the month's spend in both is held to the one budget.
BUDGET_REFUSED = "the org has spent {used} of its {limit} USD budget this month"


ADMISSION = "admission"


QUOTAS = """
SELECT minutes, messages, agents, concurrent_calls, memory_facts, knowledge_chunks, numbers, seats,
       llm_tokens, hosted_apps, budget_usd, lends
FROM quotas WHERE org = %(org)s AND env = %(env)s
"""


# Replaced whole: a limit left out stops being one.
SET_QUOTAS = """
INSERT INTO quotas (org, env, minutes, messages, agents, concurrent_calls, memory_facts,
                    knowledge_chunks, numbers, seats, llm_tokens, hosted_apps, budget_usd, lends)
VALUES (%(org)s, %(env)s, %(minutes)s, %(messages)s, %(agents)s, %(concurrent_calls)s,
        %(memory_facts)s, %(knowledge_chunks)s, %(numbers)s, %(seats)s, %(llm_tokens)s,
        %(hosted_apps)s, %(budget_usd)s, %(lends)s)
ON CONFLICT (org, env) DO UPDATE SET
    minutes = excluded.minutes, messages = excluded.messages, agents = excluded.agents,
    concurrent_calls = excluded.concurrent_calls, memory_facts = excluded.memory_facts,
    knowledge_chunks = excluded.knowledge_chunks, numbers = excluded.numbers,
    seats = excluded.seats, llm_tokens = excluded.llm_tokens,
    hosted_apps = excluded.hosted_apps, budget_usd = excluded.budget_usd, lends = excluded.lends,
    set_at = now()
"""


@dataclass(frozen=True)
class Ceiling:
    """How long a call admitted may last: what is left of the org's minutes."""

    seconds: int
    minutes: int


class Admission(BaseModel):
    """What a newborn org may use in each world: a person's first org, and any later one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # A world the row does not name has no limits.
    first: dict[Env, Quotas] = Field(default_factory=dict[Env, Quotas])
    # None gives a later org what the first got; set, one trial per person.
    later: dict[Env, Quotas] | None = None


# `at` is the log's clock, so a call is held to the month its summary will be counted in.
async def admit_call(pool: Pool, org: str, env: Env, *, running: int, at: float) -> Ceiling | None:
    """Admit one more call, with its ceiling; None when the org's minutes have no limit."""
    quotas = await quotas_of(pool, org, env)
    _refuse_past(quotas, env, "concurrent_calls", running)
    month = usage.month_of(at)
    if quotas.budget_usd is not None:
        _refuse_over_budget(quotas, await usage.spent_in(pool, org, month))
    if quotas.minutes is None and quotas.messages is None and quotas.llm_tokens is None:
        return None
    spent = await usage.used(pool, org, env, month)
    _refuse_past(quotas, env, "minutes", spent.minutes)
    _refuse_past(quotas, env, "messages", spent.messages)
    _refuse_past(quotas, env, "llm_tokens", spent.input_tokens + spent.output_tokens)
    if quotas.minutes is None:
        return None
    # Never zero: the worker reads a zero ceiling as none.
    seconds = max(1, int((quotas.minutes - spent.minutes) * 60))
    return Ceiling(seconds=seconds, minutes=quotas.minutes)


# A call is counted at its summary, so a long written call adds what it has used so far.
async def admit_turn(pool: Pool, scope: Scope, *, turns: int, tokens: int, at: float) -> None:
    """Admit one more turn of a written call already this long."""
    org, env = scope.org, scope.env
    quotas = await quotas_of(pool, org, env)
    if quotas.messages is None and quotas.llm_tokens is None:
        return
    spent = await usage.used(pool, org, env, usage.month_of(at))
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


async def admit_hosted_app(pool: Pool, org: str, env: Env, *, hosting: int) -> None:
    """Admit one more app the box hosts for the org."""
    _refuse_past(await quotas_of(pool, org, env), env, "hosted_apps", hosting)


# Sized up front: a push that would pass the limit is refused whole.
async def admit_push(pool: Pool, org: str, env: Env, *, keeping: int) -> None:
    """Admit a push that leaves the org keeping this many knowledge chunks."""
    quotas = await quotas_of(pool, org, env)
    limit = quotas.exceeded("knowledge_chunks", keeping)
    if limit is not None:
        _refused(env, "knowledge_chunks", keeping, limit)


async def quotas_of(pool: Pool, org: str, env: Env) -> Quotas:
    """The org's limits in the world; none when nobody set them."""
    async with pool.connection() as connection:
        row = await (await connection.execute(QUOTAS, {"org": org, "env": env})).fetchone()
    if row is None:
        return Quotas()
    lends = row.pop("lends")
    return Quotas(**row, lends=None if lends is None else frozenset(lends))


async def set_quotas(pool: Pool, org: str, env: Env, quotas: Quotas) -> None:
    """Replace the org's limits in the world, whole."""
    async with pool.connection() as connection:
        await _set_quotas(connection, org, env, quotas)


async def admission(pool: Pool) -> Admission:
    """What a newborn org is given; nothing limited on a box that never said."""
    async with pool.connection() as connection:
        return await _admission(connection)


async def set_admission(pool: Pool, allowed: Admission) -> None:
    """Write what a newborn org is given, whole, as the console's box screen sends it."""
    async with pool.connection() as connection:
        await box_settings.write(connection, ADMISSION, allowed.model_dump(mode="json"))


async def give_first_quotas(connection: Connection, org: str, *, already: int) -> None:
    """Set a newborn org's quotas in each world, as the box's admission says for its person."""
    allowed = await _admission(connection)
    worlds = allowed.first if allowed.later is None or already == 0 else allowed.later
    for env, quotas in worlds.items():
        await _set_quotas(connection, org, env, quotas)


def _refuse_past(quotas: Quotas, env: Env, quota: QuotaName, spent: float) -> None:
    limit = quotas.reached(quota, spent)
    if limit is not None:
        _refused(env, quota, spent, limit)


def _refused(env: Env, quota: QuotaName, spent: float, limit: int) -> None:
    sentence = REFUSED.format(
        used=_shown(spent), limit=limit, quota=quota.replace("_", " "), env=env
    )
    raise QuotaExhausted(sentence, quota=quota, used=spent, limit=limit)


def _refuse_over_budget(quotas: Quotas, spent: float) -> None:
    limit = quotas.reached("budget_usd", spent)
    if limit is not None:
        sentence = BUDGET_REFUSED.format(used=_shown(spent), limit=limit)
        raise QuotaExhausted(sentence, quota="budget_usd", used=spent, limit=limit)


def _shown(spent: float) -> int | float:
    return int(spent) if float(spent).is_integer() else round(spent, 2)


async def _admission(connection: Connection) -> Admission:
    value = await box_settings.read(connection, ADMISSION)
    return Admission() if value is None else Admission.model_validate(value)


async def _set_quotas(connection: Connection, org: str, env: Env, quotas: Quotas) -> None:
    lends = None if quotas.lends is None else sorted(quotas.lends)
    await connection.execute(
        SET_QUOTAS,
        {"org": org, "env": env, **quotas.limits, "lends": lends},
    )
