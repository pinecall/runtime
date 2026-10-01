"""The box's own doors over its orgs: the box, the orgs, quotas, people, keys, tracebacks."""

from dataclasses import replace
from importlib.metadata import version
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from pinecall.channels import routes
from pinecall.domain.errors import Conflict, NotFound
from pinecall.domain.names import ENVS, Env, parse_slug
from pinecall.domain.org import Org, Quotas
from pinecall.domain.person import KEY_SCOPES, key_scopes, parse_role
from pinecall.gateway._deps import GatewayDep, bearer_of, operator, public_url
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.api.keys import key_row
from pinecall.gateway.api.org import sso_row
from pinecall.gateway.api.providers import credentials_of, installed_vendor
from pinecall.process.recordings import recordings_of
from pinecall.providers.credentials import parse_lending
from pinecall.retrieval import knowledge, memory
from pinecall.tenancy import (
    admission,
    dial_policy,
    erasure,
    keys,
    letters,
    orgs,
    people,
    reads,
    sso,
    traceback,
    vault,
)
from pinecall.tenancy.dial_policy import Guards
from pinecall.tenancy.letters import Link
from pinecall.wire.rest.accounts import (
    BoxIdentityResponse,
    InvitationResponse,
    InviteMemberRequest,
    KeyIssuedResponse,
    KeyRow,
    MemberRow,
    OrgSsoResponse,
    RevokeKeyResponse,
)
from pinecall.wire.rest.numbers import DialGuards
from pinecall.wire.rest.ops import (
    AgentMovedResponse,
    CreateOrgRequest,
    IssueKeyRequest,
    MoveAgentRequest,
    OperatorRequest,
    OrgHolding,
    OrgMembersResponse,
    OrgProfile,
    OrgQuotas,
    OrgRow,
    PutDiallingRequest,
    PutQuotasRequest,
    SsoRequiredRequest,
    Traceback,
)
from pinecall.wire.rest.providers import ProviderKeyRequest, VendorsResponse

router = APIRouter(dependencies=[Depends(operator)])


NO_SUCH_ORG = "no org named {named}: by id or by slug"


# Forgetting an org never cascades over what still points at it.
STILL_IN_USE = "org {slug} still has {what}: revoke its keys and remove its routes first"


NOT_HELD = "agent {slug} is held right now: stop it, move it, and start it again"


NO_SUCH_AGENT = "no agent named {slug} has ever written a log here"


NO_SUCH_KEY = "no live key with fingerprint {fingerprint}"


NO_SSO = "org {slug} signs in with no identity provider"


NO_SUCH_VENDOR_KEY = "org {slug} brought no key for {vendor}"


# Who a letter says invited, when the box's own key did.
THE_OPERATOR = "The operator"


MemberId = Annotated[str, Path(alias="id")]


# The path says {id}, as the console spells it; `id` is a builtin, so the function says member.
A_MEMBER = "/v1/ops/orgs/{named}/members/{id}"


# The console opens its Box screens when this answers 200; anything else hides them.
@router.get("/v1/ops/whoami")
async def box_identity(request: Request, gateway: GatewayDep) -> BoxIdentityResponse:
    """The box this key opens, and the person holding it; nobody for the box's own key."""
    data = bearer_of(request.headers)
    found = None if data is None else await gateway.keys.verify(data)
    person = None if found is None else found.member
    return BoxIdentityResponse(
        operator=True,
        version=version("pinecall"),
        domain=gateway.connections.settings.domain,
        name=None if person is None else person.name,
        org=None if person is None else person.org,
    )


@router.get("/v1/ops/orgs")
async def list_orgs(gateway: GatewayDep) -> list[OrgRow]:
    """Every org, oldest first, the default one first."""
    return [_org_row(org) for org in await orgs.listed(gateway.connections.pool)]


@router.post("/v1/ops/orgs", status_code=201)
async def create_org(body: CreateOrgRequest, gateway: GatewayDep) -> OrgRow:
    """A new org, its id minted here, born with what admission gives one."""
    slug = parse_slug(body.slug.strip())
    return _org_row(await orgs.create(gateway.connections.pool, slug, body.name or slug))


