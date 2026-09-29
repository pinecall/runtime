"""Tests for what an org may dial: shape, strangers, the callee's hours, the pace, the ledger."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pinecall.domain.errors import DeclarationRefused, NotAllowed, QuotaExhausted
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import consents, dial_policy, orgs, policy
from pinecall.tenancy.consents import Given
from pinecall.tenancy.dial_policy import Dial
from pinecall.wire.rest.accounts import CallingHours, OrgPolicy
from pinecall.wire.rest.numbers import Consent
from tests.conftest import postgres

HER_PHONE = "+59899000001"
SHOWN = "+13617334133"
NOON = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
async def org(pool: Pool) -> str:
    """An org nobody ever called, with the default guards."""
    return (await orgs.create(pool, "clinica", "Clinica")).id


def dial_of(
    org: str,
    to: str = HER_PHONE,
    env: Env = "production",
    call: str = "call_1",
    at: datetime = NOON,
) -> Dial:
    """A dial of the org's agent to that number, at noon UTC unless said."""
    return Dial(Scope(org, env), "recepcion", to, SHOWN, "m_ana", call, at)


async def ledger(pool: Pool) -> list[tuple[str, str | None, str | None]]:
    """Every dial written down: who asked, what refused it, the call it became."""
    async with pool.connection() as connection:
        rows = await (
            await connection.execute("select asked_by, refused, call from dials order by id")
        ).fetchall()
    return [(row["asked_by"], row["refused"], row["call"]) for row in rows]


# ── what a dial may reach ──


# ── the guards ──


def test_the_defaults_are_a_call_back_box_and_a_guard_is_never_negative() -> None:
    guards = dial_policy.Guards()
    assert (guards.dial_anywhere, guards.per_minute, guards.per_day, guards.max_duration_s) == (
        False,
        6,
        200,
        600,
    )
    with pytest.raises(ValueError, match="per_minute"):
        dial_policy.Guards(per_minute=-1)


@postgres
async def test_a_number_that_never_called_this_world_is_a_stranger_until_an_operator_lifts_it(
    pool: Pool, org: str
) -> None:
    with pytest.raises(NotAllowed, match="stranger"):
        await dial_policy.guard_dial(pool, dial_of(org))
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True))
    await dial_policy.guard_dial(pool, dial_of(org))
    assert await ledger(pool) == [("m_ana", "stranger", None), ("m_ana", None, "call_1")]


@postgres
async def test_a_bad_shape_is_written_down_and_refused_with_its_guard(pool: Pool, org: str) -> None:
    with pytest.raises(DeclarationRefused, match=r"\(shape\)"):
        await dial_policy.guard_dial(pool, dial_of(org, to="+881600000"))
    assert await ledger(pool) == [("m_ana", "shape", None)]


@postgres
async def test_the_pace_counts_the_refusals_too_and_a_days_worth_has_its_own_words(
    pool: Pool, org: str
) -> None:
    await dial_policy.put_guards(
        pool,
        org,
        dial_policy.Guards(dial_anywhere=True, per_minute=2, per_day=100),
    )
    with pytest.raises(DeclarationRefused):
        await dial_policy.guard_dial(pool, dial_of(org, to="bad"))
    await dial_policy.guard_dial(pool, dial_of(org))
    with pytest.raises(QuotaExhausted, match="last minute of its 2 \\(too_fast\\)"):
        await dial_policy.guard_dial(pool, dial_of(org))
    await dial_policy.put_guards(
        pool,
        org,
        dial_policy.Guards(dial_anywhere=True, per_minute=50, per_day=3),
    )
    with pytest.raises(QuotaExhausted, match="today of its 3 \\(too_many\\)"):
        await dial_policy.guard_dial(pool, dial_of(org))


@postgres
async def test_two_dials_at_once_cannot_both_take_the_last_slot(pool: Pool, org: str) -> None:
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True, per_minute=1))
    both = await asyncio.gather(
        dial_policy.guard_dial(pool, dial_of(org, call="call_a")),
        dial_policy.guard_dial(pool, dial_of(org, call="call_b")),
        return_exceptions=True,
    )
    assert sorted(type(item).__name__ for item in both) == ["Guards", "QuotaExhausted"]


@postgres
async def test_a_second_leg_skips_the_stranger_fence_and_the_calls_own_first_leg_passes(
    pool: Pool, org: str
) -> None:
    await dial_policy.guard_second_leg(pool, dial_of(org, to="+34910000000"))
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True, per_minute=2))
    await dial_policy.guard_dial(pool, dial_of(org, call="call_2"))
    await dial_policy.guard_second_leg(pool, dial_of(org, call="call_2"))
    assert len(await ledger(pool)) == 2


# ── the called number's hours, and how often one number is rung ──

LOS_ANGELES = "+13105550142"
TOLL_FREE = "+18005550142"
MONTEVIDEO = "+59829001199"


async def dialling_anywhere(pool: Pool, org: str) -> None:
    await dial_policy.put_guards(pool, org, dial_policy.Guards(dial_anywhere=True))


async def consented(pool: Pool, org: str, *numbers: str) -> None:
    """Consent on file for each number, so a test of hours or counts is not refused for it."""
    for number in numbers:
        await consents.give(pool, Scope(org), number, Given("express", "a test", "m_ana"))


@postgres
async def test_a_us_number_rings_from_eight_to_nine_in_its_own_time_and_never_outside(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    await consented(pool, org, LOS_ANGELES)
    early = datetime(2026, 9, 29, 14, 30, tzinfo=UTC)  # 07:30 in Los Angeles
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, at=early))
    at_eight = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
    await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call="call_2", at=at_eight))
    late = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)  # 21:00 in Los Angeles
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call="call_3", at=late))
    assert [row[1] for row in await ledger(pool)] == ["quiet_hours", None, "quiet_hours"]


