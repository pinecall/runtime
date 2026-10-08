"""The org's dataset: real calls kept as goldens, born at hang-up, decided, and played again."""

import datetime as dt
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound
from pinecall.domain.names import Env
from pinecall.evals.spoken import A_SIMULATED_CALLER
from pinecall.postgres.pool import Pool
from pinecall.wire.events import (
    CallStarted,
    EventReceived,
    MemoryOps,
    StateChanged,
    ToolCall,
    UserTurnEnded,
    event_of,
)
from pinecall.wire.frames import Entry
from pinecall.wire.rest.evals import BrokeOn, CaseDecision, EvalCase, EventStep, Expect, Golden
from pinecall.wire.scores import CallScore, Judgment

A_CASE = "case_"


# The inbox is for reading: past this many cases waiting on one agent, a broken call is judged as
# ever and not kept, until a person decides some.
PENDING_AT_MOST = 50


# Born at hang-up, a case is the platform's, not a person's.
THE_PANEL = "the hang-up panel"


# Settled into fields of code; every other judge is asked again by name in `expect.judges`.
BY_CODE = frozenset({"consent", "grounded"})


# Its rule is the simulated caller's, and a case plays written lines: nobody holds it.
UNPLAYABLE = frozenset({"persona"})


# The words of the first caller line a born case is named by.
NAMED_BY_WORDS = 4


TAKEN = "agent {agent} has a case named {name} already: forget it first, or promote under another"


NOTHING_SAID = "call {call} has no line of the caller's to play again"


NO_SUCH_CASE = "no case {id} in this key's org"


UNKNOWN_CASES = "agent {agent} has no case named {names}"


WRONG_ONLY_WHEN_DISMISSED = (
    "judge_was_wrong says a judge broke where it should have held, so it dismisses the case: "
    "send it with status dismissed"
)


NOT_WHAT_BROKE = "case {name} did not break on {judge}: it broke on {broke}"


# The note is kept on the judge's calibration label: with no judge it would be dropped unsaid.
A_NOTE_ALONE = (
    "note is kept on the calibration label of the judge that was wrong: send judge_was_wrong"
)


PUT = """
INSERT INTO eval_cases (id, org, agent, name, golden, source_call, source_env, held_out, author,
                        status, broke, source_version)
VALUES (%(id)s, %(org)s, %(agent)s, %(name)s, %(golden)s, %(source_call)s, %(source_env)s,
        %(held_out)s, %(author)s, %(status)s, %(broke)s, %(source_version)s)
RETURNING id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
"""


# One statement, so two seals at once never both pass the count: the second finds the first.
BORN = """
INSERT INTO eval_cases (id, org, agent, name, golden, source_call, source_env, held_out, author,
                        status, broke, source_version)
SELECT %(id)s, %(org)s, %(agent)s, %(name)s, %(golden)s, %(source_call)s, %(source_env)s, false,
       %(author)s, 'pending', %(broke)s, %(source_version)s
WHERE (SELECT count(*) FROM eval_cases
       WHERE org = %(org)s AND agent = %(agent)s AND status = 'pending') < %(at_most)s
  AND NOT EXISTS (SELECT 1 FROM eval_cases WHERE org = %(org)s AND source_call = %(source_call)s)
ON CONFLICT (org, agent, name) DO NOTHING
RETURNING id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
"""


LISTED = """
SELECT id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
FROM eval_cases
WHERE org = %(org)s
  AND (%(agent)s::text IS NULL OR agent = %(agent)s)
  AND (%(status)s::text IS NULL OR status = %(status)s)
ORDER BY agent, status = 'pending' DESC, created_at DESC, name
"""


WAITING = """
SELECT count(*) AS pending FROM eval_cases
WHERE org = %(org)s AND (%(agent)s::text IS NULL OR agent = %(agent)s) AND status = 'pending'
"""


# A nightly run reads what a person approved and the repository does not hold; a case named is
# read whatever its status, so a pending one can be reproduced before it is decided.
PICKED = """
SELECT id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
FROM eval_cases
WHERE org = %(org)s AND agent = %(agent)s
  AND (name = ANY(%(names)s)
       OR (%(every)s AND status = 'approved' AND NOT held_out AND NOT kept_in_repo))
ORDER BY name
"""


FOUND = """
SELECT id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
FROM eval_cases
WHERE id = %(id)s AND org = %(org)s
"""


