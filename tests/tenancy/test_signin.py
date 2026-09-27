"""Sign-in: a password, a code, a paired terminal, a sign-up that founds an org, a provider."""

import httpx
import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import (
    DeclarationRefused,
    NotAllowed,
    NotSignedIn,
    QuotaExhausted,
    UpstreamFailed,
)
from pinecall.domain.types import Key, Member, Org, Quotas
from pinecall.postgres.pool import Pool
from pinecall.tenancy.keys import verify
from pinecall.tenancy.mail import Mailbox, Outbox
from pinecall.tenancy.orgs import Admission, create, quotas_of, set_admission, set_quotas
from pinecall.tenancy.people import (
    Change,
    Invitee,
    accept,
    hash_password,
    invite,
    make_operator,
    update,
)
from pinecall.tenancy.signin import (
    HANDSHAKE,
    LOGIN_CODE,
    Asking,
    Claims,
    Client,
    Handshake,
    Holder,
    OneUse,
    OrgSso,
    Pairings,
    Signup,
    Signups,
    another_key,
    authorization_url,
    discovered,
    drop_sso,
    forgotten,
    found,
    handshake,
    key_in,
    orgs_signed_into,
    put_sso,
    reachable,
    seat_vouched,
    sign_in_with_code,
    sign_in_with_password,
    sso_of,
    sso_with_domain,
    vouched_for,
)
from pinecall.tenancy.vault import vault_of
from tests.conftest import postgres
from tests.fakes import IdentityProvider, MailServer, Postbox

VAULT = vault_of(Fernet.generate_key().decode())
ANA = Invitee(email="ana@clinica.test", name="Ana García", role="developer")
WHAT_ANA_TYPES = "correct horse battery"
CLIENT = Client("https://idp.test", "the-client", "shh-made-up")


async def _org(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


async def _seated(pool: Pool, org: Org, who: Invitee = ANA, *, vouched: bool = True) -> Member:
    invited = await invite(pool, org.id, who, seats=None, vouched=vouched)
    assert invited.token is not None
    member = await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, 8))
    assert member is not None
    return member


def _idp(idp: IdentityProvider | None = None) -> tuple[IdentityProvider, httpx.AsyncClient]:
    provider = idp or IdentityProvider()
    return provider, httpx.AsyncClient(transport=provider.transport())


def _begun(org: str | None = None) -> Handshake:
    return handshake(OneUse(HANDSHAKE), "https://box.test/v1/login/sso/callback", org=org)


@postgres
async def test_the_right_password_mints_a_key_for_the_person(pool: Pool) -> None:
    org = await _org(pool)
    member = await _seated(pool, org)
    signed = await sign_in_with_password(pool, Asking("ANA@clinica.test", WHAT_ANA_TYPES))
    assert signed.member == member
    bearer = await verify(pool, signed.secret)
    assert bearer is not None
    assert (bearer.key.subject, bearer.key.env) == (member.id, "production")


@postgres
async def test_a_wrong_password_and_a_stranger_are_one_sentence(pool: Pool) -> None:
    org = await _org(pool)
    await _seated(pool, org)
    with pytest.raises(NotSignedIn) as wrong:
        await sign_in_with_password(pool, Asking("ana@clinica.test", "not it"))
    with pytest.raises(NotSignedIn) as stranger:
        await sign_in_with_password(pool, Asking("nobody@clinica.test", WHAT_ANA_TYPES))
    assert str(wrong.value) == str(stranger.value) == "nobody answers to that email and password"


@postgres
async def test_a_disabled_member_is_told_so_only_once_the_password_matched(pool: Pool) -> None:
    org = await _org(pool)
    member = await _seated(pool, org)
    await update(pool, org.id, member.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="disabled in clinica-norte"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))
    with pytest.raises(NotSignedIn, match="nobody answers"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", "not it"))


@postgres
async def test_the_org_named_is_the_one_signed_into_and_a_wrong_one_is_nobody(pool: Pool) -> None:
    home = await _org(pool)
    other = await _org(pool, "bidfire")
    await _seated(pool, home)
    await invite(pool, other.id, ANA, seats=None)
    signed = await sign_in_with_password(
        pool, Asking("ana@clinica.test", WHAT_ANA_TYPES, "bidfire")
    )
    assert signed.key.org == other.id
    with pytest.raises(NotSignedIn):
        await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES, "ghost"))


