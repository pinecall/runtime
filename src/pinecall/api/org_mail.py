"""The org's own mail server: what an admin wires, what a reader may see, and one test letter."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from starlette.requests import HTTPConnection
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api._deps import TeamKeyDep, held
from pinecall.mail import Outbox, a_test_message
from pinecall.orgs.mail import KeptMail, Mail
from pinecall.orgs.vault import NO_VAULT_KEY
from pinecall.types import Mailbox, a_security, an_address
from pinecall_protocol import WireModel

# The org's own three doors and its test send, on a key with `team` — the same scope that invites
# a person and wires the identity provider, because the letters this posts are the ones that
# invite somebody and hand a password back.
router = APIRouter()

# Nothing is wired. 404 and not an empty 204: dropping mail an org never had must never read as
# done, the same rule every other drop of this runtime follows.
NO_MAIL = "this org sends its letters through the box's own mail"

# The test send, when neither the org nor the box can send at all. It is a 409 and not a 503: the
# request was right and there is nothing to test, which is a thing an admin fixes here.
NOTHING_TO_TEST = (
    "neither this org nor this box has a mail server: wire one at PUT /v1/org/mail, or set "
    "PINECALL_SMTP_URL on the box"
)


# Off the outbox and not off a second thing on app.state: the outbox is where an org's own mail
# is known — it is the object that chooses between that and the box's — so one process holds one.
def the_mail(connection: HTTPConnection) -> Mail | None:
    """The org_mail table, or None when this runtime was given no vault key to seal a password."""
    return the_outbox(connection).table


def the_outbox(connection: HTTPConnection) -> Outbox:
    """Where every letter of this process leaves by: the org's own mail, or the box's, or none."""
    return held(connection, "outbox", Outbox)


MailDep = Annotated["Mail | None", Depends(the_mail)]
OutboxDep = Annotated[Outbox, Depends(the_outbox)]


# An org's SMTP password is a secret exactly as a provider key is, sealed under the same vault
# key; a runtime with none cannot keep one, and says so in the vault's own sentence.
async def a_kept_mail(mail: MailDep) -> Mail:
    """The org_mail table, or 503: this box has no vault key."""
    if mail is None:
        raise HTTPException(503, NO_VAULT_KEY)
    return mail


KeptMailDep = Annotated[Mail, Depends(a_kept_mail)]


class WantedMail(WireModel):
    """What an admin wires: where the letters go out, who signs in there, and who they are from."""

    host: str
    port: int
    # starttls · tls · none. The ordinary one is starttls on 587; tls is implicit TLS on 465.
    security: str = "starttls"
    # Empty for a relay that asks for no credentials, which is a mail server on the same machine.
    username: str = ""
    # Write-only, always: no door of this runtime reads one back, so a change of anything else
    # carries it again. The same shape the identity provider's client secret already has.
    password: str = ""
    # `from` on the wire, because that is the header it becomes — and a field of that name is a
    # keyword here. The same word comes back out of the GET, so a page writes one name.
    sender: str = Field(alias="from")


class TestTo(WireModel):
    """Where the one test letter goes. An admin's own address, usually."""

    to: str


@router.get("/v1/org/mail")
async def wired(key: TeamKeyDep, mail: KeptMailDep) -> Any:
    """What this org's letters go out through, and how the last one went. Never the password."""
    return _standing(await mail.of(key.org))


@router.put("/v1/org/mail")
async def wire(said: WantedMail, key: TeamKeyDep, mail: KeptMailDep) -> Any:
    """Wire this org's own mail, replacing what it had. Nothing is sent to find out it works:
    the send is `POST /v1/org/mail/test`, so a door is never blocked on somebody's relay."""
    await mail.put(key.org, a_mailbox(said))
    return _standing(await mail.of(key.org))


@router.delete("/v1/org/mail", status_code=HTTP_204_NO_CONTENT)
async def unwire(key: TeamKeyDep, mail: KeptMailDep) -> None:
    """Forget it; this org's letters go through the box's own mail again, or through nothing."""
    if not await mail.drop(key.org):
        raise HTTPException(404, NO_MAIL)


# The one door of this runtime that WAITS for a mail server, because it is the one where a person
# is watching: a letter that is refused is what they came here to find out. It records its outcome
# exactly as a real letter does, so the GET above says the same thing a moment later.
@router.post("/v1/org/mail/test")
async def test(said: TestTo, key: TeamKeyDep, outbox: OutboxDep) -> dict[str, Any]:
    """One letter, waited for: `{sent, error}` — and 409 when there is no mail server at all."""
    to = an_address(said.to)
    if await outbox.mailbox_for(key.org) is None:
        raise HTTPException(409, NOTHING_TO_TEST)
    said_back = await outbox.sent(key.org, a_test_message(to, await outbox.brand()))
    return {"sent": said_back is None, "error": said_back}


# Shared with the box's own doors (api/box_mail.py): one body, one refusal, for either mailbox.
def a_mailbox(said: WantedMail) -> Mailbox:
    """The body as the domain's own shape, or 400 in the refusal's own words."""
    return Mailbox(
        host=said.host.strip(),
        port=said.port,
        security=a_security(said.security),
        username=said.username.strip(),
        password=said.password,
        sender=said.sender.strip(),
    )


# The password is not in here and there is no door that answers with one. What is worth knowing
# about a stored password is that it is there, which `configured` says — and, far more usefully,
# whether a letter has ever gone through it: `verified_at` and `last_error`.
#
# One shape either way, every field always there: an org that wired nothing answers the empty
# value of each rather than leaving them out, so a page parses one envelope
# (protocol/schema/rest.json, OrgMail).
def _standing(kept: KeptMail | None) -> dict[str, Any]:
    """One mailbox as every reader sees it: what it is wired to, and never the password."""
    return {
        "configured": kept is not None,
        "host": None if kept is None else kept.mailbox.host,
        "port": None if kept is None else kept.mailbox.port,
        "security": None if kept is None else kept.mailbox.security,
        "username": None if kept is None else kept.mailbox.username,
        "from": None if kept is None else kept.mailbox.sender,
        "verified_at": None if kept is None else kept.verified_at,
        "last_error": None if kept is None else kept.last_error,
    }
