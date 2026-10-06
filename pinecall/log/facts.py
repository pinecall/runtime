"""What is known of every call without folding its log: the facts row, written as entries land."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.events import (
    EVENTS,
    AgentTurnEnded,
    CallDialing,
    CallEnded,
    CallRinging,
    CallStarted,
    CallSummary,
    UserTurnEnded,
    event_of,
)
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.parts import PLATFORM, Cost
from pinecall.wire.room import RoomOpened
from pinecall.wire.scores import CallScore

# The judge whose broken verdict raises the promise flag (evals/judges.py).
PROMISES = "promises"


# skipped and deferred are not judged.
SETTLED = frozenset({"held", "broken"})


ENDED_BY_A_PERSON = frozenset({"transferred", "supervisor_ended"})


# A whisper is not one of these: the agent keeps the conversation.
A_PERSON_TOOK_PART = frozenset(
    {
        "supervisor.took_over",
        "supervisor.said",
        "supervisor.ended",
        "supervisor.transferred",
        "call.transferred",
    }
)


# A web call is spoken only once a room opens.
SPOKEN_CHANNEL = "phone"


# The columns of call_facts, in the order the row is written; the dataclass names them the same.
COLUMNS = (
    "call",
    "channel",
    "direction",
    "from_number",
    "to_number",
    "name",
    "contact",
    "persona",
    "spoken",
    "ended_at",
    "end_reason",
    "outcome",
    "cost_usd",
    "cost_llm_usd",
    "cost_stt_usd",
    "cost_tts_usd",
    "cost_phone_usd",
    "cost_platform_usd",
    "judged",
    "held",
    "passed",
    "reason",
    "escalated",
    "promised",
    "e2e",
    "heard_at",
    "last_text",
    "last_at",
    "last_in",
)


ARRAYS = frozenset({"e2e", "heard_at"})

# What a summary's cost is folded by: a row's unit says its stage, and the box's own rows
# (PLATFORM) are the platform's, whatever their unit.
STAGES = ("llm", "stt", "tts", "phone", "platform")
STAGE_OF_UNIT = {
    "input_tokens": "llm",
    "cached_input_tokens": "llm",
    "cache_creation_tokens": "llm",
    "output_tokens": "llm",
    "characters": "tts",
    "audio_seconds": "stt",
    "minutes": "phone",
}


# In the order of the calls, as the heads are locked: two writers never wait in a circle.
FACTS_LOCKED = "select * from call_facts where call = any(%(calls)s) order by call for update"


LENT = """
INSERT INTO call_facts (call, lent) VALUES (%(call)s, %(lent)s)
ON CONFLICT (call) DO UPDATE SET lent = excluded.lent
"""


# Every field comes from some entry, so the row can always be folded again from the log.
@dataclass(frozen=True, slots=True)
class CallFacts:
    """One call as its lists, counts and inbox see it."""

    call: str
    agent: str = ""
    channel: str | None = None
    direction: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    name: str | None = None
    # The inbox key: the app's contact id, else the number or the visitor id.
    contact: str | None = None
    persona: str | None = None
    spoken: bool = False
    ended_at: float | None = None
    end_reason: str | None = None
    outcome: str | None = None
    cost_usd: float | None = None
    cost_llm_usd: float | None = None
    cost_stt_usd: float | None = None
    cost_tts_usd: float | None = None
    cost_phone_usd: float | None = None
    cost_platform_usd: float | None = None
    judged: int | None = None
    held: int | None = None
    passed: bool | None = None
    reason: str | None = None
    escalated: bool = False
    promised: bool = False
    e2e: tuple[float, ...] = ()
    heard_at: tuple[float, ...] = ()
    last_text: str | None = None
    last_at: float | None = None
    last_in: bool | None = None

    @property
    def score(self) -> dict[str, object] | None:
        """Return the score as the wire carries it, or None while no judge settled."""
        if not self.judged:
            return None
        return {
            "held": self.held or 0,
            "judged": self.judged,
            "passed": self.passed is not False,
            "reason": self.reason,
        }

    @property
    def flags(self) -> list[str]:
        """Return the review flags raised, in the wire's order."""
        raised = (
            ("escalated", self.escalated),
            ("low_score", self.passed is False),
            ("promise", self.promised),
        )
        return [flag for flag, up in raised if up]


# The types fold() reads; every other entry leaves a call's facts as they are, so a group of only
# those never locks or reads a facts row (most of a call's entries: its metrics, its transcripts).
FOLDED_TYPES = (
    frozenset(
        name
        for name, model in EVENTS.items()
        if model
        in (
            CallRinging,
            CallDialing,
            CallStarted,
            CallEnded,
            CallSummary,
            CallScore,
            RoomOpened,
            UserTurnEnded,
            AgentTurnEnded,
        )
    )
    | A_PERSON_TOOK_PART
)


# The whole row is written every time: the merge happened in fold(), in one place.
FACTS_WRITTEN = sql.SQL(
    "insert into call_facts ({columns}) values ({values})"
    " on conflict (call) do update set {updates}"
).format(
    columns=sql.SQL(", ").join(sql.Identifier(column) for column in COLUMNS),
    values=sql.SQL(", ").join(sql.Placeholder(column) for column in COLUMNS),
    updates=sql.SQL(", ").join(
        sql.SQL("{0} = excluded.{0}").format(sql.Identifier(column)) for column in COLUMNS[1:]
    ),
)


