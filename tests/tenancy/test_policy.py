"""Tests for an org's policy: nobody set it, replaced whole, its hours and sentences come back."""

import pytest
from pydantic import ValidationError

from pinecall.postgres.pool import Pool
from pinecall.tenancy import policy
from pinecall.wire.rest.accounts import CallingHours, OrgPolicy
from tests.conftest import postgres
from tests.tenancy.conftest import an_org


@postgres
async def test_an_org_nobody_set_has_the_platforms_defaults_and_says_nobody_set_it(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    row = await policy.policy_of(pool, org.id)
    assert row.policy == OrgPolicy()
    assert (row.set_by, row.set_at) == (None, None)


@postgres
async def test_a_policy_is_replaced_whole_and_names_who_set_it(pool: Pool) -> None:
    org = await an_org(pool)
    hours = CallingHours.model_validate({"from": 9, "until": 19})
    await policy.put_policy(
        pool, org.id, OrgPolicy(retention_days=30, calling_hours=hours, per_number_day=2), by="m_1"
    )
    first = await policy.policy_of(pool, org.id)
    assert first.policy.calling_hours == hours
    assert (first.policy.retention_days, first.policy.per_number_day) == (30, 2)
    await policy.put_policy(pool, org.id, OrgPolicy(retention_days=90), by="m_2")
    row = await policy.policy_of(pool, org.id)
    assert (row.policy.retention_days, row.policy.calling_hours, row.set_by) == (90, None, "m_2")


@postgres
async def test_the_disclosure_and_the_notice_come_back_as_they_were_set(pool: Pool) -> None:
    org = await an_org(pool)
    own = OrgPolicy(disclosure="Hi, this is Acme's virtual assistant.", recording_notice=False)
    await policy.put_policy(pool, org.id, own, by="m_1")
    row = await policy.policy_of(pool, org.id)
    assert (row.policy.disclosure, row.policy.recording_notice) == (own.disclosure, False)
    await policy.put_policy(pool, org.id, OrgPolicy(disclosure=""), by="m_1")
    row = await policy.policy_of(pool, org.id)
    assert (row.policy.disclosure, row.policy.recording_notice) == ("", True)


def test_calling_hours_are_a_window_with_at_least_one_hour() -> None:
    with pytest.raises(ValidationError, match="leave no hour"):
        CallingHours.model_validate({"from": 20, "until": 9})
    with pytest.raises(ValidationError):
        CallingHours.model_validate({"from": 8, "until": 25})
