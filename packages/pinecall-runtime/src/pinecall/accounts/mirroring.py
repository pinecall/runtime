"""A sandbox asking production who a person is, and mirroring the answer before it keys them."""

from __future__ import annotations

from pinecall.accounts.refusals import AccountRefused, NotActive
from pinecall.auth.identity import Identity
from pinecall.auth.keys import Issued, Keys, revoked_every_key_of
from pinecall.auth.members import Members
from pinecall.auth.person_keys import mint_person_key
from pinecall.extensions import Admitting
from pinecall.orgs.records import Orgs
from pinecall.types import SANDBOX, Quotas

# The mirror keeps production's ids, so the words every door uses — the slug `pinecall link` wrote,
# a key's org and subject — mean the same thing on both instances. An org of this sandbox's own
# holding the slug under another id is not written over: two orgs is a real conflict, and an
# operator of the sandbox settles it. An address is different: every member row here is a mirror.
SLUG_HELD_HERE = "{slug} is another org's on this sandbox: an operator here renames or removes it"
# Production answers the member as the row stands, disabled included: the sandbox mirrors that,
# stops every key of theirs here at once, and signs nobody in.
NOT_ACTIVE = "{email} is not an active member of {slug} at production"
# The ids a mirror was refused for: an id production gave one org, held here by another.
ANOTHER_ORGS = "{email}'s id is a member of another org on this sandbox: an operator here looks"


class MirrorRefused(AccountRefused):
    """Production's org or member holds an id or a slug this sandbox gave to another."""


# The second half of a sign-in that began at production: the code was minted there, so it is spent
# there, and what comes back is mirrored here — the org, then the member, both by production's ids
# — before a key of this sandbox's own is minted for them. A stale row of the address (a person
# production removed and invited again) loses its keys first, as a removal does. A member
# production disabled is mirrored disabled and loses every key here at once, not in a day.
#
# An org this sandbox did not have is admitted here, as signup admits one at production: the
# extension says what a new org may do in THIS world, in the same breath it is made. An org the
# sandbox already held — a later sign-in, or one the seed copied — is never admitted again.
async def mint_mirrored_key(
    code: str,
    label: str,
    identity: Identity,
    orgs: Orgs,
    members: Members,
    keys: Keys,
    admitted: Admitting,
) -> Issued:
    """A sandbox key for the person production says the code names; the refusal otherwise."""
    org, member = await identity.redeem(code)
    known = await orgs.find(org.id)
    if await orgs.mirrored(org) is None:
        raise MirrorRefused(SLUG_HELD_HERE.format(slug=org.slug))
    if known is None:
        # The sandbox's own rows of this person, before this org's is mirrored: the orgs they
        # already brought here, which is what a policy giving one trial per person reads.
        already = len(await members.orgs_of(member.email))
        allowed = admitted(org, member.email, SANDBOX, already)
        if allowed != Quotas():
            await orgs.set_quotas(org.id, allowed)
    stale = await members.by_email(org.id, member.email)
    if stale is not None and stale.member.id != member.id:
        await revoked_every_key_of(keys, org.id, stale.member.id)
    seated = await members.mirrored(member)
    if seated is None:
        raise MirrorRefused(ANOTHER_ORGS.format(email=member.email))
    if seated.status != "active":
        await revoked_every_key_of(keys, org.id, seated.id)
        raise NotActive(NOT_ACTIVE.format(email=member.email, slug=org.slug))
    return await mint_person_key(keys, seated, label, SANDBOX)
