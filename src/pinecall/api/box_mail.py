"""The box's own mail, from the operator's doors: what is stored wins over the environment's."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from pinecall.api._operator import an_operator
from pinecall.api.org_mail import OutboxDep, TestTo, WantedMail, a_mailbox
from pinecall.api.orgs import NO_BODY
from pinecall.mail import BoxMail, a_test_message
from pinecall.orgs.vault import NO_VAULT_KEY, NoVaultKey
from pinecall.types import DeclarationRefused, an_address

# The same gate every /v1/ops door takes. The mail server the box posts through is the box's
# credential and nobody's tenant's: until here it was a line of the environment, changed by
# whoever can ssh in and restart the gateway, and the person who runs a box from the console is not
# always that person.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

# Nothing is stored. 404 and not an empty 204: dropping what was never set must never read as
# done — and the environment's mailbox is not dropped here, because it is not kept here.
NOTHING_STORED = (
    "this box has no stored mail server: what it posts through, if anything, is PINECALL_SMTP_URL"
)

# The test send, when the box can send through nothing at all. 409 and not 503: the request was
# right and there is nothing to test, which is a thing the operator fixes at PUT /v1/ops/mail.
NOTHING_TO_TEST = (
    "this box has no mail server: wire one at PUT /v1/ops/mail, or set PINECALL_SMTP_URL"
)


@operator.get("/mail")
async def wired(outbox: OutboxDep) -> Any:
    """What this box posts letters through, where that came from, and how the last one went."""
    return _standing(await outbox.the_boxs.of())


@operator.put("/mail")
async def wire(said: WantedMail, outbox: OutboxDep) -> Any:
    """Store the box's mail, replacing what was stored; the environment's is left as it is and
    no longer used. Nothing is sent to find out it works: that is `POST /v1/ops/mail/test`."""
    mailbox = a_mailbox(said)
    try:
        await outbox.the_boxs.put(mailbox)
    except NoVaultKey as unsealed:
        raise HTTPException(503, NO_VAULT_KEY) from unsealed
    return _standing(await outbox.the_boxs.of())


@operator.delete("/mail", status_code=NO_BODY)
async def unwire(outbox: OutboxDep) -> None:
    """Forget the stored one; the box posts through its environment's again, or through nothing."""
    if not await outbox.the_boxs.drop():
        raise HTTPException(404, NOTHING_STORED)


# The org's twin waits for a mail server for the same reason (api/org_mail.py): a person is
# watching. This one tests the BOX's mailbox, whatever any org wired for itself.
@operator.post("/mail/test")
async def test(said: TestTo, outbox: OutboxDep) -> dict[str, Any]:
    """One letter through the box's own mail, waited for: `{sent, error}`; 409 with none."""
    try:
        to = an_address(said.to)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if await outbox.the_boxs.of() is None:
        raise HTTPException(409, NOTHING_TO_TEST)
    said_back = await outbox.sent(None, a_test_message(to, await outbox.brand()))
    return {"sent": said_back is None, "error": said_back}


# The org's envelope plus `source` (protocol/schema/rest.json, BoxMail): a page that draws the
# one draws the other, and `source` is the one thing the box's has to say that an org's does
# not — whether what it reads came from the console or from a file on the machine.
def _standing(boxs: BoxMail | None) -> dict[str, Any]:
    """One mailbox as the operator sees it: what, from where, how it went — never the password."""
    kept = None if boxs is None else boxs.kept
    return {
        "configured": boxs is not None,
        "source": None if boxs is None else boxs.source,
        "host": None if kept is None else kept.mailbox.host,
        "port": None if kept is None else kept.mailbox.port,
        "security": None if kept is None else kept.mailbox.security,
        "username": None if kept is None else kept.mailbox.username,
        "from": None if kept is None else kept.mailbox.sender,
        "verified_at": None if kept is None else kept.verified_at,
        "last_error": None if kept is None else kept.last_error,
    }
