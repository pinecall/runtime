"""Production says who a person is: the doors that are its alone, and a sandbox asking it."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from pinecall.api._deps import SettingsDep
from pinecall.api.sso import the_http
from pinecall.auth.identity import Identity, NotRedeemed
from pinecall.auth.keys import Issued, Keys, revoked_every_key_of
from pinecall.auth.members import Members
from pinecall.auth.persons import a_persons_key
from pinecall.orgs.table import Orgs
from pinecall.types import PRODUCTION, SANDBOX

# A sandbox instance keeps no password and makes no person: whoever signs in there signed in at
# production and carried a one-use code across. So the doors a person is made or proved at are
# production's, and on a sandbox they are not there at all — 404, with where they are.
SIGN_IN_THERE = "this is the sandbox, and people sign in at {identity}: it keeps no password here"


# One dependency, so a door says it is production's by what it declares and never by an `if`
# somebody could leave out of the next one. A plain function too, for the one door that is
# production's for one of its two bodies (POST /v1/login: a password, not a code).
def at_production(settings: SettingsDep) -> None:
    """Nothing at production; 404 naming where people sign in, anywhere else."""
    if settings.world != PRODUCTION:
        raise HTTPException(404, SIGN_IN_THERE.format(identity=settings.identity_url))


AtProduction = Depends(at_production)


# ── a sandbox asking production who a person is ─────────────────────────────────

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


# Production, over the process's one httpx client, on a sandbox; None on production itself, whose
# own codes are the only ones there are — and which is not handed the client it would never use.
def the_identity(connection: HTTPConnection, settings: SettingsDep) -> Identity | None:
    """Who a sandbox asks who a person is, or None where this instance is the one asked."""
    if settings.world == PRODUCTION or settings.identity_url is None:
        return None
    return Identity(the_http(connection), settings.identity_url)


IdentityDep = Annotated["Identity | None", Depends(the_identity)]


# The second half of a sign-in that began at production: the code was minted there, so it is spent
# there, and what comes back is mirrored here — the org, then the member, both by production's ids
# — before a key of this sandbox's own is minted for them. A stale row of the address (a person
# production removed and invited again) loses its keys first, as a removal does. A member
# production disabled is mirrored disabled and loses every key here at once, not in a day.
async def a_mirrored_key(
    code: str, label: str, identity: Identity, orgs: Orgs, members: Members, keys: Keys
) -> Issued:
    """A sandbox key for the person production says the code names; the refusal otherwise."""
    try:
        org, member = await identity.redeem(code)
    except NotRedeemed as refused:
        raise HTTPException(refused.status, str(refused)) from refused
    if await orgs.mirrored(org) is None:
        raise HTTPException(409, SLUG_HELD_HERE.format(slug=org.slug))
    stale = await members.by_email(org.id, member.email)
    if stale is not None and stale.member.id != member.id:
        await revoked_every_key_of(keys, org.id, stale.member.id)
    seated = await members.mirrored(member)
    if seated is None:
        raise HTTPException(409, ANOTHER_ORGS.format(email=member.email))
    if seated.status != "active":
        await revoked_every_key_of(keys, org.id, seated.id)
        raise HTTPException(403, NOT_ACTIVE.format(email=member.email, slug=org.slug))
    return await a_persons_key(keys, seated, label, SANDBOX)
