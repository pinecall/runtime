"""Sign-up: the code mailed, the org founded on the code, the code sent again."""

import time
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAvailable,
    NotFound,
    NotSignedIn,
)
from pinecall.domain.names import AN_ADDRESS, parse_slug
from pinecall.gateway._deps import GatewayDep, bearer_of, check_knock, client_of
from pinecall.tenancy import letters, orgs, people, signin
from pinecall.tenancy.signin import Refusal, Signup
from pinecall.wire.rest.accounts import (
    CodeMailedResponse,
    EmptyResponse,
    KeyIssuedResponse,
    MemberRow,
    OrgMadeResponse,
    ResendCodeRequest,
    SignupRequest,
    VerifySignupRequest,
)

router = APIRouter()


NOT_OPEN = (
    "sign-ups are not open on this box: its operator sets PINECALL_SIGNUP, or makes the org and "
    "invites you"
)


NO_MAIL = (
    "this box cannot mail a code: its operator sets PINECALL_SMTP_URL and PINECALL_MAIL_FROM, or "
    "makes the org and invites you"
)


TOO_MANY = "too many sign-ups from here: try again in a minute"


NOT_THE_SHIELD = "the sign-up doors take the key of the page in front of them"


NOT_AN_ADDRESS = "an email has one @ and a domain, not {email!r}"


NO_NAME = "a person has a name: it is what a seat says when they sit"


# An address with no sign-up waiting reads as a wrong code, so nobody learns who signed up.
REFUSED: dict[Refusal, str] = {
    "wrong": "that code is not valid",
    "expired": "that code has expired: ask for a new one",
    "burned": "too many tries: ask for a new code",
}


# Where the page in front of the sign-up says the person is. Believed only with its key: the
# header is anybody's to write.
CLIENT_HEADER = "x-pinecall-client"


# The doors are shut unless the operator opens them; behind a shield, they take its key alone.
def signup_client(request: Request, gateway: GatewayDep) -> str:
    """The address a sign-up is counted by; refused where sign-ups are shut or shielded."""
    settings = gateway.connections.settings
    if not settings.signup:
        raise NotFound(NOT_OPEN)
    if settings.signup_key is None:
        return client_of(request)
    data = bearer_of(request.headers) or ""
    if not compare_digest(data, settings.signup_key):
        raise NotSignedIn(NOT_THE_SHIELD)
    return request.headers.get(CLIENT_HEADER, "").strip() or client_of(request)


SignupClient = Annotated[str, Depends(signup_client)]


# No org exists until the code comes back: an address nobody proves never takes a slug. Every
# address is answered alike, so the door says nobody whether one has an account: a person with a
# password signs up with it, and otherwise their mailbox, not the page, is told why no code came.
@router.post("/v1/signup", status_code=202)
async def sign_up(
    body: SignupRequest, client: SignupClient, gateway: GatewayDep
) -> CodeMailedResponse:
    """Keep the sign-up and mail its six digits."""
    connections = gateway.connections
    pool = connections.pool
    if await gateway.outbox.mailbox_for(None) is None:
        raise NotAvailable(NO_MAIL)
    await check_knock(gateway, f"{client} signup", TOO_MANY)
    email = body.email.strip().lower()
    slug = parse_slug(body.org)
    if not AN_ADDRESS.match(email):
        raise DeclarationRefused(NOT_AN_ADDRESS.format(email=body.email))
    if not body.person.strip():
        raise DeclarationRefused(NO_NAME)
    hashed = await people.hash_password(body.password, connections.settings.min_password)
    brand = await letters.brand_of(pool)
    # Somebody who has a password signs up with it, or anybody could mint keys in their name; a
    # sign-up for an invited address would choose that person's password for them.
    known = await people.password_of(pool, email)
    notice = None
    if known is not None and not await people.matches(body.password, known):
        notice = letters.account_kept_letter(email, brand)
    elif known is None and await people.orgs_of(pool, email):
        notice = letters.invitation_waiting_letter(email, brand)
    if notice is not None:
        await gateway.outbox.post(None, notice)
        return CodeMailedResponse(email=email, code_expires_at=time.time() + signin.CODE_TTL_S)
    if await orgs.find(pool, slug) is not None:
        raise Conflict(orgs.SLUG_TAKEN.format(slug=slug))
    signup = Signup(email, slug, body.person, hashed, name=body.name, device=body.device)
    code, expires_at = await gateway.signins.signups.begin(signup)
    letter = letters.signup_code_letter(email, code, body.person, brand)
    await gateway.outbox.post(None, letter)
    return CodeMailedResponse(email=email, code_expires_at=expires_at)


@router.post("/v1/signup/verify", status_code=201)
async def verify_signup(
    body: VerifySignupRequest, client: SignupClient, gateway: GatewayDep
) -> OrgMadeResponse:
    """The mailed code back: the org made, its admin seated, their first key and a login code."""
    await check_knock(gateway, f"{client} signup/verify", TOO_MANY)
    taken = await gateway.signins.signups.verify(body.email.strip().lower(), body.code.strip())
    if isinstance(taken, str):
        raise DeclarationRefused(REFUSED[taken])
    founded = await signin.found(gateway.connections.pool, taken, gateway.signins.codes)
    issued = KeyIssuedResponse.of(founded.signed_in.key, founded.signed_in.secret)
    return OrgMadeResponse(
        **issued.model_dump(),
        slug=founded.org.slug,
        member=MemberRow.of(founded.admin),
        code=founded.code,
        code_expires_at=founded.code_expires_at,
    )


# The same answer for any address, so nobody learns whose sign-up is waiting.
@router.post("/v1/signup/resend", status_code=202)
async def resend_code(
    body: ResendCodeRequest, client: SignupClient, gateway: GatewayDep
) -> EmptyResponse:
    """A new code for a sign-up still waiting; the first one no longer works."""
    await check_knock(gateway, f"{client} signup/resend", TOO_MANY)
    renewed = await gateway.signins.signups.renewed(body.email.strip().lower())
    if renewed is None:
        return EmptyResponse()
    signup, code = renewed
    brand = await letters.brand_of(gateway.connections.pool)
    await gateway.outbox.post(
        None, letters.signup_code_letter(signup.email, code, signup.person, brand)
    )
    return EmptyResponse()