DECIDED = """
UPDATE eval_cases
SET status = coalesce(%(status)s, status),
    held_out = coalesce(%(held_out)s, held_out),
    kept_in_repo = coalesce(%(kept_in_repo)s, kept_in_repo),
    decided_by = CASE WHEN %(status)s::text IS NULL THEN decided_by ELSE %(by)s END,
    decided_at = CASE WHEN %(status)s::text IS NULL THEN decided_at ELSE now() END
WHERE id = %(id)s AND org = %(org)s
RETURNING id, agent, name, golden, source_call, source_env, held_out, author, created_at,
       status, broke, source_version, kept_in_repo, decided_by
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
    status: str = "approved"
    broke: Sequence[BrokeOn] = field(default_factory=tuple[BrokeOn, ...])
    source_version: int | None = None


@dataclass(frozen=True)
class Born:
    """Where a call that broke was answered: the org, the world, the agent, the settings version."""

    org: str
    env: Env
    agent: str
    version: int | None


def golden_of(entries: Sequence[Entry], name: str, expect: Expect) -> Golden:
    """The call's caller lines, the state it opened in, the app's facts and what it recalled."""
    lines: list[str] = []
    state: dict[str, object] = {}
    events: list[EventStep] = []
    recalled: list[str] = []
    for entry in entries:
        if entry.type not in {"turn.user", "state.changed", "event.received", "memory.ops"}:
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
            case MemoryOps() as memory:
                facts = (fact.text for op in memory.ops if op.op == "recall" for fact in op.facts)
                recalled.extend(fact for fact in facts if fact not in recalled)
            case _:
                continue
    call = next((entry.call for entry in entries if entry.call is not None), "")
    if not lines:
        raise Conflict(NOTHING_SAID.format(call=call))
    started = entries[0].ts if entries else 0.0
    # A field the call gave nothing for is left out, as a golden written by hand leaves it out.
    given = {"state": state, "memory": recalled, "events": [event.written() for event in events]}
    return Golden.model_validate(
        {
            "name": name,
            "input": lines,
            "today": dt.datetime.fromtimestamp(started, dt.UTC).date(),
            "expect": expect.written(),
            "promoted_from": call,
            **{field: value for field, value in given.items() if value},
        }
    )


# `state.changed` holds the whole state, so the last one at or before the cut is where it opens.
def cut_at(entries: Sequence[Entry], seq: int) -> list[Entry]:
    """The call from after a seq on, opening in the state it was in at that seq."""
    if seq == 0:
        return list(entries)
    states = [entry for entry in entries if entry.type == "state.changed" and entry.seq <= seq]
    return [*states[-1:], *(entry for entry in entries if entry.seq > seq)]


# A verdict knows what broke, never what was right: the expect says "not like this again", and a
# person writes what the agent should have done.
def expect_of(judgments: Sequence[Judgment], entries: Sequence[Entry]) -> Expect:
    """What a call's broken verdicts say must not happen again, as a golden's expect."""
    broken = [judgment for judgment in judgments if judgment.verdict == "broken"]
    not_tools: list[str] = []
    for judgment in broken:
        if judgment.name == "consent":
            seqs = frozenset(judgment.evidence.seqs)
            ran = (entry for entry in entries if entry.type == "tool.call" and entry.seq in seqs)
            named = (event_of(entry) for entry in ran)
            not_tools.extend(
                call.name
                for call in named
                if isinstance(call, ToolCall) and call.name not in not_tools
            )
    by_name = [
        judgment.name
        for judgment in broken
        if judgment.name not in BY_CODE and judgment.name not in UNPLAYABLE
    ]
    # Only what broke is set, so the golden a person reads or pulls says nothing it does not mean.
    fields: dict[str, object] = {}
    if not_tools:
        fields["not_tools"] = not_tools
    if any(judgment.name == "grounded" for judgment in broken):
        fields["grounded"] = True
    if by_name:
        fields["judges"] = list(dict.fromkeys(by_name))
    return Expect.model_validate(fields)


def broke_of(score: CallScore) -> list[BrokeOn]:
    """The judges that broke on a call, each with its reason."""
    return [
        BrokeOn(judge=judgment.name, reason=judgment.reason)
        for judgment in score.judges
        if judgment.verdict == "broken"
    ]


# A call a run opened is never judged at hang-up, so it never gets here; a simulated caller is
# meant to break things, and a call nobody spoke in has nothing to play again.
async def kept_at_hangup(
    pool: Pool, entries: Sequence[Entry], score: CallScore, born: Born
) -> None:
    """Keep a call one of its judges broke on as a pending case, unless the inbox is full."""
    broke = broke_of(score)
    if not broke or _simulated(entries) or not _caller_spoke(entries):
        return
    call = next((entry.call for entry in entries if entry.call is not None), "")
    name = _name_of(broke[0].judge, entries, call)
    golden = golden_of(entries, name, expect_of(score.judges, entries))
    params = {
        "id": f"{A_CASE}{uuid4().hex[:12]}",
        "org": born.org,
        "agent": born.agent,
        "name": name,
        "golden": Jsonb(golden.written()),
        "source_call": call,
        "source_env": born.env,
        "author": THE_PANEL,
        "broke": Jsonb([judgment.written() for judgment in broke]),
        "source_version": born.version,
        "at_most": PENDING_AT_MOST,
    }
    async with pool.connection() as connection:
        await connection.execute(BORN, params)


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
        "status": promoting.status,
        "broke": Jsonb([judgment.written() for judgment in promoting.broke]),
        "source_version": promoting.source_version,
    }
    try:
        async with pool.connection() as connection:
            row = await (await connection.execute(PUT, params)).fetchone()
    except UniqueViolation as taken:
        raise Conflict(TAKEN.format(agent=agent, name=promoting.name)) from taken
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=params["id"]))
    return _case_of(row)


async def listed(
    pool: Pool, org: str, agent: str | None, status: str | None = None
) -> list[EvalCase]:
    """The org's cases, one agent's or every one, one status or all, the pending ones first."""
    params = {"org": org, "agent": agent, "status": status}
    async with pool.connection() as connection:
        rows = await (await connection.execute(LISTED, params)).fetchall()
    return [_case_of(row) for row in rows]


async def waiting(pool: Pool, org: str, agent: str | None) -> int:
    """How many cases wait for a person, one agent's or the org's."""
    async with pool.connection() as connection:
        row = await (await connection.execute(WAITING, {"org": org, "agent": agent})).fetchone()
    return 0 if row is None else int(row["pending"])


async def picked(
    pool: Pool, org: str, agent: str, names: Sequence[str], *, every: bool
) -> list[Golden]:
    """The goldens a run plays: the cases named, and with every, the approved ones not held out."""
    params = {"org": org, "agent": agent, "names": list(names), "every": every}
    async with pool.connection() as connection:
        rows = await (await connection.execute(PICKED, params)).fetchall()
    found = [_case_of(row) for row in rows]
    missing = sorted(set(names) - {case.name for case in found})
    if missing:
        raise NotFound(UNKNOWN_CASES.format(agent=agent, names=", ".join(missing)))
    return [case.golden for case in found]


async def found(pool: Pool, org: str, case: str) -> EvalCase:
    """One of the org's cases; another org's, or nobody's, is a 404."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FOUND, {"id": case, "org": org})).fetchone()
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=case))
    return _case_of(row)


