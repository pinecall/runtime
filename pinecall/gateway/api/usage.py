"""The org's meters: the usage feed, a day's insights, an agent's drift, what the org may use."""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pinecall.channels import routes
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.org import Quotas
from pinecall.gateway._deps import ActingDep, CallsKey, GatewayDep, ScopeDep, UsageKey
from pinecall.log import drift, queries
from pinecall.log.drift import Side, Tally
from pinecall.log.facts import A_DAY_S
from pinecall.log.reduce import Usage, UsageRow, totals_by_org
from pinecall.log.store import DEFAULT_LIMIT
from pinecall.tenancy import admission, people, scopes, usage
from pinecall.tenancy.keys import check_agent
from pinecall.wire.rest.usage import (
    Drift,
    DriftSide,
    DriftVersion,
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


A_VERSION = re.compile(r"v(\d+)")


NOT_A_SIDE = "{text!r} is neither a day (YYYY-MM-DD) nor a version (v3)"


BOTH_SIDES = "a drift between versions names both: before=v3&after=v4"


class DriftQuery(BaseModel):
    """What a drift compares: an agent, two days (today and the day before) or two versions."""

    agent: str
    before: str | None = None
    after: str | None = None


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
    spent = await usage.spent_in(pool, key.org, counted_on)
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
        stages=await drift.stages_of_day(pool, scope, counted_on),
        budget=InsightsBudget(limit_usd=quotas.budget_usd, spent_usd_month=spent),
    )


# Folded when each call was sealed (log/drift.py): two days or two versions read a few rows each.
@router.get("/v1/insights/drift")
async def insights_drift(
    key: CallsKey, scope: ScopeDep, gateway: GatewayDep, query: Annotated[DriftQuery, Query()]
) -> Drift:
    """What moved between two days or versions of an agent, and the versions run or set."""
    check_agent(key.bearer, query.agent)
    pool = gateway.connections.pool
    before, after = _sides(query)
    tallies = (
        await drift.tally(pool, scope, query.agent, before),
        await drift.tally(pool, scope, query.agent, after),
    )
    judges, stages = drift.compared(*tallies)
    ran = tallies[0].versions | tallies[1].versions
    days = [side.day for side in (before, after) if side.day is not None]
    window = (_opening(min(days)), _opening(max(days)) + A_DAY_S) if days else (0.0, 0.0)
    noted = await scopes.versions_noted(pool, scope, query.agent, ran, window)
    return Drift(
        agent=query.agent,
        world=scope.env,
        before=_side(before, tallies[0]),
        after=_side(after, tallies[1]),
        judges=judges,
        stages=stages,
        versions=[
            DriftVersion(
                version=row.version,
                holder=row.holder,
                author=row.author,
                note=row.note,
                set_at=row.set_at.timestamp(),
                ran_before=row.version in tallies[0].versions,
                ran_after=row.version in tallies[1].versions,
            )
            for row in noted
        ],
    )


# Any key of the org. `used` reads what admission reads, so this page and a refusal agree.
@router.get("/v1/limits")
async def limits(key: ActingDep, gateway: GatewayDep) -> Limits:
    """Each quota of the key's world as {limit, used}, the lends, and where to buy more."""
    pool, org, env = gateway.connections.pool, key.org, key.env
    quotas = await admission.quotas_of(pool, org, env)
    used = await usage.used(pool, org, env, usage.month_of(gateway.logs.store.clock()))
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


# Two days by default: today and the day before it, in UTC.
def _sides(query: DriftQuery) -> tuple[Side, Side]:
    after = _side_of(query.after) if query.after is not None else Side(day=datetime.now(UTC).date())
    if query.before is not None:
        return _side_of(query.before), after
    if after.day is None:
        raise DeclarationRefused(BOTH_SIDES)
    return Side(day=after.day - timedelta(days=1)), after


def _side_of(text: str) -> Side:
    version = A_VERSION.fullmatch(text)
    if version is not None:
        return Side(version=int(version.group(1)))
    try:
        return Side(day=date.fromisoformat(text))
    except ValueError as refused:
        raise DeclarationRefused(NOT_A_SIDE.format(text=text)) from refused


def _side(side: Side, tallied: Tally) -> DriftSide:
    return DriftSide(
        day=None if side.day is None else side.day.isoformat(),
        version=side.version,
        versions=sorted(tallied.versions),
    )


def _lends(quotas: Quotas) -> list[str] | None:
    return None if quotas.lends is None else sorted(quotas.lends)


def _opening(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
