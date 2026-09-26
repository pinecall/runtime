"""GET /v1/insights: one day of the reader's corner at a glance, counted off the call index."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter

from pinecall.api.deps import CallIndexDep, CallsKeyDep, OrgsDep
from pinecall.auth.corner import corner_of
from pinecall.log.store.call_index import Day
from pinecall_protocol.rest import Insights

router = APIRouter()

# An org carries no timezone, so a day is cut where the log's clock is: UTC. Said in the answer, so
# a console that draws "today" in a person's own zone knows which day it was handed.
TIMEZONE = "UTC"


# Three reads of the index and one of the quotas, whatever the day: never a fold of any log. The
# day is the corner's — a developer's sandbox calls are theirs, production's are the org's — and
# the budget is the org's across both worlds, because what an org spends is one bill.
@router.get("/v1/insights")
async def insights(
    key: CallsKeyDep, index: CallIndexDep, orgs: OrgsDep, day: date | None = None
) -> Insights:
    """The day asked for, or today: its calls, how they went, their cost, and the month's spend."""
    asked = day or datetime.now(UTC).date()
    whose = corner_of(key)
    counted = await index.a_day(whose.org, whose.env, whose.holder or "", _opening(asked))
    month, next_month = _the_month_of(asked)
    spent = await index.spent_between(key.org, _opening(month), _opening(next_month))
    quotas = await orgs.quotas_of(key.org)
    return Insights.model_validate(
        {
            "day": asked.isoformat(),
            "timezone": TIMEZONE,
            "conversations": {"today": counted.calls, "yesterday": counted.yesterday},
            "resolved_rate": _resolved(counted),
            "median_e2e_s": counted.median_e2e,
            "spend_eur": counted.spent,
            "channels": {door: counted.channels.get(door, 0) for door in DOORS},
            "sessions_total": counted.total,
            "live": counted.live,
            "agents": [
                {"slug": one.slug, "today": one.calls, "score": one.score} for one in counted.agents
            ],
            "budget": {"limit_eur": quotas.budget_eur, "spent_eur_month": spent},
        }
    )


DOORS = ("phone", "web", "whatsapp")


def _resolved(counted: Day) -> float | None:
    """The share of the day's finished calls nobody took from the agent; None when none finished."""
    return counted.unescalated / counted.finished if counted.finished else None


def _opening(day: date) -> float:
    """When a day opens, in the log's clock."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


def _the_month_of(day: date) -> tuple[date, date]:
    """The first day of the day's calendar month, and the first day of the month after it."""
    first = day.replace(day=1)
    after = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    return first, after