def check_decision(case: EvalCase, decision: CaseDecision) -> None:
    """Refuse a judge called wrong on a case not dismissed or not broken on, and a note alone."""
    wrong = decision.judge_was_wrong
    if wrong is None:
        if decision.note is not None:
            raise DeclarationRefused(A_NOTE_ALONE)
        return
    if decision.status != "dismissed":
        raise DeclarationRefused(WRONG_ONLY_WHEN_DISMISSED)
    broke = [judgment.judge for judgment in case.broke]
    if wrong not in broke:
        named = ", ".join(broke) or "no judge (a person kept it)"
        raise DeclarationRefused(NOT_WHAT_BROKE.format(name=case.name, judge=wrong, broke=named))


async def decided(pool: Pool, org: str, case: str, decision: CaseDecision, by: str) -> EvalCase:
    """Write what a person decided of one case: its status, held out, kept in the repository."""
    params = {
        "id": case,
        "org": org,
        "status": decision.status,
        "held_out": decision.held_out,
        "kept_in_repo": decision.kept_in_repo,
        "by": by,
    }
    async with pool.connection() as connection:
        row = await (await connection.execute(DECIDED, params)).fetchone()
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=case))
    return _case_of(row)


async def forgotten(pool: Pool, org: str, case: str) -> None:
    """Forget one of the org's cases; another org's, or nobody's, is a 404."""
    async with pool.connection() as connection:
        row = await (await connection.execute(FORGET, {"id": case, "org": org})).fetchone()
    if row is None:
        raise NotFound(NO_SUCH_CASE.format(id=case))


def _name_of(judge: str, entries: Sequence[Entry], call: str) -> str:
    """The first judge that broke, the caller's first words, and the call's last six characters."""
    first = next(
        (
            heard.text
            for entry in entries
            if entry.type == "turn.user"
            and isinstance(heard := event_of(entry), UserTurnEnded)
            and heard.text.strip()
        ),
        "",
    )
    bare = unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode().casefold()
    words = re.findall(r"[a-z0-9]+", bare)[:NAMED_BY_WORDS]
    return "-".join([judge, *words, call[-6:].casefold()])


def _simulated(entries: Sequence[Entry]) -> bool:
    for entry in entries:
        if entry.type == "call.started" and isinstance(started := event_of(entry), CallStarted):
            return started.persona is not None or started.from_ == A_SIMULATED_CALLER
    return False


def _caller_spoke(entries: Sequence[Entry]) -> bool:
    return any(
        entry.type == "turn.user"
        and isinstance(heard := event_of(entry), UserTurnEnded)
        and bool(heard.text.strip())
        for entry in entries
    )


def _case_of(row: dict[str, object]) -> EvalCase:
    created = row["created_at"]
    return EvalCase.model_validate(
        {
            **row,
            "created_at": created.timestamp() if isinstance(created, dt.datetime) else created,
        }
    )
