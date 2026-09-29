"""An org's policy: retention, outbound calling rules, what a call says first; one row."""

from pinecall.postgres.pool import Pool
from pinecall.wire.rest.accounts import CallingHours, OrgPolicy, OrgPolicyRow

POLICY = """
SELECT retention_days, calling_from, calling_until, per_number_day, consent_everywhere,
       disclosure, recording_notice, set_by, set_at
FROM org_policy WHERE org = %(org)s
"""

PUT_POLICY = """
INSERT INTO org_policy (org, retention_days, calling_from, calling_until, per_number_day,
                        consent_everywhere, disclosure, recording_notice, set_by)
VALUES (%(org)s, %(retention_days)s, %(calling_from)s, %(calling_until)s, %(per_number_day)s,
        %(consent_everywhere)s, %(disclosure)s, %(recording_notice)s, %(set_by)s)
ON CONFLICT (org) DO UPDATE SET retention_days = excluded.retention_days,
    calling_from = excluded.calling_from, calling_until = excluded.calling_until,
    per_number_day = excluded.per_number_day, consent_everywhere = excluded.consent_everywhere,
    disclosure = excluded.disclosure, recording_notice = excluded.recording_notice,
    set_by = excluded.set_by, set_at = now()
"""


async def policy_of(pool: Pool, org: str) -> OrgPolicyRow:
    """The org's policy, and who set it last; an org nobody set has the platform's defaults."""
    async with pool.connection() as connection:
        row = await (await connection.execute(POLICY, {"org": org})).fetchone()
    if row is None:
        return OrgPolicyRow(policy=OrgPolicy(), set_by=None, set_at=None)
    hours = None
    if row["calling_from"] is not None:
        hours = CallingHours.model_validate(
            {"from": row["calling_from"], "until": row["calling_until"]}
        )
    return OrgPolicyRow(
        policy=OrgPolicy(
            retention_days=row["retention_days"],
            calling_hours=hours,
            per_number_day=row["per_number_day"],
            consent_everywhere=row["consent_everywhere"],
            disclosure=row["disclosure"],
            recording_notice=row["recording_notice"],
        ),
        set_by=str(row["set_by"]),
        set_at=row["set_at"].timestamp(),
    )


async def put_policy(pool: Pool, org: str, policy: OrgPolicy, *, by: str) -> None:
    """Replace the org's policy whole."""
    hours = policy.calling_hours
    params = {
        "org": org,
        "retention_days": policy.retention_days,
        "calling_from": None if hours is None else hours.from_,
        "calling_until": None if hours is None else hours.until,
        "per_number_day": policy.per_number_day,
        "consent_everywhere": policy.consent_everywhere,
        "disclosure": policy.disclosure,
        "recording_notice": policy.recording_notice,
        "set_by": by,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT_POLICY, params)
