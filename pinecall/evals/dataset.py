"""The org's dataset: real calls kept as goldens, promoted, listed, held out, and played again."""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool
from pinecall.wire.events import EventReceived, StateChanged, UserTurnEnded, event_of
from pinecall.wire.frames import Entry
from pinecall.wire.rest.evals import EvalCase, EventStep, Expect, Golden

A_CASE = "case_"


TAKEN = "agent {agent} has a case named {name} already: forget it first, or promote under another"


NOTHING_SAID = "call {call} has no line of the caller's to play again"


NO_SUCH_CASE = "no case {id} in this key's org"


UNKNOWN_CASES = "agent {agent} has no case named {names}"


PUT = """
INSERT INTO eval_cases (id, org, agent, name, golden, source_call, source_env, held_out, author)
VALUES (%(id)s, %(org)s, %(agent)s, %(name)s, %(golden)s, %(source_call)s, %(source_env)s,
        %(held_out)s, %(author)s)
RETURNING id, agent, name, golden, source_call, source_env, held_out, author, created_at
"""


LISTED = """
SELECT id, agent, name, golden, source_call, source_env, held_out, author, created_at
FROM eval_cases
WHERE org = %(org)s AND (%(agent)s::text IS NULL OR agent = %(agent)s)
ORDER BY agent, name
"""


# A nightly run reads what is not held out; a case held out is read only when named.
PICKED = """
SELECT id, agent, name, golden, source_call, source_env, held_out, author, created_at
FROM eval_cases
WHERE org = %(org)s AND agent = %(agent)s
  AND (name = ANY(%(names)s) OR (%(every)s AND NOT held_out))
ORDER BY name
"""


FORGET = "DELETE FROM eval_cases WHERE id = %(id)s AND org = %(org)s RETURNING id"


@dataclass(frozen=True)
class Promoted:
    """A finished call to keep as a case: whose, from which world, named how, by whom."""

    org: str
    env: Env
    name: str
    author: str
    held_out: bool = False


def golden_of(entries: Sequence[Entry], name: str, expect: Expect) -> Golden:
    """The call's caller lines, the state it opened in and the app's facts, as a golden."""
    lines: list[str] = []
    state: dict[str, object] = {}
    events: list[EventStep] = []
    for entry in entries:
        if entry.type not in {"turn.user", "state.changed", "event.received"}:
            continue
        match event_of(entry):
            case UserTurnEnded() as heard if heard.text.strip():
                lines.append(heard.text)
            case StateChanged() as changed if not lines and not state:
                state = dict(changed.state)
            case EventReceived() as arrived if arrived.source == "app":
                events.append(
                    EventStep(after_turn=len(lines), name=arrived.name, data=arrived.data)
                )
            case _:
                continue
    call = next((entry.call for entry in entries if entry.call is not None), "")
    if not lines:
        raise Conflict(NOTHING_SAID.format(call=call))
    started = entries[0].ts if entries else 0.0
    return Golden.model_validate(
        {
            "name": name,
            "state": state,
            "input": lines,
            "events": [event.written() for event in events],
            "today": dt.datetime.fromtimestamp(started, dt.UTC).date(),
            "expect": expect.written(),
            "promoted_from": call,
        }
    )


async def promoted(pool: Pool, golden: Golden, agent: str, promoting: Promoted) -> EvalCase:
    """Keep the golden as a case of the org's dataset; a name the agent has already is refused."""
    named = golden.model_copy(update={"name": promoting.name})
    params = {
        "id": f"{A_CASE}{uuid4().hex[:12]}",
        "org": promoting.org,
        "agent": agent,
        "name": promoting.name,
        "golden": Jsonb(named.written()),
        "source_call": named.promoted_from,
        "source_env": promoting.env,
        "held_out": promoting.held_out,
        "author": promoting.author,
    }
    try:
        async with pool.connection() as connection:
            row = await (await connection.execute(PUT, params)).fetchone()
    except UniqueViolation as taken:
        raise Conflict(TAKEN.format(agent=agent, name=promoting.name)) from taken
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=params["id"]))
    return _case_of(row)


async def listed(pool: Pool, org: str, agent: str | None) -> list[EvalCase]:
    """The org's cases, one agent's or every one, by agent and name."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, {"org": org, "agent": agent})).fetchall()
    return [_case_of(row) for row in rows]


async def picked(
    pool: Pool, org: str, agent: str, names: Sequence[str], *, every: bool
) -> list[Golden]:
    """The goldens a run plays: the cases named, and with every, each one not held out."""
    params = {"org": org, "agent": agent, "names": list(names), "every": every}
    async with pool.connection() as connection:
        rows = await (await connection.execute(PICKED, params)).fetchall()
    found = [_case_of(row) for row in rows]
    missing = sorted(set(names) - {case.name for case in found})
    if missing:
        raise NotFound(UNKNOWN_CASES.format(agent=agent, names=", ".join(missing)))
    return [case.golden for case in found]


async def forgotten(pool: Pool, org: str, case: str) -> None:
    """Forget one of the org's cases; another org's, or nobody's, is a 404."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FORGET, {"id": case, "org": org})).fetchone()
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=case))


def _case_of(row: dict[str, object]) -> EvalCase:
    created = row["created_at"]
    return EvalCase.model_validate(
        {
            **row,
            "created_at": created.timestamp() if isinstance(created, dt.datetime) else created,
        }
    )
