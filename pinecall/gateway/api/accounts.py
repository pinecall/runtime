"""Who a key is, and how a person gets one: sign-in, codes, the org switch, a paired terminal."""

from importlib.metadata import version

from fastapi import APIRouter, Request, Response

from pinecall.domain.errors import Conflict, DeclarationRefused, NotAllowed, NotFound, NotSignedIn
from pinecall.domain.names import PRODUCTION, other_world
from pinecall.domain.person import Member
from pinecall.gateway._deps import (
    HOST,
    ActingDep,
    BearerDep,
    GatewayDep,
    check_knock,
    client_of,
    public_url,
)
from pinecall.tenancy import keys, letters, mail, orgs, people, signin
from pinecall.tenancy.signin import Asking
from pinecall.wire.rest.accounts import (
    AcceptInvitationRequest,
    BrandRow,
    EmptyResponse,
    FirstKeyResponse,
    ForgotPasswordRequest,
    GatewayInfoResponse,
    KeyCollectedResponse,
    KeyIssuedResponse,
    MemberRow,
    MintCodeResponse,
    OpenPairingRequest,
    OrgOfPersonRow,
    OrgsOfPersonResponse,
    PairingApprovedResponse,
    PairingStatusResponse,
    SignInOrgRow,
    SignInOrgsRequest,
    SignInOrgsResponse,
    SignInRequest,
    SwitchOrgRequest,
    WhoamiResponse,
)

router = APIRouter()


ONE_OR_THE_OTHER = "log in with org, email and password, or with a code: one of the two"


# One sentence for a code spent, dead or never minted.
NO_CODE = "no code answers to that: it was used, it expired, or it never existed"


# One sentence for a word collected, dead or never printed, so nobody probes which existed.
NO_PAIRING = "no pairing answers to that: it was collected, it expired, or it never existed"


ANSWERED = "that terminal is already signed in: it has a key waiting"


NOT_A_PERSON = "an org's own key names nobody: a terminal is signed in as a person"


NO_INVITATION = (
    "no open invitation answers to that token: it was used, it expired, or it never existed"
)


# What a listed org says of a person of the box who is no member there.
AS_THE_OPERATOR = "operator"


FIRST_KEY = "invitation"


@router.get("/.well-known/pinecall")
async def gateway_info(request: Request, gateway: GatewayDep) -> GatewayInfoResponse:
    """What this gateway is and how it signs people in, before anybody holds a key."""
    connections = gateway.connections
    settings = connections.settings
    world = settings.world_named(request.headers.get(HOST))
    box_mail = await mail.box_mail_of(
        connections.pool, connections.vault, gateway.outbox.environment
    )
    brand = await letters.brand_of(connections.pool)
    return GatewayInfoResponse(
        version=version("pinecall"),
        signup=connections.settings.signup,
        min_password=connections.settings.min_password,
        mail=box_mail is not None,
        brand=BrandRow(name=brand.name, logo_url=brand.logo_url, accent=brand.accent),
        google=False,
        world=world,
        elsewhere=None if world is None else settings.address_of(other_world(world)),
    )


@router.get("/v1/whoami")
async def whoami(key: ActingDep, gateway: GatewayDep) -> WhoamiResponse:
    """Whose key this is, the world this request acts in, and what the key opens."""
    minted, member = key.bearer.key, key.bearer.member
    org = await orgs.find(gateway.connections.pool, minted.org)
    return WhoamiResponse(
        org=minted.org,
        slug=None if org is None else org.slug,
        key_id=minted.key_id,
        label=minted.label,
        env=key.env,
        scopes=sorted(minted.scopes),
        subject=minted.subject,
        name=minted.name,
        email=None if member is None else member.email,
        operator=member is not None and member.operator,
        visiting=member is not None and member.org != minted.org,
        production=minted.env == PRODUCTION if member is None else member.opens_production,
    )


