"""GET /v1/limits: what the key's org may use and has used here, and where it pays for more."""

from __future__ import annotations

from fastapi import APIRouter

from pinecall.api._deps import AdmissionDep, KeyDep, MembersDep, RoutesDep, SettingsDep
from pinecall.api._live import LiveDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.types import Quotas
from pinecall_protocol.rest import Limit, Limits

router = APIRouter()


# Any key of the org: a console, a CLI and an agent's own process may all want to say "12 of 30
# minutes", and none of the numbers is a secret from somebody who holds a key of that org. Each
# `used` is counted where the gate counts it — the Meter for what was consumed, the live tables for
# what stands now — so the page and the refusal can never disagree. One instance's numbers: the
# sandbox's minutes are the sandbox's, which is where a trial spends them.
@router.get("/v1/limits")
async def limits(
    key: KeyDep,
    admission: AdmissionDep,
    registry: RegistryDep,
    live: LiveDep,
    members: MembersDep,
    table: RoutesDep,
    settings: SettingsDep,
) -> Limits:
    """Each quota as {limit, used}, the box's lending, and the billing URL, for the key's org."""
    org = key.org
    quotas = await admission.quotas_of(org)
    totals = await admission.consumed(org)
    return Limits(
        minutes=_of(quotas.minutes, totals.minutes),
        messages=_of(quotas.messages, totals.messages),
        llm_tokens=_of(quotas.llm_tokens, totals.input_tokens + totals.output_tokens),
        concurrent_calls=_of(quotas.concurrent_calls, live.running(org)),
        agents=_of(quotas.agents, len(registry.slugs(org))),
        seats=_of(quotas.seats, await members.seated(org)),
        numbers=_of(quotas.numbers, await table.managed_by(org)),
        lends=_lent(quotas),
        billing_url=settings.billing_url,
    )


def _of(limit: int | None, used: float) -> Limit:
    """One quota as the wire says it: null is no limit."""
    return Limit(limit=limit, used=used)


def _lent(quotas: Quotas) -> list[str] | None:
    """The lending, sorted so the same row always reads the same."""
    return None if quotas.lends is None else sorted(quotas.lends)
