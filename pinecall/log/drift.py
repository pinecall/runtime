"""Drift: each day's stage latencies and verdicts by agent and version, folded at the seal."""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Literal

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log import _histogram, queries, refold
from pinecall.log.store import entry_of
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.events import AgentTurnEnded, UserTurnEnded, event_of
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import TurnMetadata
from pinecall.wire.rest.usage import (
    DriftJudge,
    DriftStage,
    InsightsStage,
    JudgeTally,
    StageTally,
)
from pinecall.wire.scores import CallScore

type Stage = Literal["stt", "llm", "tts"]


# A turn's stage, the vendor and the model its report named ('' for none).
type StageKey = tuple[Stage, str, str]


# A judge by name and by the hash of the question it asked.
type JudgeKey = tuple[str, str]


logger = logging.getLogger(__name__)


# The table cannot key a null: a call that ran on no version of its scope's settings is 0.
NO_VERSION = 0


# skipped and deferred were never settled, so they count on neither side.
SETTLED = frozenset({"held", "broken"})


# Calls named per read of the heads while rebuilding; each is folded in its own transaction.
A_PAGE = 500


# Taken once per call: a second fold of the same call finds its row and changes only what differs.
CLAIMED = """
insert into drift_calls (call, org, env, holder, agent, day, config_version, verdicts)
values (%(call)s, %(org)s, %(env)s, %(holder)s, %(agent)s, %(day)s, %(version)s, %(verdicts)s)
on conflict (call) do nothing
returning call
"""


COUNTED = "select verdicts from drift_calls where call = %(call)s for update"


RECOUNTED = "update drift_calls set verdicts = %(verdicts)s where call = %(call)s"


# Two histograms add bucket by bucket (log/_histogram.py); both always have its BUCKETS.
STAGES_ADDED = """
insert into stage_days as kept (org, env, holder, agent, day, config_version, stage, vendor,
                                model, turns, buckets, confidence_sum, confidence_turns)
select %(org)s, %(env)s, %(holder)s, %(agent)s, %(day)s, %(version)s, sample.stage,
       sample.vendor, sample.model, sample.turns, sample.buckets, sample.confidence_sum,
       sample.confidence_turns
from jsonb_to_recordset(%(samples)s) as sample(stage text, vendor text, model text,
     turns integer, buckets integer[], confidence_sum double precision, confidence_turns integer)
on conflict (org, env, holder, day, agent, config_version, stage, vendor, model) do update set
    turns = kept.turns + excluded.turns,
    buckets = (select array_agg(mine + theirs order by at)
               from unnest(kept.buckets, excluded.buckets) with ordinality as bucket(mine, theirs,
                                                                                    at)),
    confidence_sum = kept.confidence_sum + excluded.confidence_sum,
    confidence_turns = kept.confidence_turns + excluded.confidence_turns
"""


# A verdict counted is +1 on its side; a verdict replaced is -1 on the side it was counted on.
VERDICTS_ADDED = """
insert into judge_days as kept (org, env, holder, agent, day, config_version, judge, criteria,
                                held, broken)
select %(org)s, %(env)s, %(holder)s, %(agent)s, %(day)s, %(version)s, verdict.judge,
       verdict.criteria, verdict.held, verdict.broken
from jsonb_to_recordset(%(verdicts)s) as verdict(judge text, criteria text, held integer,
                                                   broken integer)
on conflict (org, env, holder, day, agent, config_version, judge, criteria) do update set
    held = kept.held + excluded.held,
    broken = kept.broken + excluded.broken
"""


# A side is a day, a version, or both; one agent's rows in the scope.
SIDE_STAGES = """
select stage, vendor, model, config_version, turns, buckets, confidence_sum, confidence_turns
from stage_days
where org = %(org)s and env = %(env)s and holder = %(holder)s and agent = %(agent)s
  and (%(day)s::date is null or day = %(day)s)
  and (%(version)s::integer is null or config_version = %(version)s)
"""


SIDE_VERDICTS = """
select judge, criteria, config_version, held, broken
from judge_days
where org = %(org)s and env = %(env)s and holder = %(holder)s and agent = %(agent)s
  and (%(day)s::date is null or day = %(day)s)
  and (%(version)s::integer is null or config_version = %(version)s)
"""


DAY_STAGES = """
select stage, vendor, model, turns, buckets, confidence_sum, confidence_turns
from stage_days
where org = %(org)s and env = %(env)s and holder = %(holder)s and day = %(day)s
"""


# A rebuild forgets what it will fold again, in one transaction, then folds call by call.
FORGET = """
with calls as (
    delete from drift_calls
    where (%(org)s::text is null or org = %(org)s) and (%(since)s::date is null or day >= %(since)s)
), stages as (
    delete from stage_days
    where (%(org)s::text is null or org = %(org)s) and (%(since)s::date is null or day >= %(since)s)
)
delete from judge_days
where (%(org)s::text is null or org = %(org)s) and (%(since)s::date is null or day >= %(since)s)
"""