def fold(facts: CallFacts, entry: Entry) -> CallFacts:
    """Return the facts with what the entry says; an entry that says nothing to them, unchanged."""
    if entry.call is None or entry.ephemeral:
        return facts
    folded = facts
    match _readable(entry):
        case CallRinging() | CallDialing() | CallStarted() as line:
            folded = _on_the_line(facts, line)
        case CallEnded() | CallSummary() as over:
            folded = _over(facts, over)
        case CallScore() as score:
            folded = _scored(facts, score)
        case RoomOpened():
            folded = replace(facts, spoken=True)
        case UserTurnEnded() | AgentTurnEnded() as turn:
            folded = _turn_folded(facts, entry, turn)
        case _ if entry.type in A_PERSON_TOOK_PART:
            folded = replace(facts, escalated=True)
        case _:
            pass
    return folded


# On the writer's connection, so folds land in seq order under the head rows' locks. The entries
# are each call's in seq order: every call's row is locked at once, folded in memory, and the rows
# that moved are written in one round of statements.
async def record(connection: Connection, entries: Sequence[Entry]) -> None:
    """Fold the entries into their calls' facts rows, in the transaction they are written in."""
    by_call: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.call is not None and not entry.ephemeral and entry.type in FOLDED_TYPES:
            by_call.setdefault(entry.call, []).append(entry)
    if not by_call:
        return
    locked = await connection.execute(FACTS_LOCKED, {"calls": sorted(by_call)})
    known = {str(row["call"]): facts_of(row) for row in await locked.fetchall()}
    written: list[Mapping[str, object]] = []
    for call, kept in by_call.items():
        facts = known.get(call, CallFacts(call=call))
        folded = facts
        for entry in kept:
            folded = fold(folded, entry)
        if folded != facts:
            row = {**asdict(folded), "e2e": list(folded.e2e), "heard_at": list(folded.heard_at)}
            row.pop("agent")
            written.append(row)
    if written:
        async with connection.cursor() as cursor:
            await cursor.executemany(FACTS_WRITTEN, written)


def facts_of(row: DictRow) -> CallFacts:
    """Return the facts a call_facts row holds, with its agent when the head row was joined."""
    facts = CallFacts(**{name: row[name] for name in COLUMNS if name not in ARRAYS})
    return replace(
        facts, agent=row.get("agent") or "", e2e=tuple(row["e2e"]), heard_at=tuple(row["heard_at"])
    )


# Not a fold: the vendors a call ran on the box's key are not in its entries, the gateway knows
# them at the seal.
async def lent(pool: Pool, call: str, vendors: Sequence[str]) -> None:
    """Keep the vendors the call ran on the box's own key with its facts."""
    async with pool.connection() as connection:
        await connection.execute(LENT, {"call": call, "lent": list(vendors)})


def _readable(entry: Entry) -> WireModel | None:
    try:
        return event_of(entry)
    except DeclarationRefused:
        return None


def _on_the_line(facts: CallFacts, line: CallRinging | CallDialing | CallStarted) -> CallFacts:
    caller = line.caller
    direction = line.direction if isinstance(line, CallStarted) else None
    persona = line.persona if isinstance(line, CallStarted) else None
    return replace(
        facts,
        channel=line.channel,
        direction=direction or ("outbound" if isinstance(line, CallDialing) else "inbound"),
        from_number=line.from_,
        to_number=line.to,
        name=facts.name if caller is None else caller.name or facts.name,
        contact=(caller.id if caller is not None and caller.id else None) or line.from_,
        persona=persona or facts.persona,
        spoken=facts.spoken or line.channel == SPOKEN_CHANNEL,
    )


# The reason the call ended with stands; the summary's is taken only when there was none.
def _over(facts: CallFacts, over: CallEnded | CallSummary) -> CallFacts:
    if isinstance(over, CallEnded):
        return replace(
            facts,
            ended_at=over.ended_at,
            end_reason=facts.end_reason or over.reason,
            escalated=facts.escalated or over.reason in ENDED_BY_A_PERSON,
        )
    return replace(
        facts,
        outcome=over.outcome,
        cost_usd=over.cost.usd,
        end_reason=facts.end_reason or over.reason,
        **_cost_by_stage(over.cost),
    )


def _cost_by_stage(cost: Cost) -> dict[str, float]:
    summed: dict[str, float] = dict.fromkeys(STAGES, 0.0)
    for row in cost.rows:
        stage = "platform" if row.provider == PLATFORM else STAGE_OF_UNIT[row.unit]
        summed[stage] += row.usd
    return {f"cost_{stage}_usd": round(usd, 6) for stage, usd in summed.items()}


# A verdict replaces the one before it whole.
def _scored(facts: CallFacts, score: CallScore) -> CallFacts:
    settled = [judge for judge in score.judges if judge.verdict in SETTLED]
    broken = [settled_one for settled_one in settled if settled_one.verdict == "broken"]
    passed = score.passed if score.passed is not None else (not broken if settled else None)
    return replace(
        facts,
        judged=len(settled),
        held=len(settled) - len(broken),
        passed=passed,
        reason=broken[0].reason if broken else None,
        promised=any(item.name == PROMISES for item in broken),
    )


def _turn_folded(facts: CallFacts, entry: Entry, turn: UserTurnEnded | AgentTurnEnded) -> CallFacts:
    if isinstance(turn, UserTurnEnded):
        return replace(
            facts,
            heard_at=(*facts.heard_at, entry.ts),
            last_text=turn.text,
            last_at=entry.ts,
            last_in=True,
        )
    e2e = turn.metrics.e2e_latency
    return replace(
        facts,
        e2e=facts.e2e if e2e is None else (*facts.e2e, e2e),
        last_text=turn.text,
        last_at=entry.ts,
        last_in=False,
    )