# The body says a password or a code; each is its own sign-in, and a body with both is refused.
@router.post("/v1/login")
async def sign_in(body: SignInRequest, request: Request, gateway: GatewayDep) -> KeyIssuedResponse:
    """A key for a person and a device, from a password, or from a one-use code."""
    pool = gateway.connections.pool
    if body.code is not None:
        if body.org is not None or body.email is not None or body.password is not None:
            raise DeclarationRefused(ONE_OR_THE_OTHER)
        signed = await signin.sign_in_with_code(
            pool, gateway.signins.codes, body.code, device=body.device
        )
        if signed is None:
            raise NotFound(NO_CODE)
        return KeyIssuedResponse.of(signed.key, signed.secret)
    if body.email is None or body.password is None:
        raise DeclarationRefused(ONE_OR_THE_OTHER)
    knock = f"{client_of(request)} {body.org or '*'}/{body.email}"
    check_knock(gateway, knock, signin.TOO_MANY.format(email=body.email))
    typed = Asking(body.email, body.password, body.org, body.device)
    signed = await signin.sign_in_with_password(pool, typed)
    return KeyIssuedResponse.of(signed.key, signed.secret)


# Throttled as the login is, and refused in its sentence, so a wrong password says nothing more.
@router.post("/v1/login/orgs")
async def sign_in_orgs(
    body: SignInOrgsRequest, request: Request, gateway: GatewayDep
) -> SignInOrgsResponse:
    """The orgs an address and a password open, minting nothing."""
    pool = gateway.connections.pool
    knock = f"{client_of(request)} */{body.email}"
    check_knock(gateway, knock, signin.TOO_MANY.format(email=body.email))
    opened = await signin.orgs_signed_into(pool, body.email, body.password)
    if not opened:
        raise NotSignedIn(signin.NOBODY)
    rows: list[SignInOrgRow] = []
    for row in opened:
        org = await orgs.find(pool, row.org)
        if org is not None:
            rows.append(SignInOrgRow(org=org.id, slug=org.slug, name=org.name, role=row.role))
    return SignInOrgsResponse(orgs=rows)


# A person of the box also sees every org there is, to visit without taking a seat.
@router.get("/v1/login/orgs")
async def list_orgs_of_person(key: BearerDep, gateway: GatewayDep) -> OrgsOfPersonResponse:
    """Every org this key's person may open, oldest first, and which one this key opens."""
    pool = gateway.connections.pool
    person = _person_of(key.member)
    rows: list[OrgOfPersonRow] = []
    for row in await people.orgs_of(pool, person.email):
        if row.status == "disabled":
            continue
        org = await orgs.find(pool, row.org)
        rows.append(
            OrgOfPersonRow(
                org=row.org,
                slug=None if org is None else org.slug,
                name=None if org is None else org.name,
                role=row.role,
                status=row.status,
                here=row.org == key.key.org,
                member=True,
            )
        )
    if not person.operator:
        return OrgsOfPersonResponse(orgs=rows)
    theirs = {row.org for row in rows}
    for org in await orgs.listed(pool):
        if org.id in theirs:
            continue
        rows.append(
            OrgOfPersonRow(
                org=org.id,
                slug=org.slug,
                name=org.name,
                role=AS_THE_OPERATOR,
                status="active",
                here=org.id == key.key.org,
                member=False,
            )
        )
    return OrgsOfPersonResponse(orgs=rows)


@router.post("/v1/login/org")
async def switch_org(
    body: SwitchOrgRequest, key: BearerDep, gateway: GatewayDep
) -> KeyIssuedResponse:
    """The same person's key in another org of theirs, or a visit for a person of the box."""
    pool = gateway.connections.pool
    person = _person_of(key.member)
    org = await orgs.find(pool, body.org)
    if org is None:
        raise NotAllowed(signin.NOT_THEIRS.format(org=body.org))
    signed = await signin.key_in(pool, person, org.id, label=key.key.label)
    return KeyIssuedResponse.of(signed.key, signed.secret)


# A browser signs in with the code, so a key never rides a URL.
@router.post("/v1/login/codes")
async def mint_code(key: BearerDep, gateway: GatewayDep) -> MintCodeResponse:
    """A one-use word that gives a browser a key like this one, for five minutes."""
    code, expires_at = await gateway.signins.codes.mint(key.key)
    return MintCodeResponse(code=code, expires_at=expires_at)