SEALED_PAGE = """
select log from call_log_head
where call is not null and sealed and started_at is not null and log > %(after)s
  and (%(org)s::text is null or org = %(org)s)
  and (%(since)s::float8 is null or started_at >= %(since)s)
order by log
limit %(limit)s
"""


@dataclass(frozen=True)
class StageSample:
    """One stage on one vendor and model: its turns, their seconds counted, the ears' confidence."""

    turns: int = 0
    buckets: tuple[int, ...] = tuple(_histogram.empty())
    confidence_sum: float = 0.0
    confidence_turns: int = 0


@dataclass(frozen=True)
class Verdict:
    """One judge's settled verdict on a call, under the hash of the question it asked."""

    judge: str
    criteria: str
    held: bool


@dataclass(frozen=True)
class JudgeRead:
    """One judge on one side: its verdicts held and settled, and the questions it asked."""

    held: int
    judged: int
    criteria: frozenset[str]

    @property
    def pass_rate(self) -> float | None:
        """The share of its settled verdicts that held; None when none settled."""
        return self.held / self.judged if self.judged else None


@dataclass(frozen=True)
class Side:
    """One side of a drift: a day, a version, or both."""

    day: date | None = None
    version: int | None = None


@dataclass(frozen=True)
class Tally:
    """One side of an agent's drift: its stages and judges, and the versions its calls ran."""

    stages: dict[StageKey, StageSample] = field(default_factory=dict[StageKey, StageSample])
    judges: dict[str, JudgeRead] = field(default_factory=dict[str, JudgeRead])
    versions: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Rebuilt:
    """What a rebuild folded again."""

    calls: int
    folded: int


VERDICTS: TypeAdapter[list[Verdict]] = TypeAdapter(list[Verdict])


# The seal's: a call is folded once, and folded again (judged again) only its verdicts change.
async def fold(pool: Pool, call: str, entries: Sequence[Entry], score: CallScore) -> bool:
    """Count a sealed call's stages and verdicts into its day; False for a call never started."""
    corner = await queries.scope_of_call(pool, call)
    if corner is None or corner.scope is None or corner.started_at is None:
        return False
    where = _where(corner.scope, corner.agent, _day_of(corner.started_at), corner.versions.config)
    verdicts = _verdicts_of(score)
    async with pool.connection() as connection, connection.transaction():
        claimed = await connection.execute(
            CLAIMED,
            {**where, "call": call, "verdicts": Jsonb(VERDICTS.dump_python(verdicts, mode="json"))},
        )
        if await claimed.fetchone() is not None:
            await _stages_added(connection, where, _samples_of(entries))
            await _verdicts_added(connection, where, _tallied(verdicts, sign=1))
            return True
        row = await (await connection.execute(COUNTED, {"call": call})).fetchone()
        counted = [] if row is None else VERDICTS.validate_python(row["verdicts"])
        if counted == verdicts:
            return True
        changed = {**_tallied(counted, sign=-1)}
        for key, (passes, fails) in _tallied(verdicts, sign=1).items():
            before = changed.get(key, (0, 0))
            changed[key] = (before[0] + passes, before[1] + fails)
        await _verdicts_added(connection, where, changed)
        await connection.execute(
            RECOUNTED,
            {"call": call, "verdicts": Jsonb(VERDICTS.dump_python(verdicts, mode="json"))},
        )
    return True


async def stages_of_day(pool: Pool, scope: Scope, day: date) -> list[InsightsStage]:
    """Every stage of the scope's day by vendor and model, every agent and version added."""
    params = {"org": scope.org, "env": scope.env, "holder": scope.holder, "day": day}
    async with pool.connection() as connection:
        rows = await (await connection.execute(DAY_STAGES, params)).fetchall()
    merged = _merged_stages(rows)
    return [
        InsightsStage(
            stage=stage,
            vendor=vendor or None,
            model=model or None,
            **_tallied_stage(sample).model_dump(),
        )
        for (stage, vendor, model), sample in sorted(merged.items())
    ]


# What moved most comes first: a judge by its pass rate, a stage by its median.
def compared(before: Tally, after: Tally) -> tuple[list[DriftJudge], list[DriftStage]]:
    """Each judge and each stage on both sides, and how far it moved, the most moved first."""
    judges = [
        _judge_moved(name, before.judges.get(name), after.judges.get(name))
        for name in sorted({*before.judges, *after.judges})
    ]
    stages = [
        _stage_moved(key, before.stages.get(key), after.stages.get(key))
        for key in sorted({*before.stages, *after.stages})
    ]
    judges.sort(key=lambda judge: -abs(judge.moved) if judge.moved is not None else 1.0)
    stages.sort(key=lambda row: -abs(row.median_moved_s) if row.median_moved_s is not None else 1.0)
    return judges, stages


