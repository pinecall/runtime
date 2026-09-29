"""The bodies of the org's meters: the usage feed, one day's insights, and the limits."""

from pinecall.domain.names import Env
from pinecall.wire.frames import WireModel


class UsageTotals(WireModel):
    """What was consumed: calls, minutes, turns, tokens, characters, judge calls, the cost."""

    calls: int
    minutes: float
    messages: int
    input_tokens: int
    output_tokens: int
    characters: int
    judge_calls: int
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
    """How many calls started on the day, and on the day before it."""

    today: int
    yesterday: int


class InsightsChannels(WireModel):
    """The day's calls by the door they came in by."""

    phone: int
    web: int
    whatsapp: int


class InsightsAgent(WireModel):
    """One agent's day: its calls, and the share of judgments it held."""

    slug: str
    today: int
    score: float | None


class InsightsBudget(WireModel):
    """What the org may spend in a month and what it has spent so far, both worlds together."""

    limit_usd: int | None
    spent_usd_month: float


class Insights(WireModel):
    """GET /v1/insights: one day of the key's world and scope, counted off the call index."""

    day: str
    timezone: str
    conversations: InsightsConversations
    resolved_rate: float | None
    median_e2e_s: float | None
    spend_usd: float
    channels: InsightsChannels
    sessions_total: int
    live: int
    agents: list[InsightsAgent]
    budget: InsightsBudget


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
