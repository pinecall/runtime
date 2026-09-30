"""The org's meters: the usage feed, one day's insights, and what the org may use."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from pinecall.channels import routes
from pinecall.domain.org import Quotas
from pinecall.gateway._deps import ActingDep, CallsKey, GatewayDep, ScopeDep, UsageKey
from pinecall.log import queries
from pinecall.log.reduce import Usage, UsageRow, totals_by_org
from pinecall.log.store import DEFAULT_LIMIT
from pinecall.tenancy import admission, people
from pinecall.wire.rest.usage import (
    Insights,
    InsightsAgent,
    InsightsBudget,
    InsightsChannels,
    InsightsConversations,
    Limit,
    Limits,
    UsagePage,
    UsageTotals,
)
from pinecall.wire.rest.usage import UsageRow as UsageRowResponse

router = APIRouter()


# An org carries no timezone, so a day is cut in UTC and the answer says so.
TIMEZONE = "UTC"


DECEMBER = 12


# The cursor is the store's position of the last row read: a billing consumer resumes from it
# and counts nothing twice.
@router.get("/v1/usage")
async def usage_feed(
    key: UsageKey,
    gateway: GatewayDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=DEFAULT_LIMIT)] = DEFAULT_LIMIT,
) -> UsagePage:
    """The org's metered rows after the cursor, their totals, and the next cursor."""
    page = await queries.metered_page(gateway.logs.store, after=after, limit=limit, org=key.org)
    totals = totals_by_org(page.rows).get(key.org)
    return UsagePage(
        rows=[usage_row_response(row) for row in page.rows],
        totals=None if totals is None else usage_totals(totals),
        next=page.next,
    )


# The day is the scope's; the budget is the org's across both worlds, since it is one bill.
@router.get("/v1/insights")
async def insights(
    key: CallsKey, scope: ScopeDep, gateway: GatewayDep, day: date | None = None
) -> Insights:
    """One day of the key's world and scope at a glance, and the month's spend."""
    pool = gateway.connections.pool
    counted_on = day or datetime.now(UTC).date()
    counted = await queries.counted_day(pool, scope, _opening(counted_on))
    month, next_month = _month_of(counted_on)
    spent = await queries.spent_between(pool, key.org, _opening(month), _opening(next_month))
    quotas = await admission.quotas_of(pool, key.org, key.env)
    return Insights(
        day=counted_on.isoformat(),
        timezone=TIMEZONE,
        conversations=InsightsConversations(today=counted.calls, yesterday=counted.yesterday),
        resolved_rate=counted.unescalated / counted.finished if counted.finished else None,
        median_e2e_s=counted.median_e2e,
        spend_usd=counted.spent,
        channels=InsightsChannels(
            phone=counted.channels["phone"],
            web=counted.channels["web"],
            whatsapp=counted.channels["whatsapp"],
        ),
        sessions_total=counted.total,
        live=counted.live,
        agents=[
            InsightsAgent(slug=agent.slug, today=agent.calls, score=agent.score)
            for agent in counted.agents
        ],
        budget=InsightsBudget(limit_usd=quotas.budget_usd, spent_usd_month=spent),
    )


# Any key of the org. `used` reads what admission reads, so this page and a refusal agree.
@router.get("/v1/limits")
async def limits(key: ActingDep, gateway: GatewayDep) -> Limits:
    """Each quota of the key's world as {limit, used}, the lends, and where to buy more."""
    pool, org, env = gateway.connections.pool, key.org, key.env
    quotas = await admission.quotas_of(pool, org, env)
    used = await admission.used(pool, org, env)
    return Limits(
        minutes=Limit(limit=quotas.minutes, used=used.minutes),
        messages=Limit(limit=quotas.messages, used=used.messages),
        llm_tokens=Limit(limit=quotas.llm_tokens, used=used.input_tokens + used.output_tokens),
        concurrent_calls=Limit(limit=quotas.concurrent_calls, used=gateway.live.running(org, env)),
        agents=Limit(limit=quotas.agents, used=len(gateway.sockets.slugs(org))),
        seats=Limit(limit=quotas.seats, used=await people.seated(pool, org)),
        numbers=Limit(limit=quotas.numbers, used=await routes.managed_in(pool, org, env)),
        lends=_lends(quotas),
        billing_url=gateway.connections.settings.billing_url,
        world=env,
    )


def usage_row_response(row: UsageRow) -> UsageRowResponse:
    """A folded row as the feed sends it."""
    return UsageRowResponse(
        cursor=row.cursor,
        org=row.org,
        agent=row.agent,
        call=row.call,
        type=row.type,
        at=row.at,
        minutes=row.used.minutes,
        messages=row.used.messages,
        input_tokens=row.used.input_tokens,
        output_tokens=row.used.output_tokens,
        characters=row.used.characters,
        judge_calls=row.used.judge_calls,
        cost_usd=row.used.cost_usd,
    )


def usage_totals(used: Usage) -> UsageTotals:
    """A sum of usage as the feed sends it."""
    return UsageTotals(
        calls=used.calls,
        minutes=used.minutes,
        messages=used.messages,
        input_tokens=used.input_tokens,
        output_tokens=used.output_tokens,
        characters=used.characters,
        judge_calls=used.judge_calls,
        cost_usd=used.cost_usd,
    )


def _lends(quotas: Quotas) -> list[str] | None:
    return None if quotas.lends is None else sorted(quotas.lends)


def _opening(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


def _month_of(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    if first.month == DECEMBER:
        return first, first.replace(year=first.year + 1, month=1)
    return first, first.replace(month=first.month + 1)