async def tally(pool: Pool, scope: Scope, agent: str, side: Side) -> Tally:
    """One side of an agent's drift in the scope: its stages, its judges, its versions."""
    params = {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "agent": agent,
        "day": side.day,
        "version": side.version,
    }
    # independent: two reads of counted days; a call counted between them moves one by a call
    async with pool.connection() as connection:
        stages = await (await connection.execute(SIDE_STAGES, params)).fetchall()
        verdicts = await (await connection.execute(SIDE_VERDICTS, params)).fetchall()
    judges: dict[str, JudgeRead] = {}
    for row in verdicts:
        before = judges.get(row["judge"], JudgeRead(0, 0, frozenset()))
        judges[row["judge"]] = JudgeRead(
            held=before.held + row["held"],
            judged=before.judged + row["held"] + row["broken"],
            criteria=before.criteria | {row["criteria"]},
        )
    ran = {int(row["config_version"]) for row in (*stages, *verdicts)}
    return Tally(
        stages=_merged_stages(stages),
        judges=judges,
        versions=frozenset(version for version in ran if version != NO_VERSION),
    )


async def rebuild(pool: Pool, *, org: str | None, since: float | None) -> Rebuilt:
    """Forget the drift of the calls the flags name and fold each sealed one again from its log."""
    day = None if since is None else _day_of(since)
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(FORGET, {"org": org, "since": day})
    calls = folded = 0
    after = ""
    while names := await _sealed_after(pool, org, since, after):
        for call in names:
            entries = await _entries_of(pool, call)
            score = _last_score(entries)
            if score is not None and await fold(pool, call, entries, score):
                folded += 1
            calls += 1
        after = names[-1]
    return Rebuilt(calls=calls, folded=folded)


def _samples_of(entries: Iterable[Entry]) -> dict[StageKey, StageSample]:
    """The stages a call's turns reported, by the vendor and model each turn named."""
    seconds: dict[StageKey, list[float]] = {}
    turns: dict[StageKey, int] = {}
    confidence: dict[StageKey, list[float]] = {}
    for entry in entries:
        for key, value, sure in _stages_of(entry):
            turns[key] = turns.get(key, 0) + 1
            if value is not None:
                seconds.setdefault(key, []).append(value)
            if sure is not None:
                confidence.setdefault(key, []).append(sure)
    return {
        key: StageSample(
            turns=count,
            buckets=tuple(_histogram.counted(seconds.get(key, ()))),
            confidence_sum=sum(confidence.get(key, ())),
            confidence_turns=len(confidence.get(key, ())),
        )
        for key, count in turns.items()
    }


def _verdicts_of(score: CallScore) -> list[Verdict]:
    """The settled verdicts of a score, each under the hash of its question."""
    return [
        Verdict(judge.name, _criteria_hash(judge.criteria), judge.verdict == "held")
        for judge in score.judges
        if judge.verdict in SETTLED
    ]


def _criteria_hash(criteria: str) -> str:
    """A judge's question as drift keys it: its hash, so a changed question is told apart."""
    return sha256(criteria.encode()).hexdigest()


def _day_of(started_at: float) -> date:
    """The UTC day a call that started then is counted on."""
    return datetime.fromtimestamp(started_at, UTC).date()


def _stages_of(entry: Entry) -> list[tuple[StageKey, float | None, float | None]]:
    if entry.type not in {"turn.user", "turn.agent"}:
        return []
    try:
        turn = event_of(entry)
    except DeclarationRefused:
        logger.warning("call %s: seq %d is not a turn drift reads", entry.call, entry.seq)
        return []
    found: list[tuple[StageKey, float | None, float | None]] = []
    if isinstance(turn, UserTurnEnded):
        heard = turn.metrics
        delay, sure = heard.transcription_delay, turn.transcript_confidence
        if delay is not None or sure is not None:
            found.append((_key("stt", heard.stt_metadata), delay, sure))
    if isinstance(turn, AgentTurnEnded):
        answered = turn.metrics
        if answered.llm_node_ttft is not None:
            found.append((_key("llm", answered.llm_metadata), answered.llm_node_ttft, None))
        if answered.tts_node_ttfb is not None:
            found.append((_key("tts", answered.tts_metadata), answered.tts_node_ttfb, None))
    return found


def _key(stage: Stage, metadata: TurnMetadata | None) -> StageKey:
    if metadata is None:
        return (stage, "", "")
    return (stage, metadata.model_provider or "", metadata.model_name or "")


def _where(scope: Scope, agent: str, day: date, version: int | None) -> dict[str, object]:
    return {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "agent": agent,
        "day": day,
        "version": NO_VERSION if version is None else version,
    }


