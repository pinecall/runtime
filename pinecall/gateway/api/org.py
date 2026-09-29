"""The org's own: judging, identity provider, mailbox, policy, erasures, reads and export."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from pinecall.domain.errors import Conflict, DeclarationRefused, NotFound, UpstreamFailed
from pinecall.domain.person import parse_role
from pinecall.gateway._deps import (
    CallsKey,
    GatewayDep,
    TeamKey,
    UsageKey,
    asked_by,
    public_url,
)
from pinecall.providers import catalog
from pinecall.providers.catalog import judge_ceiling
from pinecall.tenancy import erasure, export, keys, letters, mail, orgs, policy, reads, sso
from pinecall.tenancy.mail import Mailbox, MailboxStatus
from pinecall.tenancy.sso import Client, OrgSso
from pinecall.wire.rest.accounts import (
    OrgMailRequest,
    OrgMailResponse,
    OrgPolicy,
    OrgPolicyRow,
    OrgSsoRequest,
    OrgSsoResponse,
    SendTestLetterRequest,
    SendTestLetterResponse,
)
from pinecall.wire.rest.agents import (
    JudgingRequest,
    JudgingSettings,
)
from pinecall.wire.rest.calls import ErasureTrail, ReadsResponse

router = APIRouter()


# A 404, not a 204: taking away what is not there must not look like it worked.
NO_SSO = "this org signs in with no identity provider"


NO_MAIL = "this org sends its letters through the box's own mail"


# The issuer is asked before it is kept, so a typo shows here and not at the first sign-in.
NOT_KEPT = "{said}: nothing was kept"


# A 409, not a 503: the request is fine, and there is nothing to test.
NOTHING_TO_TEST = (
    "neither this org nor this box has a mail server: set one at PUT /v1/org/mail, or set "
    "PINECALL_SMTP_URL on the box"
)


# Per org, not per world: judging is billed to the org across both.
@router.get("/v1/org/judging")
async def get_judging(key: CallsKey, gateway: GatewayDep) -> JudgingSettings:
    """Whether hang-up judging is on, and its ceiling per call."""
    pool = gateway.connections.pool
    on = await orgs.judged(pool, key.org)
    return JudgingSettings(on=on, ceiling_usd=judge_ceiling(await catalog.providers(pool)))


@router.put("/v1/org/judging")
async def put_judging(body: JudgingRequest, key: UsageKey, gateway: GatewayDep) -> JudgingSettings:
    """Hang-up judging on or off, from the next call."""
    pool = gateway.connections.pool
    await orgs.set_judging(pool, key.org, on=body.on)
    return JudgingSettings(on=body.on, ceiling_usd=judge_ceiling(await catalog.providers(pool)))


# `team`: the provider decides who the org's people are.
@router.get("/v1/org/sso")
async def get_sso(key: TeamKey, request: Request, gateway: GatewayDep) -> OrgSsoResponse:
    """The org's provider, never its secret, and the redirect URI to register there."""
    connections = gateway.connections
    wired = await sso.sso_of(connections.pool, connections.vault, key.org)
    return sso_row(wired, f"{public_url(request, gateway)}{sso.CALLBACK}")


# The role the provider seats people with counts as granted by this key: a manager makes no admin.
@router.put("/v1/org/sso")
async def put_sso(
    body: OrgSsoRequest, key: TeamKey, request: Request, gateway: GatewayDep
) -> OrgSsoResponse:
    """Replace the org's provider whole, once its issuer answered as one."""
    connections = gateway.connections
    role = None if body.role is None else parse_role(body.role)
    wanted = OrgSso(
        org=key.org,
        client=Client(body.issuer.strip(), body.client_id.strip(), body.client_secret),
        domains=tuple(domain.strip().lower().removeprefix("@") for domain in body.domains),
        role=role,
        required=body.required,
    )
    keys.check_may_grant(key.bearer, role, production=False)
    try:
        await sso.discovered(connections.http, wanted.client.issuer)
    except UpstreamFailed as unanswered:
        raise DeclarationRefused(NOT_KEPT.format(said=unanswered)) from unanswered
    await sso.put_sso(connections.pool, connections.vault, wanted)
    return sso_row(wanted, f"{public_url(request, gateway)}{sso.CALLBACK}")


@router.delete("/v1/org/sso", status_code=204)
async def drop_sso(key: TeamKey, gateway: GatewayDep) -> None:
    """Forget the org's provider: passwords open it again from the next attempt."""
    if not await sso.drop_sso(gateway.connections.pool, key.org):
        raise NotFound(NO_SSO)


# `team`: these letters carry invitations and password links.
@router.get("/v1/org/mail")
async def get_mail(key: TeamKey, gateway: GatewayDep) -> OrgMailResponse:
    """The org's own mailbox, never its password, and how its last letter went."""
    connections = gateway.connections
    return _mail_row(await mail.mail_of(connections.pool, connections.vault, key.org))