@postgres
async def test_a_proven_person_still_invited_elsewhere_is_seated_by_signing_in_there(
    pool: Pool,
) -> None:
    home = await _org(pool)
    other = await _org(pool, "bidfire")
    await _seated(pool, home)
    await invite(pool, other.id, ANA, seats=None)
    signed = await sign_in_with_password(
        pool, Asking("ana@clinica.test", WHAT_ANA_TYPES, "bidfire")
    )
    assert signed.member is not None
    assert signed.member.status == "active"


@postgres
async def test_an_org_that_signs_in_with_its_provider_refuses_the_password_after_it_matched(
    pool: Pool,
) -> None:
    org = await _org(pool)
    await _seated(pool, org)
    await put_sso(pool, VAULT, OrgSso(org.id, CLIENT, ("clinica.test",), required=True))
    with pytest.raises(NotAllowed, match=r"clinica-norte signs in with its identity provider"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))


@postgres
async def test_the_orgs_a_password_opens_are_listed_minting_nothing(pool: Pool) -> None:
    home = await _org(pool)
    other = await _org(pool, "bidfire")
    await _seated(pool, home)
    await invite(pool, other.id, ANA, seats=None)
    opened = await orgs_signed_into(pool, "ana@clinica.test", WHAT_ANA_TYPES)
    assert [one.org for one in opened] == [home.id, other.id]
    assert await orgs_signed_into(pool, "ana@clinica.test", "not it") == []


@postgres
async def test_a_code_is_spent_once_for_the_person_that_minted_it(pool: Pool) -> None:
    org = await _org(pool)
    member = await _seated(pool, org)
    codes: OneUse[Holder] = OneUse(LOGIN_CODE)
    code, _ = codes.mint(member)
    assert code.startswith("lc_")
    signed = await sign_in_with_code(pool, codes, code)
    assert signed is not None
    assert signed.member == member
    assert await sign_in_with_code(pool, codes, code) is None


@postgres
async def test_a_servers_key_minted_a_code_gives_a_browser_a_key_like_its_own(pool: Pool) -> None:
    org = await _org(pool)
    codes: OneUse[Holder] = OneUse(LOGIN_CODE)
    server = Key("k_1", org.id, env="sandbox", scopes=frozenset({"app", "calls"}))
    code, _ = codes.mint(server)
    signed = await sign_in_with_code(pool, codes, code)
    assert signed is not None
    assert (signed.key.env, signed.key.scopes, signed.member) == ("sandbox", server.scopes, None)


def test_a_code_dies_on_its_own_and_a_stranger_is_none() -> None:
    now = [100.0]
    codes: OneUse[str] = OneUse(LOGIN_CODE, clock=lambda: now[0])
    code, dies = codes.mint("x")
    assert dies == 400.0
    assert codes.spend("lc_nobody") is None
    now[0] = 401
    assert codes.spend(code) is None


def test_two_codes_are_never_the_same_word() -> None:
    codes: OneUse[str] = OneUse(LOGIN_CODE)
    assert codes.mint("a")[0] != codes.mint("a")[0]


def test_a_terminal_prints_a_word_a_browser_answers_and_the_terminal_collects_once() -> None:
    pairings = Pairings()
    code, _ = pairings.open("Ana's laptop")
    asked = pairings.asking(code)
    assert asked is not None
    assert (asked.device, asked.answered) == ("Ana's laptop", False)
    assert pairings.collect(code).waiting
    assert pairings.fill(code, "pc_live_x", "org_1")
    assert not pairings.fill(code, "pc_live_y", "org_1")
    assert pairings.collect(code).key == "pc_live_x"
    assert pairings.collect(code) == pairings.collect("cli_nobody")
    assert not pairings.collect(code).waiting


def test_the_code_is_six_digits_and_only_its_hash_is_kept() -> None:
    signups = Signups()
    code = signups.begin(Signup("ana@c.test", "clinica", "Ana", "h"))
    assert len(code) == 6
    assert code.isdigit()
    assert code.encode() not in signups.pending["ana@c.test"].code_hash


def test_the_right_code_takes_the_sign_up_out_once() -> None:
    signups = Signups()
    signup = Signup("ana@c.test", "clinica", "Ana", "h")
    code = signups.begin(signup)
    assert signups.verify("ana@c.test", code) == signup
    assert signups.verify("ana@c.test", code) == "wrong"


