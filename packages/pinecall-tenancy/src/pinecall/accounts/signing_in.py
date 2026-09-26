"""A person signing in: by email and password, by a code a key minted, or as themselves again."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.accounts.refusals import (
    DISABLED,
    NOBODY,
    NOBODY_ANYWHERE,
    NOT_A_MEMBER,
    NOT_A_PERSONS,
    NOT_YET,
    VISITS_PRODUCTION,
    WITH_THE_PROVIDER,
    NotActive,
    NotAMembersKey,
    SignsInWithProvider,
    WrongCredentials,
)
from pinecall.auth import passwords
from pinecall.auth.env import is_persons_key
from pinecall.auth.keys import Issued, KeyRecord, Keys
from pinecall.auth.members import Kept, Members
from pinecall.auth.person_keys import mint_person_key, until
from pinecall.auth.visitor_keys import visitor_email
from pinecall.orgs.org_sso import Sso
from pinecall.orgs.records import Orgs
from pinecall.types import SANDBOX, Env

# What a key is labelled when the person named no device: the door it came through.
LOGGED_IN = "login"
A_BROWSER = "console"


@dataclass(frozen=True)
class SigningIn:
    """An email and a password, in the org named or, with none named, the oldest of theirs."""

    email: str
    password: str
    org: str | None = None
    # What the key is labelled: this browser, this laptop. Revoked on its own, later.
    device: str | None = None


async def sign_in_with_password(
    signing_in: SigningIn, orgs: Orgs, members: Members, keys: Keys, sso: Sso | None, world: Env
) -> Issued:
    """The member this email and password name, in the org named or in the oldest of theirs, and
    a key minted for them."""
    nobody = NOBODY_ANYWHERE if signing_in.org is None else NOBODY.format(org=signing_in.org)
    # The password is the PERSON's, whichever org it was chosen in: a row of theirs still
    # invited in this org — made before they existed, or before this rule — is seated with it.
    known = await members.a_persons_password(signing_in.email)
    if not await passwords.matches(signing_in.password, known) or known is None:
        raise WrongCredentials(nobody)
    kept = await _the_row_for(signing_in, orgs, members, sso)
    if kept is None:
        raise WrongCredentials(nobody)
    member = kept.member
    # Said after the password matched, and about the org the row is in: a person with two orgs
    # lands in the one a password still opens (below), and only somebody whose every org signs in
    # with a provider is sent to one.
    if await is_sso_only(sso, member.org):
        org = await orgs.find(member.org)
        raise SignsInWithProvider(WITH_THE_PROVIDER.format(org=org.slug if org else member.org))
    if member.status == "disabled":
        raise NotActive(DISABLED.format(email=member.email, org=member.org))
    if member.status == "invited":
        # Seated with the password they have only once the address is PROVED theirs (0048): a
        # password chosen through a link an admin handed over could be anybody's, and an
        # invited row seated on it was the row the wrong person walked into.
        seated = (
            await members.join(member.org, member.id, known)
            if await members.verified(member.email)
            else None
        )
        if seated is None:
            raise NotActive(NOT_YET.format(email=member.email))
        member = seated
    return await mint_person_key(keys, member, signing_in.device or LOGGED_IN, world)


async def sign_in_with_code(
    record: KeyRecord, device: str | None, keys: Keys, world: Env
) -> Issued:
    """A key of the browser's own minted from the record a code of ours stood for. A person's
    carries no world, whatever world the request that minted the code named, and lives as long as
    a person's key does here — never longer than the key that minted the code."""
    person = is_persons_key(record)
    return await keys.issue(
        org=record.org,
        label=device or A_BROWSER,
        env=SANDBOX if person else record.env,
        scopes=record.scopes,
        subject=record.subject,
        name=record.name,
        expires_at=until(world, record) if person else record.expires_at,
    )


# The one place a key is minted FROM another key: the card that signs a terminal in
# (api/accounts/pairing.py). The scopes come off the MEMBER and not off the key that asked — the
# role is the source, and a role changed since the asking key was minted is the role now.
async def mint_key_for_same_person(
    key: KeyRecord, label: str | None, keys: Keys, members: Members, world: Env
) -> Issued:
    """A key for the person this one names, with what their role opens."""
    if key.subject is None:
        raise NotAMembersKey(NOT_A_PERSONS)
    if visitor_email(key.subject) is not None:
        raise NotAMembersKey(VISITS_PRODUCTION)
    member = await members.find(key.org, key.subject)
    if member is None or member.status != "active":
        raise NotActive(NOT_A_MEMBER)
    return await mint_person_key(keys, member, label, world, minted_from=key)


async def _the_row_for(
    signing_in: SigningIn, orgs: Orgs, members: Members, sso: Sso | None
) -> Kept | None:
    """The person's row in the org named; with none named, their oldest row that is not disabled."""
    email = signing_in.email
    if signing_in.org is not None:
        org = await orgs.find(signing_in.org)
        return None if org is None else await members.by_email(org.id, email)
    rows = await members.orgs_of(email)
    # An org that signs in with its provider is passed over here rather than refused: a person of
    # two orgs, one of them on SSO, types no org and lands in the one their password opens. When
    # every org of theirs is on a provider the loop finds none and the fallback below says so.
    for row in rows:
        if row.status != "disabled" and not await is_sso_only(sso, row.org):
            return await members.by_email(row.org, email)
    for row in rows:
        if row.status != "disabled":
            return await members.by_email(row.org, email)
    return None if not rows else await members.by_email(rows[0].org, email)


# None is a box with no vault key: it can read no client secret, so no org signs in with a
# provider there and every one of them is opened by a password. That is also the way back for a
# box whose vault key was lost, and it is deliberate — see orgs/org_sso.py.
async def is_sso_only(sso: Sso | None, org: str) -> bool:
    """Whether this org has said a password opens it no longer."""
    if sso is None:
        return False
    wired = await sso.of(org)
    return wired is not None and wired.required
