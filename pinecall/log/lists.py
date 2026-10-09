"""The pages of calls a list asks for: by agent, channel and words, and a persona's own runs."""

from dataclasses import asdict, dataclass, field

from psycopg import sql

from pinecall.domain.scope import Scope
from pinecall.log.facts import CallFacts, facts_of
from pinecall.postgres.pool import Pool

# words is an escaped LIKE pattern and digits its digits; a call with no facts row still matches
# a filter on the agent alone.
_MATCHING = sql.SQL("""
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.call is not null
  and head.env = %(env)s and head.holder = %(holder)s
  and (%(agent)s::text is null or head.agent = %(agent)s)
  and (%(channel)s::text is null or f.channel = %(channel)s)
  and (%(words)s::text is null
       or lower(head.log) like lower(%(words)s) || '%%'
       or (%(digits)s::text <> '' and (regexp_replace(coalesce(f.from_number, ''), '\\D', '', 'g')
                                   like '%%' || %(digits)s || '%%'
                              or regexp_replace(coalesce(f.to_number, ''), '\\D', '', 'g')
                                   like '%%' || %(digits)s || '%%'))
       or f.name ilike '%%' || %(words)s || '%%'
       or f.outcome ilike '%%' || %(words)s || '%%')
""")


# The page before this one is the cursor: a call id, whose start time and id bound the page.
_A_PAGE_BEFORE = sql.SQL("""
  and (%(before)s::text is null or (coalesce(head.started_at, -1), head.log) < (
        select coalesce(before.started_at, -1), before.log
        from call_log_head before where before.log = %(before)s))
order by coalesce(head.started_at, -1) desc, head.log desc
limit %(limit)s
""")


# A persona's runs to one agent, or — neither named — every simulated call of the scope.
_THE_PERSONAS_RUNS = sql.SQL("""
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and f.persona is not null
  and (%(agent)s::text is null or head.agent = %(agent)s)
  and (%(persona)s::text is null or f.persona = %(persona)s)
""")


@dataclass(frozen=True, slots=True)
class ListFilters:
    """What a call list asks for: an agent, a channel, words, and the page before this one."""

    agent: str | None = None
    # Matched case-insensitively against the call id's start, either number's digits, the
    # caller's name and the outcome.
    q: str | None = None
    channel: str | None = None
    before: str | None = None


@dataclass(frozen=True, slots=True)
class Found:
    """One page of calls, newest first, the total and the cursor of the next page."""

    calls: list[str]
    total: int
    next: str | None


@dataclass(frozen=True, slots=True)
class PersonaRunFilters:
    """Whose runs a list asks for: an agent, a persona, the page before; neither is every run."""

    agent: str | None = None
    persona: str | None = None
    before: str | None = None


@dataclass(frozen=True, slots=True)
class PersonaRun:
    """One simulated call by a persona: when it started, its facts, how many turns it took."""

    started_at: float
    facts: CallFacts
    turns: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "turns", len(self.facts.heard_at))


@dataclass(frozen=True, slots=True)
class PersonaRuns:
    """One page of a persona's runs, newest first, the total and the cursor of the next page."""

    runs: list[PersonaRun]
    total: int
    next: str | None


FOUND_COUNT = sql.SQL("select count(*) as total ") + _MATCHING


FOUND_PAGE = sql.SQL("select head.log as call ") + _MATCHING + _A_PAGE_BEFORE


PERSONA_RUNS_COUNT = sql.SQL("select count(*) as total ") + _THE_PERSONAS_RUNS


PERSONA_RUNS_PAGE = (
    sql.SQL("select f.*, head.agent, coalesce(head.started_at, -1) as started_at ")
    + _THE_PERSONAS_RUNS
    + _A_PAGE_BEFORE
)


async def found(pool: Pool, scope: Scope, wanted: ListFilters, *, limit: int) -> Found:
    """Return one page of the scope's calls the filter matches, newest first, and their total."""
    params = {
        **asdict(scope),
        "agent": wanted.agent,
        "channel": wanted.channel,
        "words": None if wanted.q is None else _like_escaped(wanted.q),
        "digits": "".join(item for item in (wanted.q or "") if item.isdigit()),
    }
    # independent: the total and the page are read apart, as a list always was
    async with pool.connection() as connection:
        total = await (await connection.execute(FOUND_COUNT, params)).fetchone()
        page = {**params, "before": wanted.before, "limit": limit + 1}
        rows = await (await connection.execute(FOUND_PAGE, page)).fetchall()
    calls = [str(row["call"]) for row in rows]
    return Found(
        calls=calls[:limit],
        total=0 if total is None else int(total["total"]),
        next=calls[limit - 1] if len(calls) > limit else None,
    )


async def runs_of_persona(
    pool: Pool, scope: Scope, wanted: PersonaRunFilters, *, limit: int
) -> PersonaRuns:
    """Return a page of the simulated calls the filter names, newest first, and a total."""
    params = {**asdict(scope), "agent": wanted.agent, "persona": wanted.persona}
    # independent: the total and the page are read apart, as a list always was
    async with pool.connection() as connection:
        total = await (await connection.execute(PERSONA_RUNS_COUNT, params)).fetchone()
        page = {**params, "before": wanted.before, "limit": limit + 1}
        rows = await (await connection.execute(PERSONA_RUNS_PAGE, page)).fetchall()
    runs = [PersonaRun(started_at=float(row["started_at"]), facts=facts_of(row)) for row in rows]
    return PersonaRuns(
        runs=runs[:limit],
        total=0 if total is None else int(total["total"]),
        next=runs[limit - 1].facts.call if len(runs) > limit else None,
    )


def _like_escaped(words: str) -> str:
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
