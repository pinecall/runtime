"""The bodies of the org's meters: the usage feed, a window's insights, its drift, the limits."""

from typing import Literal

from pinecall.domain.names import Env
from pinecall.wire.frames import WireModel


class UsageTotals(WireModel):
    """What was consumed: calls, minutes, turns, tokens, characters, judges asked, the cost."""

    calls: int
    minutes: float
    messages: int
    input_tokens: int
    output_tokens: int
    characters: int
    judge_calls: int
    evals: int
    simulations: int
    cost_usd: float


# Flat, as v1 wrote it: a billing layer that reads the feed today reads it unchanged at the cutover.
class UsageRow(WireModel):
    """One metered entry: whose it was, which call, what it consumed, and where the feed is."""

    cursor: int
    org: str
    agent: str
    call: str
    type: str
    at: float
    minutes: float
    messages: int
    input_tokens: int
    output_tokens: int
    characters: int
    judge_calls: int
    evals: int
    # A call.summary of a call a simulated caller played: billed as one simulation, not minutes.
    simulated: bool
    cost_usd: float


class UsagePage(WireModel):
    """GET /v1/usage: the org's metered rows after the cursor, their totals, the next cursor."""

    rows: list[UsageRow]
    totals: UsageTotals | None
    next: int | None


class BoxUsagePage(WireModel):
    """GET /v1/ops/usage: every org's metered rows after the cursor, totals per org, the next."""

    rows: list[UsageRow]
    totals: dict[str, UsageTotals]
    next: int | None


class InsightsConversations(WireModel):
    """How many calls started in the window, and in the window of the same length before it."""

    now: int
    before: int


class InsightsChannels(WireModel):
    """The window's calls by the door they came in by."""

    phone: int
    web: int
    whatsapp: int


class InsightsSpend(WireModel):
    """What one agent's window cost by stage, in US dollars, and per minute of its ended calls."""

    llm_usd: float
    stt_usd: float
    tts_usd: float
    phone_usd: float
    platform_usd: float
    minutes: float
    per_minute_usd: float | None


class InsightsAgent(WireModel):
    """One agent's window: its calls, the share of judgments it held, what it cost by stage."""

    slug: str
    calls: int
    score: float | None
    spend: InsightsSpend


class InsightsStage(WireModel):
    """One stage of the window's turns on one vendor and model: how slow, how sure the ears were."""

    stage: Literal["stt", "llm", "tts"]
    vendor: str | None
    model: str | None
    turns: int
    median_s: float | None
    p95_s: float | None
    confidence: float | None


class InsightsBudget(WireModel):
    """What the org may spend in a month and what it has spent so far, both worlds together."""

    limit_usd: int | None
    spent_usd_month: float


class InsightsEnding(WireModel):
    """One way the window's calls ended, and how many did."""

    reason: str
    count: int


class InsightsDay(WireModel):
    """One UTC day of the window: its calls by door, what they cost, how their judges answered."""

    day: str
    phone: int
    web: int
    whatsapp: int
    spend_usd: float
    judged: int
    passed: int


class SeriesStage(WireModel):
    """One stage over one day's turns: how many, and how slow at the median and the tail."""

    stage: Literal["stt", "llm", "tts"]
    turns: int
    median_s: float | None
    p95_s: float | None


class SeriesJudge(WireModel):
    """One judge over one day: the verdicts that held, and those settled."""

    name: str
    held: int
    judged: int


class SeriesDay(WireModel):
    """One UTC day of the window, every number the Observability screen draws."""

    day: str
    calls: int
    finished: int
    escalated: int
    spend_usd: float
    mean_length_s: float | None
    e2e_median_s: float | None
    e2e_p95_s: float | None
    endings: list[InsightsEnding]
    stages: list[SeriesStage]
    judges: list[SeriesJudge]
    tools_ran: int
    tools_failed: int


class Series(WireModel):
    """GET /v1/insights/series: the window day by day, every number a chart draws."""

    day: str
    days: int
    agent: str | None
    timezone: str
    series: list[SeriesDay]


class Insights(WireModel):
    """GET /v1/insights: whole UTC days of the key's world and scope, counted off the call index."""

    day: str
    days: int
    timezone: str
    conversations: InsightsConversations
    resolved_rate: float | None
    median_e2e_s: float | None
    spend_usd: float
    channels: InsightsChannels
    judged: int
    passed: int
    escalated: int
    mean_length_s: float | None
    endings: list[InsightsEnding]
    series: list[InsightsDay]
    sessions_total: int
    live: int
    agents: list[InsightsAgent]
    stages: list[InsightsStage]
    budget: InsightsBudget


class DriftSide(WireModel):
    """One side of a drift as asked: a day, a version; and the versions its calls ran."""

    day: str | None
    version: int | None
    versions: list[int]


class JudgeTally(WireModel):
    """One judge on one side: its verdicts that held, those settled, and the share that held."""

    held: int
    judged: int
    pass_rate: float | None


class DriftJudge(WireModel):
    """One judge on both sides, how far its pass rate moved, and whether its question changed."""

    name: str
    before: JudgeTally | None
    after: JudgeTally | None
    moved: float | None
    criteria_changed: bool


class StageTally(WireModel):
    """One stage on one side: its turns, median and p95 in seconds, the ears' confidence."""

    turns: int
    median_s: float | None
    p95_s: float | None
    confidence: float | None


class DriftStage(WireModel):
    """One stage on one vendor and model on both sides, and how far its median and p95 moved."""

    stage: Literal["stt", "llm", "tts"]
    vendor: str | None
    model: str | None
    before: StageTally | None
    after: StageTally | None
    median_moved_s: float | None
    p95_moved_s: float | None


class DriftVersion(WireModel):
    """A version of the agent's settings a side ran or that was set between: who, when, why."""

    version: int
    holder: str
    author: str
    note: str | None
    set_at: float
    ran_before: bool
    ran_after: bool


class Drift(WireModel):
    """GET /v1/insights/drift: what moved between two days or versions of an agent, and why."""

    agent: str
    world: Env
    before: DriftSide
    after: DriftSide
    judges: list[DriftJudge]
    stages: list[DriftStage]
    versions: list[DriftVersion]


class Limit(WireModel):
    """One quota: the limit the org was given, and how much of it is used."""

    limit: int | None
    used: float


class Limits(WireModel):
    """GET /v1/limits: what the key's org may use in the world and has used, and its lends."""

    minutes: Limit
    messages: Limit
    llm_tokens: Limit
    concurrent_calls: Limit
    agents: Limit
    seats: Limit
    numbers: Limit
    lends: list[str] | None
    billing_url: str | None
    world: Env