# Sends nothing: the test door does, and a person watches it.
@router.put("/v1/org/mail")
async def put_mail(body: OrgMailRequest, key: TeamKey, gateway: GatewayDep) -> OrgMailResponse:
    """Replace the org's own mailbox; its letters go through it from the next one."""
    connections = gateway.connections
    await mail.put_mail(connections.pool, connections.vault, key.org, mailbox_of(body))
    return _mail_row(await mail.mail_of(connections.pool, connections.vault, key.org))


@router.delete("/v1/org/mail", status_code=204)
async def drop_mail(key: TeamKey, gateway: GatewayDep) -> None:
    """Forget the org's own mailbox: its letters go through the box's again."""
    if not await mail.drop_mail(gateway.connections.pool, key.org):
        raise NotFound(NO_MAIL)


# The one mail door that waits on SMTP; how it went is kept as a real letter's is.
@router.post("/v1/org/mail/test")
async def send_test_letter(
    body: SendTestLetterRequest, key: TeamKey, gateway: GatewayDep
) -> SendTestLetterResponse:
    """One test letter, waited for: whether it went, and what the server said when not."""
    to = mail.address_of(body.to)
    if await gateway.outbox.mailbox_for(key.org) is None:
        raise Conflict(NOTHING_TO_TEST)
    letter = letters.probe_letter(to, await letters.brand_of(gateway.connections.pool))
    error = await gateway.outbox.sent(key.org, letter)
    return SendTestLetterResponse(sent=error is None, error=error)


def mailbox_of(body: OrgMailRequest) -> Mailbox:
    """The mailbox a body describes, its words trimmed; refused in the mailbox's own terms."""
    return Mailbox(
        host=body.host.strip(),
        port=body.port,
        security=mail.parse_security(body.security),
        username=body.username.strip(),
        password=body.password,
        sender=body.sender.strip(),
    )


def sso_row(wired: OrgSso | None, redirect_uri: str) -> OrgSsoResponse:
    """An org's provider as the doors send it: wired or not, never the secret."""
    return OrgSsoResponse(
        configured=wired is not None,
        issuer=None if wired is None else wired.client.issuer,
        client_id=None if wired is None else wired.client.client_id,
        domains=[] if wired is None else list(wired.domains),
        role=None if wired is None else wired.role,
        required=wired is not None and wired.required,
        redirect_uri=redirect_uri,
    )


@router.get("/v1/org/erasures")
async def erasures(key: TeamKey, gateway: GatewayDep) -> ErasureTrail:
    """The org's erasures, newest first: what went, when, and who asked."""
    rows = await erasure.trail(gateway.connections.pool, key.org)
    return ErasureTrail(erasures=rows)


@router.get("/v1/org/reads")
async def who_read(
    key: TeamKey, gateway: GatewayDep, subject: Annotated[str | None, Query()] = None
) -> ReadsResponse:
    """Who read the org's calls and recordings, newest first; of one call or number when named."""
    rows = await reads.of_org(gateway.connections.pool, key.org, subject=subject)
    return ReadsResponse(reads=rows)


@router.get("/v1/org/policy")
async def get_policy(key: TeamKey, gateway: GatewayDep) -> OrgPolicyRow:
    """The org's compliance settings: how many days a sealed call is kept, and who set it."""
    return await policy.policy_of(gateway.connections.pool, key.org)


@router.put("/v1/org/policy")
async def put_policy(body: OrgPolicy, key: TeamKey, gateway: GatewayDep) -> OrgPolicyRow:
    """Replace the org's compliance settings whole, from the next nightly run."""
    pool = gateway.connections.pool
    await policy.put_policy(pool, key.org, body, by=asked_by(key))
    return await policy.policy_of(pool, key.org)


@router.get("/v1/org/export", response_model=None)
async def export_org(key: TeamKey, gateway: GatewayDep) -> StreamingResponse:
    """The org's data in the key's world as JSON Lines: calls, memories, settings, documents."""
    filename = f"pinecall-{key.org}-{key.env}.jsonl"
    return StreamingResponse(
        _one_per_line(export.lines(gateway.connections.pool, key.org, key.env)),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _mail_row(kept: MailboxStatus | None) -> OrgMailResponse:
    # By its wire name: `from` is a keyword.
    return OrgMailResponse.model_validate(
        {
            "configured": kept is not None,
            "host": None if kept is None else kept.mailbox.host,
            "port": None if kept is None else kept.mailbox.port,
            "security": None if kept is None else kept.mailbox.security,
            "username": None if kept is None else kept.mailbox.username,
            "from": None if kept is None else kept.mailbox.sender,
            "verified_at": None if kept is None else kept.verified_at,
            "last_error": None if kept is None else kept.last_error,
        }
    )


async def _one_per_line(lines: AsyncIterator[str]) -> AsyncIterator[bytes]:
    async for line in lines:
        yield f"{line}\n".encode()
