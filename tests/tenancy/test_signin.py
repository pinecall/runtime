"""Sign-in: a password, a code, a paired terminal, a sign-up that founds an org, a provider."""

import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import (
    NotAllowed,
    NotSignedIn,
    UpstreamFailed,
)
from pinecall.domain.org import Org, Quotas
from pinecall.domain.person import Key, Member
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections, vault_of
from pinecall.tenancy.admission import Admission, quotas_of, set_admission
from pinecall.tenancy.keys import person_key, verify
from pinecall.tenancy.mail import Mailbox, Outbox
from pinecall.tenancy.orgs import create
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
    Asking,
    Handshake,
    SignIns,
    Signup,
    another_key,
    forgotten,
    found,
    key_in,
    orgs_signed_into,
    sign_in_with_code,
    sign_in_with_password,
)
from pinecall.tenancy.sso import (
    Claims,
    Client,
    authorization_url,
    discovered,
    reachable,
    vouched_for,
)
from pinecall.tenancy.words import Words
from tests.conftest import postgres
from tests.fakes.idp import IdentityProvider
from tests.fakes.mail import MailServer, Postbox

VAULT = vault_of(Fernet.generate_key().decode())
ANA = Invitee(email="ana@clinica.test", name="Ana García", role="developer")
WHAT_ANA_TYPES = "correct horse battery"
CLIENT = Client("https://idp.test", "the-client", "shh-made-up")


async def org_of(pool: Pool, slug: str = "clinica-norte") -> Org:
    return await create(pool, slug, slug.title())


async def seated(pool: Pool, org: Org, who: Invitee = ANA, *, vouched: bool = True) -> Member:
    invited = await invite(pool, org.id, who, seats=None, vouched=vouched)
    assert invited.token is not None
    member = await accept(pool, invited.token, await hash_password(WHAT_ANA_TYPES, 8))
    assert member is not None
    return member


def idp_of(idp: IdentityProvider | None = None) -> tuple[IdentityProvider, httpx.AsyncClient]:
    provider = idp or IdentityProvider()
    return provider, httpx.AsyncClient(transport=provider.transport())


# The handshake as `handshake` makes it, without keeping it: these read it, never spend it.
def begun_signin(org: str | None = None) -> Handshake:
    return Handshake(
        state=f"st_{secrets.token_urlsafe(8)}",
        org=org,
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(32),
        redirect_uri="https://box.test/v1/login/sso/callback",
    )


def kept_on(pool: Pool, clock: Callable[[], float] = time.time) -> SignIns:
    """The sign-ins kept on the test's schema, timed by the clock."""
    return SignIns.kept(Words(pool, VAULT, clock))


@postgres
async def test_the_right_password_mints_a_key_for_the_person(pool: Pool) -> None:
    org = await org_of(pool)
    member = await seated(pool, org)
    typed = Asking("ANA@clinica.test", WHAT_ANA_TYPES, device="phone")
    signed = await sign_in_with_password(pool, typed)
    assert signed.member == member
    bearer = await verify(pool, signed.secret)
    assert bearer is not None
    assert (bearer.key.subject, bearer.key.env, bearer.key.label) == (
        member.id,
        "production",
        "phone",
    )


@postgres
async def test_a_wrong_password_and_a_stranger_are_one_sentence(pool: Pool) -> None:
    org = await org_of(pool)
    await seated(pool, org)
    with pytest.raises(NotSignedIn) as wrong:
        await sign_in_with_password(pool, Asking("ana@clinica.test", "not it"))
    with pytest.raises(NotSignedIn) as stranger:
        await sign_in_with_password(pool, Asking("nobody@clinica.test", WHAT_ANA_TYPES))
    assert str(wrong.value) == str(stranger.value) == "nobody answers to that email and password"


