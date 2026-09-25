"""The org a verified sign-up makes: the row, what the policy allows it, its admin and their key."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from pinecall.api.members import member_as_json
from pinecall.auth.codes import LoginCodes
from pinecall.auth.keys import Keys
from pinecall.auth.members import Members
from pinecall.auth.persons import a_persons_key
from pinecall.auth.signups import Pending
from pinecall.extensions import Extensions
from pinecall.orgs.table import Orgs
from pinecall.types import Env, Quotas

TAKEN = "{slug} is taken: pick another name for the org"

# The label of the first key and the seat it names: the door it came through.
SIGNED_UP = "signup"


# Everything here happens only once the address has proved itself: an org whose email nobody
# answered for is never a row, so a fake email leaves nothing standing and takes no slug.
async def the_org_made(  # noqa: PLR0913 — the sign-up, and every store making an org writes
    pending: Pending,
    device: str | None,
    world: Env,
    orgs: Orgs,
    members: Members,
    keys: Keys,
    codes: LoginCodes,
    extensions: Extensions,
) -> dict[str, Any]:
    """The org made, allowed what its gateway's policy says, its admin active, their first key."""
    # Counted before the org exists: the orgs this person already had here (extensions/points.py).
    already = len(await members.orgs_of(pending.email))
    org = await orgs.create(pending.slug, pending.name or pending.slug)
    if org is None:
        raise HTTPException(409, TAKEN.format(slug=pending.slug))
    # What this org may do is whoever charges for it's to say, through the point a package plugged
    # into (extensions/points.py); the runtime's own answer is no limit, and no limit is no row.
    allowed = extensions.admitted(org, pending.email, world, already)
    if allowed != Quotas():
        await orgs.set_quotas(org.id, allowed)
    # The org is new, so nobody holds the email yet: the invitation is minted and spent in one
    # breath, the very path a person invited later walks, and the member ends `active`.
    invited = await members.invite(org.id, pending.email, pending.person, "admin", ())
    assert invited is not None
    # A person who already has a password on this box is seated at once and keeps it: one person,
    # one password (auth/members.py). Anybody else spends the invitation with the one they chose.
    member = (
        invited.member
        if invited.token is None
        else await members.accept(invited.token, pending.hashed)
    )
    assert member is not None
    # The admin's own key, which opens production too: an admin always does (0039).
    issued = await a_persons_key(keys, member, device or pending.device or SIGNED_UP, world)
    minted = codes.mint(issued.record)
    return {
        **issued.as_json,
        "slug": org.slug,
        "member": member_as_json(member),
        "code": minted.code,
        "code_expires_at": minted.expires_at,
    }
