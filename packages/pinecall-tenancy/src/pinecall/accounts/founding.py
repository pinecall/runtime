"""The org a verified sign-up founds: the row, what its policy allows, its admin and their key."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.auth.keys import Issued, Keys
from pinecall.auth.login_codes import Code, LoginCodes
from pinecall.auth.members import Members
from pinecall.auth.person_keys import mint_person_key
from pinecall.auth.signups import Pending
from pinecall.extensions import Extensions
from pinecall.orgs.records import SLUG_TAKEN, Orgs, SlugTaken
from pinecall.types import Env, Member, Org, Quotas

# The label of the first key and the seat it names: the door it came through.
SIGNED_UP = "signup"


@dataclass(frozen=True)
class OrgFounded:
    """What founding left standing: the org, its first admin, their key, a code for the console."""

    org: Org
    admin: Member
    issued: Issued
    code: Code


# Everything here happens only once the address has proved itself: an org whose email nobody
# answered for is never a row, so a fake email leaves nothing standing and takes no slug.
async def make_org(
    pending: Pending,
    device: str | None,
    world: Env,
    orgs: Orgs,
    members: Members,
    keys: Keys,
    codes: LoginCodes,
    extensions: Extensions,
) -> OrgFounded:
    """The org made, allowed what its gateway's policy says, its admin active, their first key."""
    # Counted before the org exists: the orgs this person already had here (extensions/points.py).
    already = len(await members.orgs_of(pending.email))
    org = await orgs.create(pending.slug, pending.name or pending.slug)
    if org is None:
        raise SlugTaken(SLUG_TAKEN.format(slug=pending.slug))
    # What this org may do is whoever charges for it's to say, through the point a package plugged
    # into (extensions/points.py); the runtime's own answer is no limit, and no limit is no row.
    allowed = extensions.admitted(org, pending.email, world, already)
    if allowed != Quotas():
        await orgs.set_quotas(org.id, allowed)
    # The org is new, so nobody holds the email yet: the invitation is minted and spent in one
    # breath, the very path a person invited later walks, and the member ends `active`.
    invited = await members.invite(org.id, pending.email, pending.person, "admin", ())
    if invited is None:
        raise RuntimeError(f"{pending.email} already accepted into the org it is founding")
    # A person who already has a password on this box is seated at once and keeps it: one person,
    # one password (auth/members.py). Anybody else spends the invitation with the one they chose.
    admin = (
        invited.member
        if invited.token is None
        else await members.accept(invited.token, pending.hashed)
    )
    if admin is None:
        raise RuntimeError(f"the invitation just made for {pending.email} seated nobody")
    # The admin's own key, which opens production too: an admin always does (0039).
    issued = await mint_person_key(keys, admin, device or pending.device or SIGNED_UP, world)
    return OrgFounded(org=org, admin=admin, issued=issued, code=codes.mint(issued.record))