@postgres
async def test_a_disabled_member_is_told_so_only_once_the_password_matched(pool: Pool) -> None:
    org = await org_of(pool)
    member = await seated(pool, org)
    await update(pool, org.id, member.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="disabled in clinica-norte"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))
    with pytest.raises(NotSignedIn, match="nobody answers"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", "not it"))


@postgres
async def test_the_org_named_is_the_one_signed_into_and_a_wrong_one_is_nobody(pool: Pool) -> None:
    home = await org_of(pool)
    other = await org_of(pool, "bidfire")
    await seated(pool, home)
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
    home = await org_of(pool)
    other = await org_of(pool, "bidfire")
    await seated(pool, home)
    await invite(pool, other.id, ANA, seats=None)
    signed = await sign_in_with_password(
        pool, Asking("ana@clinica.test", WHAT_ANA_TYPES, "bidfire")
    )
    assert signed.member is not None
    assert signed.member.status == "active"


@postgres
async def test_the_orgs_a_password_opens_are_listed_minting_nothing(pool: Pool) -> None:
    home = await org_of(pool)
    other = await org_of(pool, "bidfire")
    await seated(pool, home)
    await invite(pool, other.id, ANA, seats=None)
    opened = await orgs_signed_into(pool, "ana@clinica.test", WHAT_ANA_TYPES)
    assert [item.org for item in opened] == [home.id, other.id]
    assert await orgs_signed_into(pool, "ana@clinica.test", "not it") == []


@postgres
async def test_a_code_is_spent_once_for_the_person_that_minted_it(pool: Pool) -> None:
    org = await org_of(pool)
    member = await seated(pool, org)
    codes = kept_on(pool).codes
    code, _ = await codes.mint(member)
    assert code.startswith("lc_")
    signed = await sign_in_with_code(pool, codes, code)
    assert signed is not None
    assert signed.member == member
    assert await sign_in_with_code(pool, codes, code) is None


@postgres
async def test_a_servers_key_minted_a_code_gives_a_browser_a_key_like_its_own(pool: Pool) -> None:
    org = await org_of(pool)
    codes = kept_on(pool).codes
    server = Key("k_1", org.id, env="sandbox", scopes=frozenset({"app", "calls"}))
    code, _ = await codes.mint(server)
    signed = await sign_in_with_code(pool, codes, code)
    assert signed is not None
    assert (signed.key.env, signed.key.scopes, signed.member) == ("sandbox", server.scopes, None)


@postgres
async def test_a_persons_key_minted_a_code_gives_the_browser_that_person_dying_with_the_key(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    member = await seated(pool, org)
    dies = datetime(2030, 1, 1, tzinfo=UTC)
    parent, _ = await person_key(pool, member, parent=Key("k_p", org.id, expires_at=dies))
    codes = kept_on(pool).codes
    code, _ = await codes.mint(parent)
    signed = await sign_in_with_code(pool, codes, code, device="chrome")
    assert signed is not None
    assert (signed.key.subject, signed.key.name, signed.key.label) == (
        member.id,
        member.name,
        "chrome",
    )
    assert signed.key.expires_at == dies
    bearer = await verify(pool, signed.secret)
    assert bearer is not None
    assert bearer.member == member


@postgres
async def test_a_code_dies_on_its_own_and_a_stranger_is_none(pool: Pool) -> None:
    now = [100.0]
    codes = kept_on(pool, lambda: now[0]).codes
    code, dies = await codes.mint(Key("k_1", "org_1"))
    assert dies == 400.0
    assert await codes.spend("lc_nobody") is None
    now[0] = 401
    assert await codes.spend(code) is None


@postgres
async def test_two_codes_are_never_the_same_word_and_the_table_keeps_neither_word(
    pool: Pool,
) -> None:
    codes = kept_on(pool).codes
    first, _ = await codes.mint(Key("k_1", "org_1"))
    second, _ = await codes.mint(Key("k_1", "org_1"))
    assert first != second
    async with pool.connection() as connection:
        rows = await (
            await connection.execute("select word_hash, sealed from one_use_words")
        ).fetchall()
    kept = b"".join(bytes(row["word_hash"]) + row["sealed"].encode() for row in rows)
    assert first.encode() not in kept
    assert b"k_1" not in kept


@postgres
async def test_a_terminal_prints_a_word_a_browser_answers_and_the_terminal_collects_once(
    pool: Pool,
) -> None:
    pairings = kept_on(pool).pairings
    code, _ = await pairings.open("Ana's laptop")
    params = await pairings.asking(code)
    assert params is not None
    assert (params.device, params.answered) == ("Ana's laptop", False)
    assert (await pairings.collect(code)).waiting
    assert await pairings.fill(code, "pc_live_x", "org_1")
    assert not await pairings.fill(code, "pc_live_y", "org_1")
    assert (await kept_on(pool).pairings.collect(code)).key == "pc_live_x"
    assert await pairings.collect(code) == await pairings.collect("cli_nobody")
    assert not (await pairings.collect(code)).waiting


@postgres
async def test_the_code_is_six_digits_and_the_right_one_takes_the_sign_up_out_once(
    pool: Pool,
) -> None:
    signups = kept_on(pool, lambda: 100.0).signups
    signup = Signup("ana@c.test", "clinica", "Ana", "h")
    code, expires_at = await signups.begin(signup)
    assert expires_at == 100.0 + 15 * 60
    assert len(code) == 6
    assert code.isdigit()
    assert await kept_on(pool, lambda: 100.0).signups.verify("ana@c.test", code) == signup
    assert await signups.verify("ana@c.test", code) == "wrong"


@postgres
async def test_six_wrong_codes_burn_it(pool: Pool) -> None:
    signups = kept_on(pool).signups
    code, _ = await signups.begin(Signup("ana@c.test", "clinica", "Ana", "h"))
    wrong = "000000" if code != "000000" else "000001"
    outcomes = [await signups.verify("ana@c.test", wrong) for _ in range(6)]
    assert outcomes == ["wrong"] * 5 + ["burned"]
    assert await signups.verify("ana@c.test", code) == "burned"


@postgres
async def test_a_code_dies_at_fifteen_minutes_and_a_renewed_one_replaces_the_first(
    pool: Pool,
) -> None:
    now = [0.0]
    signups = kept_on(pool, lambda: now[0]).signups
    first, _ = await signups.begin(Signup("ana@c.test", "clinica", "Ana", "h"))
    renewed = await signups.renewed("ana@c.test")
    assert renewed is not None
    assert await signups.verify("ana@c.test", first) == "wrong"
    now[0] = 15 * 60 + 1
    assert await signups.verify("ana@c.test", renewed[1]) == "expired"
    assert await signups.renewed("nobody@c.test") is None


@postgres
async def test_a_verified_sign_up_founds_the_org_seats_its_admin_and_hands_a_key_and_a_code(
    pool: Pool,
) -> None:
    codes = kept_on(pool).codes
    signup = Signup("ana@c.test", "clinica", "Ana", await hash_password(WHAT_ANA_TYPES, 8))
    founded = await found(pool, signup, codes)
    assert (founded.org.slug, founded.admin.role, founded.admin.status) == (
        "clinica",
        "admin",
        "active",
    )
    assert (await verify(pool, founded.signed_in.secret)) is not None
    assert founded.signed_in.key.label == "signup"
    kept = await codes.alive(founded.code)
    assert kept is not None
    assert (kept.value, kept.expires_at) == (founded.admin, founded.code_expires_at)
    assert await codes.spend(founded.code) == founded.admin
    assert (
        await sign_in_with_password(pool, Asking("ana@c.test", WHAT_ANA_TYPES))
    ).key.org == founded.org.id


@postgres
async def test_a_second_org_of_the_same_person_is_born_as_the_box_says_for_later_ones(
    pool: Pool,
) -> None:
    trial, closed = Quotas(minutes=30), Quotas(minutes=0)
    await set_admission(pool, Admission(first={"sandbox": trial}, later={"sandbox": closed}))
    codes = kept_on(pool).codes
    hashed = await hash_password(WHAT_ANA_TYPES, 8)
    first = await found(pool, Signup("ana@c.test", "clinica", "Ana", hashed), codes)
    second = await found(pool, Signup("ana@c.test", "clinica-sur", "Ana", hashed), codes)
    assert await quotas_of(pool, first.org.id, "sandbox") == trial
    assert await quotas_of(pool, second.org.id, "sandbox") == closed


@postgres
async def test_the_same_person_gets_a_key_for_another_device_that_dies_with_the_asking_one(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    member = await seated(pool, org)
    signed = await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))
    paired = await another_key(pool, signed.key, member, device="berna-mbp")
    assert (paired.key.subject, paired.key.label) == (member.id, "berna-mbp")
    with pytest.raises(NotAllowed, match="a server's token names nobody"):
        await another_key(pool, Key("k_s", org.id), None)
    await update(pool, org.id, member.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="no longer an active member"):
        await another_key(pool, signed.key, await _disabled(pool, org, member))


async def _disabled(pool: Pool, org: Org, member: Member) -> Member:
    return await update(pool, org.id, member.id, Change(status="disabled"))


@postgres
async def test_a_person_opens_another_org_of_theirs_and_an_operator_opens_any(pool: Pool) -> None:
    home = await org_of(pool)
    other = await org_of(pool, "bidfire")
    stranger = await org_of(pool, "acme")
    member = await seated(pool, home)
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
    pool: Pool, monkeypatch: pytest.MonkeyPatch, connections: Connections
) -> None:
    postbox = Postbox()
    monkeypatch.setattr(MailServer, "postbox", postbox)
    monkeypatch.setattr("smtplib.SMTP", MailServer)
    org = await org_of(pool)
    await seated(pool, org)
    box = Mailbox("smtp.box.test", 587, "starttls", "u", "p", "Box <no-reply@box.test>")
    outbox = Outbox(replace(connections, vault=VAULT), box)
    await forgotten(pool, outbox, "ana@clinica.test", "https://box.test")
    await forgotten(pool, outbox, "nobody@clinica.test", "https://box.test")
    await outbox.drained()
    (letter,) = postbox.sent
    assert letter["To"] == "ana@clinica.test"
    assert "https://box.test/invitations/inv_" in letter.as_string()


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