# `holding` is what the org has now, never a fold of the log: usage is /v1/ops/usage.
@router.get("/v1/ops/orgs/{named}")
async def org_standing(named: str, gateway: GatewayDep) -> OrgProfile:
    """One org as it stands: its quotas per world, its dial guards, and what it holds."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    quotas: dict[Env, Quotas] = {env: await admission.quotas_of(pool, org.id, env) for env in ENVS}
    return OrgProfile(
        id=org.id,
        slug=org.slug,
        name=org.name,
        quotas={env: _quotas_row(kept) for env, kept in quotas.items()},
        dialling=_guards_row(await dial_policy.guards_of(pool, org.id)),
        holding=OrgHolding(
            memory_facts=sum([await memory.kept(pool, org.id, env) for env in ENVS]),
            knowledge_chunks=sum([await knowledge.kept(pool, org.id, env) for env in ENVS]),
            numbers=sum([await routes.managed_in(pool, org.id, env) for env in ENVS]),
            seats=await people.seated(pool, org.id),
        ),
    )


@router.delete("/v1/ops/orgs/{named}", status_code=204)
async def remove_org(named: str, gateway: GatewayDep) -> None:
    """Erase the org whole, its calls and recordings too; refused while a key or route names it."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    if any(row.revoked_at is None for row in await keys.listed(pool, org.id)):
        raise Conflict(STILL_IN_USE.format(slug=org.slug, what="live keys"))
    for env in ENVS:
        if await routes.of_org(pool, org.id, env):
            raise Conflict(STILL_IN_USE.format(slug=org.slug, what="routes"))
    recordings = recordings_of(gateway.connections.settings, gateway.connections.http)
    erased = await erasure.org(pool, recordings, org.id, by=reads.OPERATOR)
    for call in erased.calls:
        gateway.logs.forget(call)


# The way back from an agent registered with the wrong org's key: its logs and its numbers move.
# The operator's, since an org able to pull a slug could take another org's agent.
@router.put("/v1/ops/orgs/{named}/agents")
async def move_agent(named: str, body: MoveAgentRequest, gateway: GatewayDep) -> AgentMovedResponse:
    """An agent's logs and numbers moved into this org; refused while somebody holds it."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    if gateway.sockets.declared(body.agent) is not None:
        raise Conflict(NOT_HELD.format(slug=body.agent))
    moved = await gateway.logs.store.moved(body.agent, org.id)
    if moved == 0:
        raise NotFound(NO_SUCH_AGENT.format(slug=body.agent))
    numbers, stayed = await routes.moved_with_agent(pool, body.agent, org.id)
    return AgentMovedResponse(
        agent=body.agent, org=org.slug, logs=moved, numbers=numbers, stayed=stayed
    )


@router.put("/v1/ops/orgs/{named}/quotas")
async def put_quotas(named: str, body: PutQuotasRequest, gateway: GatewayDep) -> OrgQuotas:
    """The org's limits in one world, replaced whole; they bite the next call and register."""
    org = await _org(gateway, named)
    wanted = body.quotas
    quotas = Quotas(
        minutes=wanted.limits.get("minutes"),
        messages=wanted.limits.get("messages"),
        agents=wanted.limits.get("agents"),
        concurrent_calls=wanted.limits.get("concurrent_calls"),
        memory_facts=wanted.limits.get("memory_facts"),
        knowledge_chunks=wanted.limits.get("knowledge_chunks"),
        numbers=wanted.limits.get("numbers"),
        seats=wanted.limits.get("seats"),
        llm_tokens=wanted.limits.get("llm_tokens"),
        hosted_apps=wanted.limits.get("hosted_apps"),
        budget_usd=wanted.budget_usd,
        lends=None if wanted.lends is None else parse_lending(wanted.lends),
    )
    await admission.set_quotas(gateway.connections.pool, org.id, body.env, quotas)
    return _quotas_row(quotas)


# The operator's: an org able to lift its own dial guards would have none.
@router.put("/v1/ops/orgs/{named}/dialling")
async def put_dialling(named: str, body: PutDiallingRequest, gateway: GatewayDep) -> DialGuards:
    """The org's dial guards, replaced whole; one left out is the default."""
    org = await _org(gateway, named)
    guards = Guards.model_validate(body.model_dump(exclude_none=True))
    await dial_policy.put_guards(gateway.connections.pool, org.id, guards)
    return _guards_row(guards)