def test_six_wrong_codes_burn_it() -> None:
    signups = Signups()
    code = signups.begin(Signup("ana@c.test", "clinica", "Ana", "h"))
    wrong = "000000" if code != "000000" else "000001"
    outcomes = [signups.verify("ana@c.test", wrong) for _ in range(6)]
    assert outcomes == ["wrong"] * 5 + ["burned"]
    assert signups.verify("ana@c.test", code) == "burned"


def test_a_code_dies_at_fifteen_minutes_and_a_renewed_one_replaces_the_first() -> None:
    now = [0.0]
    signups = Signups(clock=lambda: now[0])
    first = signups.begin(Signup("ana@c.test", "clinica", "Ana", "h"))
    renewed = signups.renewed("ana@c.test")
    assert renewed is not None
    assert signups.verify("ana@c.test", first) == "wrong"
    now[0] = 15 * 60 + 1
    assert signups.verify("ana@c.test", renewed[1]) == "expired"
    assert signups.renewed("nobody@c.test") is None


@postgres
async def test_a_verified_sign_up_founds_the_org_seats_its_admin_and_hands_a_key_and_a_code(
    pool: Pool,
) -> None:
    codes: OneUse[Holder] = OneUse(LOGIN_CODE)
    signup = Signup("ana@c.test", "clinica", "Ana", await hash_password(WHAT_ANA_TYPES, 8))
    founded = await found(pool, signup, codes)
    assert (founded.org.slug, founded.admin.role, founded.admin.status) == (
        "clinica",
        "admin",
        "active",
    )
    assert (await verify(pool, founded.signed_in.secret)) is not None
    assert codes.spend(founded.code) == founded.admin
    assert (
        await sign_in_with_password(pool, Asking("ana@c.test", WHAT_ANA_TYPES))
    ).key.org == founded.org.id


@postgres
async def test_a_second_org_of_the_same_person_is_born_as_the_box_says_for_later_ones(
    pool: Pool,
) -> None:
    trial, closed = Quotas(minutes=30), Quotas(minutes=0)
    await set_admission(pool, Admission(first={"sandbox": trial}, later={"sandbox": closed}))
    codes: OneUse[Holder] = OneUse(LOGIN_CODE)
    hashed = await hash_password(WHAT_ANA_TYPES, 8)
    first = await found(pool, Signup("ana@c.test", "clinica", "Ana", hashed), codes)
    second = await found(pool, Signup("ana@c.test", "clinica-sur", "Ana", hashed), codes)
    assert await quotas_of(pool, first.org.id, "sandbox") == trial
    assert await quotas_of(pool, second.org.id, "sandbox") == closed


@postgres
async def test_the_same_person_gets_a_key_for_another_device_that_dies_with_the_asking_one(
    pool: Pool,
) -> None:
    org = await _org(pool)
    member = await _seated(pool, org)
    signed = await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))
    paired = await another_key(pool, signed.key, member)
    assert paired.key.subject == member.id
    with pytest.raises(NotAllowed, match="a server's token names nobody"):
        await another_key(pool, Key("k_s", org.id), None)
    await update(pool, org.id, member.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="no longer an active member"):
        await another_key(pool, signed.key, await _disabled(pool, org, member))


async def _disabled(pool: Pool, org: Org, member: Member) -> Member:
    return await update(pool, org.id, member.id, Change(status="disabled"))


@postgres
async def test_a_person_opens_another_org_of_theirs_and_an_operator_opens_any(pool: Pool) -> None:
    home = await _org(pool)
    other = await _org(pool, "bidfire")
    stranger = await _org(pool, "acme")
    member = await _seated(pool, home)
    # Proven at home with a password, she is seated in the other org at once.
    await invite(pool, other.id, ANA, seats=None)
    assert (await key_in(pool, member, other.id)).key.org == other.id
    with pytest.raises(NotAllowed, match="not an org of this person's"):
        await key_in(pool, member, stranger.id)
    operator = await make_operator(pool, home.id, member.id, on=True)
    assert operator is not None
    visit = await key_in(pool, operator, stranger.id)
    assert (visit.key.org, visit.key.subject) == (stranger.id, member.id)
    assert (await verify(pool, visit.secret)) is not None