async def test_the_authorization_url_merges_its_query_into_one_the_endpoint_already_carries() -> (
    None
):
    _, http = idp_of()
    provider = await discovered(http, "https://idp.test")
    begun = begun_signin(org="org_1")
    url = httpx.URL(authorization_url(provider, CLIENT, begun))
    assert url.params["prompt"] == "select_account"
    assert url.params["state"] == begun.state
    assert url.params["code_challenge_method"] == "S256"
    assert begun.verifier not in str(url)


async def test_an_exchange_refused_or_answered_with_no_json_is_a_refusal_and_not_a_crash() -> None:
    _, http = idp_of(IdentityProvider(refusal=(400, '{"error_description": "code used already"}')))
    with pytest.raises(NotSignedIn, match="code used already"):
        await vouched_for(http, CLIENT, begun_signin(), "the-code")
    _, http = idp_of(IdentityProvider(refusal=(200, "not json")))
    with pytest.raises((NotSignedIn, UpstreamFailed)):
        await vouched_for(http, CLIENT, begun_signin(), "the-code")


async def claims_of(idp: IdentityProvider, email: str) -> Claims:
    begun = begun_signin()
    name = "Ana García" if email.startswith("ana@") else None
    idp.id_token = idp.signed(nonce=begun.nonce, email=email, sub=f"sub-{email}", name=name)
    async with httpx.AsyncClient(transport=idp.transport()) as http:
        return await vouched_for(http, CLIENT, begun, "c")