@postgres
async def test_a_us_number_no_zone_can_hold_is_refused(pool: Pool, org: str) -> None:
    await dialling_anywhere(pool, org)
    await consented(pool, org, TOLL_FREE)
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, TOLL_FREE))


@postgres
async def test_the_org_narrows_the_us_hours_and_never_widens_them(pool: Pool, org: str) -> None:
    await dialling_anywhere(pool, org)
    await consented(pool, org, LOS_ANGELES)
    wide = CallingHours.model_validate({"from": 6, "until": 23})
    await policy.put_policy(pool, org, OrgPolicy(calling_hours=wide), by="m_1")
    seven = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)  # 07:00 in Los Angeles
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, at=seven))
    narrow = CallingHours.model_validate({"from": 10, "until": 17})
    await policy.put_policy(pool, org, OrgPolicy(calling_hours=narrow), by="m_1")
    nine = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 09:00 in Los Angeles
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call="call_2", at=nine))


@postgres
async def test_another_country_is_held_to_the_orgs_hours_only_when_it_set_some(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    eleven_pm = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)  # 23:00 in Montevideo
    await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, at=eleven_pm))
    hours = CallingHours.model_validate({"from": 9, "until": 20})
    await policy.put_policy(pool, org, OrgPolicy(calling_hours=hours), by="m_1")
    with pytest.raises(NotAllowed, match="quiet_hours"):
        await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, call="call_2", at=eleven_pm))


@postgres
async def test_the_sandbox_and_the_askers_own_phone_are_held_to_no_hours(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    early = datetime(2026, 9, 29, 14, 30, tzinfo=UTC)
    await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, env="sandbox", at=early))
    own = replace(dial_of(org, LOS_ANGELES, call="call_2", at=early), own_phone=True)
    await dial_policy.guard_dial(pool, own)


@postgres
async def test_a_us_number_is_rung_three_times_a_day_and_the_fourth_is_refused(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    await consented(pool, org, LOS_ANGELES, "+12125550142")
    noon_there = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    for n in range(3):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call=f"c{n}", at=noon_there))
    with pytest.raises(QuotaExhausted, match="too_often"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call="c4", at=noon_there))
    # Another number of the same org is its own count.
    await dial_policy.guard_dial(pool, dial_of(org, "+12125550142", call="c5", at=noon_there))


@postgres
async def test_another_country_has_no_daily_count_until_the_org_sets_one(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    for n in range(4):
        await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, call=f"c{n}"))
    await policy.put_policy(pool, org, OrgPolicy(per_number_day=4), by="m_1")
    with pytest.raises(QuotaExhausted, match="too_often"):
        await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, call="c9"))


def test_a_number_in_two_zones_is_held_to_both() -> None:
    zones = dial_policy.zones_of("+19075550142")
    assert set(zones) == {"America/Adak", "America/Anchorage"}
    eight_in_anchorage = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 07:00 in Adak
    assert not dial_policy.within(zones, eight_in_anchorage, (8, 21))


# ── consent and the do-not-call list ──

A_CONSENT = Consent(kind="express", source="the booking form", text="Yes, call me back")


@postgres
async def test_a_us_number_in_production_needs_consent_on_file_or_with_the_dial(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    at_noon = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    with pytest.raises(NotAllowed, match="no_consent"):
        await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, at=at_noon))
    with_one = replace(dial_of(org, LOS_ANGELES, call="call_2", at=at_noon), consent=A_CONSENT)
    await dial_policy.guard_dial(pool, with_one)
    # The consent the dial carried is on file now: the next call needs none.
    await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, call="call_3", at=at_noon))
    history = await consents.history(pool, Scope(org), LOS_ANGELES)
    assert [(row.kind, row.call) for row in history.rows] == [("express", "call_2")]


@postgres
async def test_an_opt_out_refuses_the_number_whatever_consent_the_dial_carries(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    await consents.give(
        pool, Scope(org), MONTEVIDEO, Given("opt_out", "the caller asked", "agent:x")
    )
    with pytest.raises(NotAllowed, match="do_not_call"):
        await dial_policy.guard_dial(pool, replace(dial_of(org, MONTEVIDEO), consent=A_CONSENT))
    assert await consents.standing_of(pool, Scope(org), MONTEVIDEO) == "opted_out"
    await consents.give(pool, Scope(org), MONTEVIDEO, Given("written", "a signed form", "m_ana"))
    await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, call="call_2"))


@postgres
async def test_another_country_needs_no_consent_until_the_org_asks_for_it_everywhere(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO))
    await policy.put_policy(pool, org, OrgPolicy(consent_everywhere=True), by="m_1")
    with pytest.raises(NotAllowed, match="no_consent"):
        await dial_policy.guard_dial(pool, dial_of(org, MONTEVIDEO, call="call_2"))


@postgres
async def test_the_sandbox_and_the_askers_own_phone_are_held_to_no_list_and_no_consent(
    pool: Pool, org: str
) -> None:
    await dialling_anywhere(pool, org)
    await consents.give(pool, Scope(org, "sandbox"), LOS_ANGELES, Given("opt_out", "x", "m_ana"))
    at_noon = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    await dial_policy.guard_dial(pool, dial_of(org, LOS_ANGELES, env="sandbox", at=at_noon))
    own = replace(dial_of(org, LOS_ANGELES, call="call_2", at=at_noon), own_phone=True)
    await dial_policy.guard_dial(pool, own)
