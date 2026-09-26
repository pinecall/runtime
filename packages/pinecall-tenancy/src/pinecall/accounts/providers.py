"""A person an identity provider vouched for: the claims checked, and the row that word seats."""

from __future__ import annotations

import httpx

from pinecall.accounts.refusals import DISABLED, AccountRefused, NotActive
from pinecall.accounts.signing_in import is_sso_only
from pinecall.auth.members import Members, NoSeatLeft, normalize_email
from pinecall.auth.openid import Claims, OpenIdRefused, Provider, claims, configuration, exchange
from pinecall.auth.sso_state import Handshake
from pinecall.orgs.admission import Admission
from pinecall.orgs.org_sso import Sso
from pinecall.types import Member, Org, OrgSso

IDP_REFUSED = "{issuer} did not sign this person in: {said}"
NO_EMAIL = "{issuer} vouched for somebody it gave no email address for"
NOT_VERIFIED = "{issuer} has not verified {email}: nobody is seated on an unverified address"
NOBODY_HERE = "nobody in {org} answers to {email}, and it seats nobody it was not told to"

# What a box-wide provider (Google) is refused with, where it names no org: the address is nobody
# here, is disabled everywhere, or belongs to an org that signs in with a provider of its own.
NOT_ON_THIS_BOX = (
    "{email} is not a member of any org on this box: an admin of your org has to invite that "
    "address before Google can sign it in"
)
DISABLED_EVERYWHERE = "{email} is disabled in every org of theirs here"
THEIR_OWN_PROVIDER = (
    "{email} belongs to an org that signs in with its own identity provider: open "
    "/v1/login/sso?org={org} instead"
)


class ProviderUnreachable(AccountRefused):
    """The issuer's configuration could not be read: the request was right, the provider is down."""


class ProviderRefused(AccountRefused):
    """The provider did not vouch for a verified address: the code, the token or the claims."""


class NobodyToSeat(AccountRefused):
    """The provider vouched for an address no org here seats on that word."""


async def provider_config(http: httpx.AsyncClient, issuer: str) -> Provider:
    """The issuer's configuration, or the refusal saying it could not be read."""
    try:
        return await configuration(http, issuer)
    except OpenIdRefused as refused:
        raise ProviderUnreachable(str(refused)) from refused


# The same three steps for an org's own provider and for a box-wide one
# (api/accounts/google_login.py): which client this gateway is at the issuer is all that differs
# between them.
async def claims_from_provider(
    http: httpx.AsyncClient,
    issuer: str,
    client_id: str,
    client_secret: str,
    handshake: Handshake,
    code: str,
) -> Claims:
    """The code spent and the id_token checked — signature, issuer, audience, expiry, nonce."""
    provider = await provider_config(http, issuer)
    try:
        id_token = await exchange(
            http,
            provider,
            client_id,
            client_secret,
            code,
            handshake.redirect_uri,
            handshake.verifier,
        )
        said = await claims(http, provider, id_token, client_id, handshake.nonce)
    except OpenIdRefused as refused:
        raise ProviderRefused(IDP_REFUSED.format(issuer=issuer, said=refused)) from refused
    if not said.email:
        raise ProviderRefused(NO_EMAIL.format(issuer=issuer))
    # An address the provider has not verified is an address somebody typed into a directory, and
    # seating on one is how a stranger becomes a member by claiming a colleague's email.
    if not said.email_verified:
        raise ProviderRefused(NOT_VERIFIED.format(issuer=issuer, email=said.email))
    return said


async def seat_vouched(
    org: Org, wired: OrgSso, said: Claims, members: Members, admission: Admission
) -> Member:
    """The member this address names in this org — invited, seated or made, per the org's rule."""
    email = normalize_email(said.email)
    kept = await members.by_email(org.id, email)
    if kept is not None:
        member = kept.member
        if member.status == "disabled":
            raise NotActive(DISABLED.format(email=member.email, org=org.slug))
        # A person who just proved who they are at their org's OWN provider has accepted their
        # invitation: the link would only buy them a password, and this org signs in without one.
        # They keep no password, so `required` costs them nothing and the row is simply active —
        # and verified, on the provider's word (0048), whatever it was before.
        return await _activated(members, org, member)
    if wired.role is None:
        raise NobodyToSeat(NOBODY_HERE.format(org=org.slug, email=email))
    # A member is a seat whoever it was made by, so the plan is asked here exactly as the invite
    # door asks it — before the row, because a seat is a stock — in the quota's own sentence.
    try:
        await admission.a_seat(org.id, await members.seated(org.id))
        invited = await members.invite(
            org.id,
            email,
            said.name or email.partition("@")[0],
            wired.role,
            (),
            seats=(await admission.quotas_of(org.id)).seats,
        )
    except NoSeatLeft as full:
        # The write judged the seat again, under its lock, and refused: the same sentence.
        await admission.a_seat(full.org, full.seated)
        raise
    if invited is None:
        raise NobodyToSeat(NOBODY_HERE.format(org=org.slug, email=email))
    return await _activated(members, org, invited.member)


async def _activated(members: Members, org: Org, member: Member) -> Member:
    """The row active and verified, with no password on it: the provider is how they sign in."""
    seated = await members.vouched_for(org.id, member.id)
    if seated is None:
        raise NobodyToSeat(NOBODY_HERE.format(org=org.slug, email=member.email))
    return seated


# The same rule the password login follows with no org named: the OLDEST org of theirs that a
# password would open — not disabled, not on its own provider — and the switcher does the rest. A
# row still INVITED is seated: the provider verified the address, which is exactly what the link
# in the invitation would have proved, and it is what an org's own provider does (above). Every
# invited row of theirs is seated, not only the oldest: they proved the address once.
async def home_of(members: Members, sso: Sso | None, email: str) -> Member:
    """Their oldest org a box-wide sign-in may enter, seated where they were only invited."""
    rows = await members.orgs_of(email)
    if not rows:
        raise NobodyToSeat(NOT_ON_THIS_BOX.format(email=email))
    standing = [row for row in rows if row.status != "disabled"]
    if not standing:
        raise NobodyToSeat(DISABLED_EVERYWHERE.format(email=email))
    allowed = [row for row in standing if not await is_sso_only(sso, row.org)]
    if not allowed:
        raise NobodyToSeat(THEIR_OWN_PROVIDER.format(email=email, org=standing[0].org))
    # The provider named the address, which is what proves it (0048): every standing row of
    # theirs is verified, and an invited one seated, on that word.
    seated: list[Member] = []
    for row in allowed:
        member = await members.vouched_for(row.org, row.id)
        seated.append(row if member is None else member)
    return seated[0]