def _tallied(verdicts: Iterable[Verdict], *, sign: int) -> dict[JudgeKey, tuple[int, int]]:
    tallied: dict[JudgeKey, tuple[int, int]] = {}
    for item in verdicts:
        passes, fails = tallied.get((item.judge, item.criteria), (0, 0))
        tallied[(item.judge, item.criteria)] = (
            (passes + sign, fails) if item.held else (passes, fails + sign)
        )
    return tallied


async def _stages_added(
    connection: Connection, where: Mapping[str, object], samples: Mapping[StageKey, StageSample]
) -> None:
    if not samples:
        return
    rows: list[JsonObject] = [
        {
            "stage": stage,
            "vendor": vendor,
            "model": model,
            "turns": sample.turns,
            "buckets": list(sample.buckets),
            "confidence_sum": sample.confidence_sum,
            "confidence_turns": sample.confidence_turns,
        }
        for (stage, vendor, model), sample in samples.items()
    ]
    await connection.execute(STAGES_ADDED, {**where, "samples": Jsonb(rows)})


async def _verdicts_added(
    connection: Connection, where: Mapping[str, object], tallied: Mapping[JudgeKey, tuple[int, int]]
) -> None:
    if not tallied:
        return
    rows: list[JsonObject] = [
        {"judge": judge, "criteria": criteria, "held": passes, "broken": fails}
        for (judge, criteria), (passes, fails) in tallied.items()
    ]
    await connection.execute(VERDICTS_ADDED, {**where, "verdicts": Jsonb(rows)})


def _merged_stages(rows: Iterable[DictRow]) -> dict[StageKey, StageSample]:
    merged: dict[StageKey, StageSample] = {}
    for row in rows:
        key: StageKey = (row["stage"], row["vendor"], row["model"])
        before = merged.get(key, StageSample())
        merged[key] = StageSample(
            turns=before.turns + row["turns"],
            buckets=tuple(_histogram.added(before.buckets, row["buckets"])),
            confidence_sum=before.confidence_sum + row["confidence_sum"],
            confidence_turns=before.confidence_turns + row["confidence_turns"],
        )
    return merged


def _tallied_stage(sample: StageSample) -> StageTally:
    return StageTally(
        turns=sample.turns,
        median_s=_histogram.rank(sample.buckets, _histogram.MEDIAN),
        p95_s=_histogram.rank(sample.buckets, _histogram.P95),
        confidence=sample.confidence_sum / sample.confidence_turns
        if sample.confidence_turns
        else None,
    )


def _judge_moved(name: str, before: JudgeRead | None, after: JudgeRead | None) -> DriftJudge:
    rates = (
        None if before is None else before.pass_rate,
        None if after is None else after.pass_rate,
    )
    return DriftJudge(
        name=name,
        before=_judge_tally(before),
        after=_judge_tally(after),
        moved=None if None in rates else _moved(rates[0], rates[1]),
        criteria_changed=before is not None
        and after is not None
        and before.criteria != after.criteria,
    )


def _judge_tally(read: JudgeRead | None) -> JudgeTally | None:
    if read is None:
        return None
    return JudgeTally(held=read.held, judged=read.judged, pass_rate=read.pass_rate)


def _stage_moved(
    key: StageKey, before: StageSample | None, after: StageSample | None
) -> DriftStage:
    stage, vendor, model = key
    first = None if before is None else _tallied_stage(before)
    second = None if after is None else _tallied_stage(after)
    return DriftStage(
        stage=stage,
        vendor=vendor or None,
        model=model or None,
        before=first,
        after=second,
        median_moved_s=None
        if first is None or second is None
        else _moved(first.median_s, second.median_s),
        p95_moved_s=None if first is None or second is None else _moved(first.p95_s, second.p95_s),
    )


def _moved(before: float | None, after: float | None) -> float | None:
    return None if before is None or after is None else after - before


async def _sealed_after(pool: Pool, org: str | None, since: float | None, after: str) -> list[str]:
    params = {"org": org, "since": since, "after": after, "limit": A_PAGE}
    async with pool.connection() as connection:
        rows = await (await connection.execute(SEALED_PAGE, params)).fetchall()
    return [str(row["log"]) for row in rows]


async def _entries_of(pool: Pool, call: str) -> list[Entry]:
    async with pool.connection() as connection:
        rows = await (await connection.execute(refold.ENTRIES, {"call": call})).fetchall()
    return [entry_of(row) for row in rows]


def _last_score(entries: Sequence[Entry]) -> CallScore | None:
    scored = next((entry for entry in reversed(entries) if entry.type == "call.score"), None)
    if scored is None:
        return None
    try:
        return CallScore.model_validate(scored.data)
    except ValueError:
        logger.warning("call %s: its call.score is not one drift reads", scored.call)
        return None