# 202 whoever asks, and throttled like the login, so the door says nothing about who exists.
@router.post("/v1/login/reset", status_code=202)
async def forget_password(
    body: ForgotPasswordRequest, request: Request, gateway: GatewayDep
) -> EmptyResponse:
    """Mail a one-use link that sets the password, where one can go."""
    knock = f"{client_of(request)} */{body.email}"
    check_knock(gateway, knock, signin.TOO_MANY.format(email=body.email))
    await signin.forgotten(
        gateway.connections.pool, gateway.outbox, body.email, public_url(request, gateway)
    )
    return EmptyResponse()


@router.post("/v1/login/pairings")
async def open_pairing(body: OpenPairingRequest, gateway: GatewayDep) -> MintCodeResponse:
    """The word a terminal prints for a browser to approve, and when it dies."""
    code, expires_at = await gateway.signins.pairings.open(body.device)
    return MintCodeResponse(code=code, expires_at=expires_at)


# Reading it does not spend it, so the approving page can be reloaded.
@router.get("/v1/login/pairings/{code}")
async def pairing_status(code: str, gateway: GatewayDep) -> PairingStatusResponse:
    """Which terminal a browser is about to sign in, and whether it is answered."""
    waiting = await gateway.signins.pairings.asking(code)
    if waiting is None:
        raise NotFound(NO_PAIRING)
    return PairingStatusResponse(
        device=waiting.device, expires_at=waiting.expires_at, answered=waiting.answered
    )


# The password is typed in the browser, never the terminal, so this works with SSO too; the
# terminal gets a key of its own, never the browser's.
@router.post("/v1/login/pairings/{code}")
async def approve_pairing(
    code: str, key: BearerDep, gateway: GatewayDep
) -> PairingApprovedResponse:
    """Sign the terminal in as the person this browser is."""
    if key.key.subject is None or key.member is None:
        raise NotAllowed(NOT_A_PERSON)
    pairings = gateway.signins.pairings
    waiting = await pairings.asking(code)
    if waiting is None:
        raise NotFound(NO_PAIRING)
    if waiting.answered:
        raise Conflict(ANSWERED)
    signed = await signin.another_key(
        gateway.connections.pool, key.key, key.member, device=waiting.device
    )
    if not await pairings.fill(code, signed.secret, signed.key.org):
        raise Conflict(ANSWERED)
    return PairingApprovedResponse(device=waiting.device, org=signed.key.org)


# 202 is "ask again"; 404 is a word gone, and an empty 200 would read as a key of "".
@router.get("/v1/login/pairings/{code}/key")
async def collect_key(
    code: str, response: Response, gateway: GatewayDep
) -> KeyCollectedResponse | EmptyResponse:
    """The terminal's key, once; 202 while nobody has approved."""
    collected = await gateway.signins.pairings.collect(code)
    if collected.key is not None:
        return KeyCollectedResponse(key=collected.key)
    if not collected.waiting:
        raise NotFound(NO_PAIRING)
    response.status_code = 202
    return EmptyResponse()


# No key: the week-long, one-use token is the credential.
@router.post("/v1/invitations/{token}")
async def accept_invitation(
    token: str, body: AcceptInvitationRequest, gateway: GatewayDep
) -> FirstKeyResponse:
    """Choose a password: the member is active, and here is their first key."""
    pool = gateway.connections.pool
    hashed = await people.hash_password(body.password, gateway.connections.settings.min_password)
    member = await people.accept(pool, token, hashed)
    if member is None:
        raise NotFound(NO_INVITATION)
    minted, secret = await keys.person_key(pool, member, label=body.device or FIRST_KEY)
    issued = KeyIssuedResponse.of(minted, secret)
    return FirstKeyResponse(**issued.model_dump(), member=MemberRow.of(member))


def _person_of(member: Member | None) -> Member:
    if member is None:
        raise NotAllowed(signin.NOT_A_PERSONS)
    return member