# ── its people ──


@router.get("/v1/ops/orgs/{named}/members")
async def org_members(named: str, gateway: GatewayDep) -> OrgMembersResponse:
    """The org's people, oldest first, and how many hold a seat."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    return OrgMembersResponse(
        members=[MemberRow.of(member) for member in await people.listed(pool, org.id)],
        seated=await people.seated(pool, org.id),
    )


# Takes no seat of the org's plan: this is how an org gets its first admin with sign-up shut.
# The token is answered this once; the letter goes too when the box can mail.
@router.post("/v1/ops/orgs/{named}/members", status_code=201)
async def invite_member(
    named: str, body: InviteMemberRequest, request: Request, gateway: GatewayDep
) -> InvitationResponse:
    """Invite a person into the org, the link's token once."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    invitee = people.Invitee(
        body.email, body.name, parse_role(body.role), frozenset(body.agents), body.production
    )
    invited = await people.invite(pool, org.id, invitee, seats=None, vouched=True)
    mailed = False
    card = None
    if invited.token is not None:
        card = letters.card_link(public_url(request, gateway), invited.token)
        link = Link(
            org=org.name,
            link=card,
            by=THE_OPERATOR,
            dies=invited.expires_at,
        )
        letter = letters.invitation_letter(invited.member.email, link, await letters.brand_of(pool))
        mailed = await gateway.outbox.post(org.id, letter)
    return InvitationResponse(
        member=MemberRow.of(invited.member),
        token=invited.token,
        expires_at=invited.expires_at,
        mailed=mailed,
        link=card,
    )


# Here and not on the tenant's door: an org admin could otherwise make itself the operator.
@router.put(f"{A_MEMBER}/operator")
async def make_operator(
    named: str, member: MemberId, body: OperatorRequest, gateway: GatewayDep
) -> MemberRow:
    """Whether this member runs the box; false takes it back at once."""
    org = await _org(gateway, named)
    changed = await people.make_operator(gateway.connections.pool, org.id, member, on=body.operator)
    if changed is None:
        raise NotFound(people.NOBODY_BY_THAT_ID)
    return MemberRow.of(changed)


# The tenant door's rules less "not yourself": the last active admin still stays.
@router.delete(A_MEMBER, status_code=204)
async def remove_member(named: str, member: MemberId, gateway: GatewayDep) -> None:
    """The person out of the org for good, their keys revoked."""
    org = await _org(gateway, named)
    await people.remove(gateway.connections.pool, org.id, member)
    gateway.keys.revoked(subject=member)


@router.get("/v1/ops/orgs/{named}/sso")
async def org_sso(named: str, request: Request, gateway: GatewayDep) -> OrgSsoResponse:
    """Which provider the org signs in with, never its secret."""
    connections = gateway.connections
    org = await _org(gateway, named)
    wired = await sso.sso_of(connections.pool, connections.vault, org.id)
    return sso_row(wired, f"{public_url(request, gateway)}{sso.CALLBACK}")


# The break-glass: a password opens the org again while its provider is down. Never the other
# way: requiring the provider is the org's own call.
@router.put("/v1/ops/orgs/{named}/sso/required")
async def sso_required(
    named: str, body: SsoRequiredRequest, request: Request, gateway: GatewayDep
) -> OrgSsoResponse:
    """Whether a password may still open the org beside its provider."""
    connections = gateway.connections
    org = await _org(gateway, named)
    wired = await sso.sso_of(connections.pool, connections.vault, org.id)
    if wired is None:
        raise NotFound(NO_SSO.format(slug=org.slug))
    changed = replace(wired, required=body.required)
    await sso.put_sso(connections.pool, connections.vault, changed)
    return sso_row(changed, f"{public_url(request, gateway)}{sso.CALLBACK}")


# ── its keys ──


@router.get("/v1/ops/orgs/{named}/keys")
async def org_keys(named: str, gateway: GatewayDep) -> list[KeyRow]:
    """Every key of the org, oldest first, the revoked ones named as revoked; never a key."""
    pool = gateway.connections.pool
    org = await _org(gateway, named)
    names = {member.id: member.name for member in await people.listed(pool, org.id)}
    return [key_row(row, names) for row in await keys.listed(pool, org.id)]


