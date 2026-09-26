"""A person into an org and out of it: the invitation and its letter, and the removal for good."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pinecall.accounts.refusals import NO_SUCH_MEMBER, AccountRefused, NoSuchMember
from pinecall.auth.keys import Keys, revoked_every_key_of
from pinecall.auth.members import Members
from pinecall.mail import Letter, Outbox, card_link, invitation_letter
from pinecall.types import Member, Org, Role

# The email already belongs to somebody who accepted: they log in, and nobody re-invites them.
ALREADY_A_MEMBER = "{email} is already a member of this org: they log in"

# Removing is for good, so the org is never left with nobody who can run it: the last ACTIVE
# admin stays until another one exists. An invited admin does not count: an org whose only admin
# has not chosen a password is an org nobody can sign in to.
THE_LAST_ADMIN = (
    "{email} is the last active admin of this org: make somebody else an admin first, "
    "or the org is left with nobody who can run it"
)


class AlreadyAMember(AccountRefused):
    """The address belongs to a member of this org who has accepted: they sign in instead."""


class LastAdmin(AccountRefused):
    """The member is the org's last active admin, and would leave it with nobody to run it."""


@dataclass(frozen=True)
class Invitee:
    """Who is invited, what they will be allowed, on which agents — empty is every agent."""

    email: str
    name: str
    role: Role
    agents: Sequence[str] = field(default_factory=tuple[str, ...])
    # Whether they may act in production. An admin does whatever this says.
    production: bool = False


@dataclass(frozen=True)
class LinkMade:
    """A member and the one-use link minted for them: the token when it is handed over, when it
    dies, and whether a letter carrying it was posted."""

    member: Member
    token: str | None
    expires_at: str | None
    mailed: bool


async def invite_member(
    members: Members,
    org: Org,
    invitee: Invitee,
    inviter: str,
    base: str,
    outbox: Outbox,
    *,
    handed: bool = True,
    vouched: bool = True,
    seats: int | None = None,
) -> LinkMade:
    """The row, the token the once, and whether a letter carrying it was posted; refused for an
    email that already accepted here. `seats` is what the org may hold, judged by the write.

    A person who already exists on this box — an email with a password in another org, and
    proved to be theirs — is seated active at once and the answer carries no token: they sign
    in with the password they have, and the console's org switch lists the new org beside the
    others. There is nothing for a letter to carry, so nothing is posted and `mailed` is false.

    `handed` false keeps the token out of the answer too: the letter carries it, and only the
    letter (`elsewhere_too`). `vouched` says whether accepting it proves the address (0048).
    The box's own door hands it over always, and vouches: the operator knows who they seat.
    """
    invited = await members.invite(
        org.id,
        invitee.email,
        invitee.name,
        invitee.role,
        invitee.agents,
        production=invitee.production,
        vouched=vouched,
        seats=seats,
    )
    if invited is None:
        raise AlreadyAMember(ALREADY_A_MEMBER.format(email=invitee.email))
    letter = (
        None
        if invited.token is None
        else invitation_letter(
            invited.member.email,
            org.name,
            inviter,
            card_link(base, invited.token),
            invited.expires_at,
        )
    )
    return LinkMade(
        member=invited.member,
        token=invited.token if handed else None,
        expires_at=invited.expires_at,
        mailed=await post_letter(outbox, org.id, letter),
    )


# For good, where `disabled` is for now: the keys stop first, so there is no moment at which the
# row is gone and a key of theirs still opens a door; then the row goes, its open links with it
# (0014's CASCADE), and the seat is free because a seat is a count of rows. What the log wrote
# about them stays readable — it names the id as text, and the id now names nobody.
async def remove_member(members: Members, keys: Keys, org: str, id: str) -> None:
    """Every key of theirs revoked, then the row and its links gone; refused for a stranger and
    for the last active admin. The org's door and the operator's twin both end here."""
    found = await members.find(org, id)
    if found is None:
        raise NoSuchMember(NO_SUCH_MEMBER.format(id=id))
    if _is_an_active_admin(found) and not any(
        _is_an_active_admin(other) and other.id != id for other in await members.listed(org)
    ):
        raise LastAdmin(THE_LAST_ADMIN.format(email=found.email))
    await revoked_every_key_of(keys, org, id)
    if not await members.remove(org, id):
        raise NoSuchMember(NO_SUCH_MEMBER.format(id=id))


# `mailed` says a letter was HANDED OVER to a mail server, never that it arrived: the send runs
# after the door has answered (mail/outbox.py), because a door blocked on somebody else's relay
# is a door. False is the honest answer for a box and an org that both send no mail at all, and
# it is what every answer carried before this existed.
async def post_letter(outbox: Outbox, org: str, letter: Letter | None) -> bool:
    """Whether there was a letter to post and somebody to post it through."""
    return False if letter is None else await outbox.post(org, letter)


def _is_an_active_admin(member: Member) -> bool:
    """Whether this person can run the org today: an admin who has chosen a password."""
    return member.role == "admin" and member.status == "active"