@postgres
async def test_a_forgotten_password_mails_a_link_and_says_nothing_about_a_stranger(
    pool: Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    postbox = Postbox()
    monkeypatch.setattr(MailServer, "postbox", postbox)
    monkeypatch.setattr("smtplib.SMTP", MailServer)
    org = await _org(pool)
    await _seated(pool, org)
    box = Mailbox("smtp.box.test", 587, "starttls", "u", "p", "Box <no-reply@box.test>")
    outbox = Outbox(pool, VAULT, box)
    await forgotten(pool, outbox, "ana@clinica.test", "https://box.test")
    await forgotten(pool, outbox, "nobody@clinica.test", "https://box.test")
    await outbox.drained()
    (letter,) = postbox.sent
    assert letter["To"] == "ana@clinica.test"
    assert "https://box.test/invitations/inv_" in letter.as_string()


@postgres
async def test_an_orgs_provider_round_trips_sealed_and_is_found_by_domain(pool: Pool) -> None:
    org = await _org(pool)
    sso = OrgSso(org.id, CLIENT, ("clinica.test", "clinica.uy"), role="qa")
    await put_sso(pool, VAULT, sso)
    assert await sso_of(pool, VAULT, org.id) == sso
    assert [one.org for one in await sso_with_domain(pool, VAULT, "clinica.uy")] == [org.id]
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT * FROM org_sso")).fetchone()
    assert row is not None
    assert "shh-made-up" not in str(dict(row))
    assert await drop_sso(pool, org.id)
    assert not await drop_sso(pool, org.id)
    assert sso.admits("Bo@Clinica.TEST")
    assert not sso.admits("bo@other.test")


def test_an_issuer_or_a_domain_that_is_not_one_is_refused_before_anything_is_fetched() -> None:
    with pytest.raises(DeclarationRefused, match="trailing slash"):
        Client("https://idp.test/", "c", "s")
    with pytest.raises(DeclarationRefused, match="https URL"):
        Client("http://idp.test", "c", "s")
    with pytest.raises(DeclarationRefused, match="not an email domain"):
        OrgSso("org_1", CLIENT, ("Clinica.Test",))


def test_an_endpoint_the_box_will_not_knock_at_is_refused_by_field() -> None:
    for bad in (
        "http://idp.test/x",
        "https://localhost/x",
        "https://10.0.0.1/x",
        "https://a.internal/x",
    ):
        with pytest.raises(NotAllowed, match="jwks_uri"):
            reachable(bad, issuer="https://idp.test", field="jwks_uri")
    assert (
        reachable("https://idp.test/x", issuer="https://idp.test", field="f")
        == "https://idp.test/x"
    )


async def test_a_configuration_naming_another_issuer_or_no_endpoint_is_refused() -> None:
    idp, http = _idp(IdentityProvider(issuer="https://other.test"))
    with pytest.raises(UpstreamFailed, match="one of the two is wrong"):
        await discovered(httpx.AsyncClient(transport=idp.transport()), "https://idp.test")
    await http.aclose()
    silent = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    with pytest.raises(UpstreamFailed, match="not an OpenID provider"):
        await discovered(silent, "https://idp.test")


async def test_the_authorization_url_merges_its_query_into_one_the_endpoint_already_carries() -> (
    None
):
    _, http = _idp()
    provider = await discovered(http, "https://idp.test")
    begun = _begun(org="org_1")
    url = httpx.URL(authorization_url(provider, CLIENT, begun))
    assert url.params["prompt"] == "select_account"
    assert url.params["state"] == begun.state
    assert url.params["code_challenge_method"] == "S256"
    assert begun.verifier not in str(url)


async def test_an_exchange_refused_or_answered_with_no_json_is_a_refusal_and_not_a_crash() -> None:
    _, http = _idp(IdentityProvider(refusal=(400, '{"error_description": "code used already"}')))
    with pytest.raises(NotSignedIn, match="code used already"):
        await vouched_for(http, CLIENT, _begun(), "the-code")
    _, http = _idp(IdentityProvider(refusal=(200, "not json")))
    with pytest.raises((NotSignedIn, UpstreamFailed)):
        await vouched_for(http, CLIENT, _begun(), "the-code")


async def test_a_signed_id_token_with_this_sign_ins_nonce_vouches_for_a_verified_address() -> None:
    idp, http = _idp()
    begun = _begun()
    idp.id_token = idp.signed(nonce=begun.nonce)
    said = await vouched_for(http, CLIENT, begun, "the-code")
    assert (said.email, said.name, said.email_verified) == ("ana@clinica.test", "Ana García", True)
    assert idp.exchanged[0]["code_verifier"] == begun.verifier


async def test_an_id_token_for_another_sign_in_or_an_unverified_address_is_refused() -> None:
    idp, http = _idp()
    idp.id_token = idp.signed(nonce="somebody-elses")
    with pytest.raises(NotSignedIn, match="different sign-in"):
        await vouched_for(http, CLIENT, _begun(), "c")
    begun = _begun()
    idp.id_token = idp.signed(nonce=begun.nonce, email_verified=False)
    with pytest.raises(NotSignedIn, match="has not verified"):
        await vouched_for(http, CLIENT, begun, "c")


async def test_an_id_token_for_two_audiences_names_this_client_as_the_authorized_party() -> None:
    idp, http = _idp()
    begun = _begun()
    idp.id_token = idp.signed(nonce=begun.nonce, aud=["the-client", "another"])
    with pytest.raises(NotSignedIn, match="issued to None"):
        await vouched_for(http, CLIENT, begun, "c")
    idp.id_token = idp.signed(nonce=begun.nonce, aud=["the-client", "another"], azp="the-client")
    assert (await vouched_for(http, CLIENT, begun, "c")).subject == "sub-ana"


async def test_an_id_token_naming_no_key_is_verified_against_the_one_key_published() -> None:
    idp, http = _idp(IdentityProvider(kid=None))
    begun = _begun()
    idp.id_token = idp.signed(nonce=begun.nonce)
    assert (await vouched_for(http, CLIENT, begun, "c")).email == "ana@clinica.test"


async def test_a_provider_that_takes_only_basic_auth_is_signed_in_to_that_way() -> None:
    idp, http = _idp(IdentityProvider(basic_only=True))
    begun = _begun()
    idp.id_token = idp.signed(nonce=begun.nonce)
    await vouched_for(http, CLIENT, begun, "c")
    assert "client_secret" not in idp.exchanged[0]


@postgres
async def test_a_provider_seats_a_member_invited_and_makes_one_uninvited_when_told_to(
    pool: Pool,
) -> None:
    org = await _org(pool)
    idp, _ = _idp()
    said_ana = await _claims(idp, "ana@clinica.test")
    told = OrgSso(org.id, CLIENT, ("clinica.test",), role="qa")
    await invite(pool, org.id, ANA, seats=None)
    seated_ana = await seat_vouched(pool, org, told, said_ana)
    assert (seated_ana.status, seated_ana.verified, seated_ana.role) == (
        "active",
        True,
        "developer",
    )
    seated_bo = await seat_vouched(pool, org, told, await _claims(idp, "bo@clinica.test"))
    assert (seated_bo.role, seated_bo.name) == ("qa", "bo")
    untold = OrgSso(org.id, CLIENT, ("clinica.test",))
    with pytest.raises(NotAllowed, match="seats nobody it was not told to"):
        await seat_vouched(pool, org, untold, await _claims(idp, "cy@clinica.test"))
    await update(pool, org.id, seated_bo.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="disabled"):
        await seat_vouched(pool, org, told, await _claims(idp, "bo@clinica.test"))


@postgres
async def test_a_person_the_provider_seats_unasked_takes_a_seat_like_an_invited_one(
    pool: Pool,
) -> None:
    org = await _org(pool)
    await set_quotas(pool, org.id, "production", Quotas(seats=1))
    await _seated(pool, org)
    idp, _ = _idp()
    told = OrgSso(org.id, CLIENT, ("clinica.test",), role="qa")
    with pytest.raises(QuotaExhausted, match="seats"):
        await seat_vouched(pool, org, told, await _claims(idp, "bo@clinica.test"))


async def _claims(idp: IdentityProvider, email: str) -> Claims:
    begun = _begun()
    name = "Ana García" if email.startswith("ana@") else None
    idp.id_token = idp.signed(nonce=begun.nonce, email=email, sub=f"sub-{email}", name=name)
    async with httpx.AsyncClient(transport=idp.transport()) as http:
        return await vouched_for(http, CLIENT, begun, "c")