# The one answer that carries the key: only its fingerprint is kept.
@router.post("/v1/ops/orgs/{named}/keys")
async def issue_key(named: str, body: IssueKeyRequest, gateway: GatewayDep) -> KeyIssuedResponse:
    """A key of the org in the world named, answered this once."""
    org = await _org(gateway, named)
    scopes = KEY_SCOPES if body.scopes is None else key_scopes(body.scopes)
    issued = keys.Issued(
        org=org.id,
        env=body.env,
        scopes=scopes,
        label=body.label,
        subject=body.subject,
        name=body.name,
    )
    minted, secret = await keys.issue(gateway.connections.pool, issued)
    return KeyIssuedResponse.of(minted, secret)


# A POST, not a DELETE: the row stays, so the entries that name the key still read.
@router.post("/v1/ops/keys/{fingerprint}/revoke")
async def revoke_key(fingerprint: str, gateway: GatewayDep) -> RevokeKeyResponse:
    """One key stops opening anything from the next request on."""
    if not await keys.revoke(gateway.connections.pool, fingerprint):
        raise NotFound(NO_SUCH_KEY.format(fingerprint=fingerprint))
    gateway.keys.revoked(fingerprint=fingerprint)
    return RevokeKeyResponse(fingerprint=fingerprint, revoked=True)


# ── its vendor keys ──


@router.get("/v1/ops/orgs/{named}/provider-keys")
async def org_vendors(named: str, gateway: GatewayDep) -> VendorsResponse:
    """The vendors the org brought its own credentials for, never the credentials."""
    org = await _org(gateway, named)
    return VendorsResponse(vendors=await vault.vendors_of(gateway.connections.pool, org.id))


@router.put("/v1/ops/orgs/{named}/provider-keys/{vendor}", status_code=204)
async def put_org_vendor_key(
    named: str, vendor: str, body: ProviderKeyRequest, gateway: GatewayDep
) -> None:
    """The org's own credentials for a vendor, set for it; its calls run on them next."""
    connections = gateway.connections
    org = await _org(gateway, named)
    await vault.put_credentials(
        connections.pool, connections.vault, org.id, installed_vendor(vendor), credentials_of(body)
    )


@router.delete("/v1/ops/orgs/{named}/provider-keys/{vendor}", status_code=204)
async def drop_org_vendor_key(named: str, vendor: str, gateway: GatewayDep) -> None:
    """The org's credentials for a vendor taken back; its calls run on the box's next."""
    org = await _org(gateway, named)
    named_vendor = installed_vendor(vendor)
    if not await vault.drop_credentials(gateway.connections.pool, org.id, named_vendor):
        raise NotFound(NO_SUCH_VENDOR_KEY.format(slug=org.slug, vendor=named_vendor))


# A carrier asks within days of a call; the records reach 24 months back, the default here.
@router.get("/v1/ops/traceback")
async def number_traceback(
    gateway: GatewayDep, number: str, since: float | None = None
) -> Traceback:
    """Every phone call with a number, kept or erased, and every dial to it, of every org."""
    pool = gateway.connections.pool
    found = await traceback.of_number(pool, number, since)
    await traceback.read_by(pool, found, reads.OPERATOR)
    return found


def _org_row(org: Org) -> OrgRow:
    """An org as the doors send it."""
    return OrgRow(id=org.id, slug=org.slug, name=org.name)


def _quotas_row(quotas: Quotas) -> OrgQuotas:
    """An org's limits as the doors send them, `lends` sorted."""
    return OrgQuotas(
        limits=dict(quotas.limits),
        budget_usd=quotas.budget_usd,
        lends=None if quotas.lends is None else sorted(quotas.lends),
    )


def _guards_row(guards: Guards) -> DialGuards:
    """The dial guards as the doors send them."""
    return DialGuards(
        dial_anywhere=guards.dial_anywhere,
        per_minute=guards.per_minute,
        per_day=guards.per_day,
        max_duration_s=guards.max_duration_s,
    )


async def _org(gateway: Gateway, named: str) -> Org:
    org = await orgs.find(gateway.connections.pool, named)
    if org is None:
        raise NotFound(NO_SUCH_ORG.format(named=named))
    return org
