"""A forgotten password: the one-use link posted to the person who forgot it, where mail can go."""

from __future__ import annotations

from pinecall.accounts.signing_in import is_sso_only
from pinecall.auth.members import Members
from pinecall.mail import Outbox, card_link, forgotten_password_letter
from pinecall.orgs.org_sso import Sso
from pinecall.orgs.records import Orgs


# One path for every address, and it ends the same way for all of them: nothing is raised, nothing
# is answered differently, and no branch of it costs a password hash — the one thing here that
# would take a measurable moment for a member and none at all for a stranger. The letter itself
# goes in the background (mail/outbox.py), so what an address that HAS a member costs the door
# over one that has not is a row read and a row written.
async def post_reset_link(
    email: str, base: str, orgs: Orgs, members: Members, sso: Sso | None, outbox: Outbox
) -> None:
    """A reset link posted for the oldest org of theirs a password still opens and whose letters
    can carry one; nothing at all for anybody else."""
    for row in await members.orgs_of(email):
        if row.status != "active" or await is_sso_only(sso, row.org):
            continue
        # Asked BEFORE the token is minted, and that order is the point: `members.reset` spends
        # every older link of that member, so minting one nothing will carry would let anybody
        # who knows an address kill the link an admin handed over an hour ago.
        if await outbox.mailbox_for(row.org) is None:
            continue
        issued = await members.reset(row.org, row.id)
        if issued is None or issued.token is None:
            continue
        org = await orgs.find(row.org)
        link = card_link(base, issued.token)
        named = org.name if org else row.org
        letter = forgotten_password_letter(row.email, named, link, issued.expires_at)
        await outbox.post(row.org, letter)
        return
