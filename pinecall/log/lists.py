"""The pages of calls a list asks for: by agent, channel and words, and a persona's own runs."""

from dataclasses import asdict, dataclass, field

from pinecall.domain.scope import Scope
from pinecall.log.facts import (
    FOUND_COUNT,
    FOUND_PAGE,
    PERSONA_RUNS_COUNT,
    PERSONA_RUNS_PAGE,
    CallFacts,
    facts_of,
)
from pinecall.postgres.pool import Pool


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
    """Whose runs a list asks for: the agent, the persona that called it, the page before this."""

    agent: str
    persona: str
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
    """Return a page of the persona's calls to the agent in the scope, newest first, and a total."""
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
