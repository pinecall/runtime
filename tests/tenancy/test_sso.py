"""Tests for sign-in with the org's identity provider."""

import socket
import time

import httpx
import pytest

from pinecall.domain.errors import (
    DeclarationRefused,
    NotAllowed,
    NotSignedIn,
    QuotaExhausted,
    UpstreamFailed,
)
from pinecall.domain.org import Quotas
from pinecall.postgres.pool import Pool
from pinecall.tenancy.admission import set_quotas
from pinecall.tenancy.people import (
    Change,
    invite,
    update,
)
from pinecall.tenancy.signin import (
    Asking,
    sign_in_with_password,
)
from pinecall.tenancy.sso import (
    Client,
    OrgSso,
    discovered,
    drop_sso,
    put_sso,
    seat_vouched,
    sso_of,
    sso_with_domain,
    vouched_for,
)
from tests.conftest import postgres
from tests.fakes.idp import IdentityProvider
from tests.tenancy.test_signin import (
    ANA,
    CLIENT,
    VAULT,
    WHAT_ANA_TYPES,
    begun_signin,
    claims_of,
    idp_of,
    org_of,
    seated,
)


@postgres
async def test_an_org_that_signs_in_with_its_provider_refuses_the_password_after_it_matched(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    await seated(pool, org)
    await put_sso(pool, VAULT, OrgSso(org.id, CLIENT, ("clinica.test",), required=True))
    with pytest.raises(NotAllowed, match=r"clinica-norte signs in with its identity provider"):
        await sign_in_with_password(pool, Asking("ana@clinica.test", WHAT_ANA_TYPES))


@postgres
async def test_an_orgs_provider_round_trips_sealed_and_is_found_by_domain(pool: Pool) -> None:
    org = await org_of(pool)
    sso = OrgSso(org.id, CLIENT, ("clinica.test", "clinica.uy"), role="qa")
    await put_sso(pool, VAULT, sso)
    assert await sso_of(pool, VAULT, org.id) == sso
    assert [item.org for item in await sso_with_domain(pool, VAULT, "clinica.uy")] == [org.id]
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


async def test_a_configuration_naming_another_issuer_or_no_endpoint_is_refused() -> None:
    idp, http = idp_of(IdentityProvider(issuer="https://other.test"))
    with pytest.raises(UpstreamFailed, match="one of the two is wrong"):
        await discovered(httpx.AsyncClient(transport=idp.transport()), "https://idp.test")
    await http.aclose()
    silent = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    with pytest.raises(UpstreamFailed, match="not an OpenID provider"):
        await discovered(silent, "https://idp.test")


async def test_a_public_name_that_resolves_inside_is_refused_like_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def inside(*_asked: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", inside)
    silent = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    with pytest.raises(NotAllowed, match="never an address"):
        await discovered(silent, "https://sso.clinica.test")
    with pytest.raises(NotAllowed, match="never an address"):
        await discovered(silent, "https://keycloak.auth.svc")


async def test_a_signed_id_token_with_this_sign_ins_nonce_vouches_for_a_verified_address() -> None:
    idp, http = idp_of()
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce)
    text = await vouched_for(http, CLIENT, begun, "the-code")
    assert (text.email, text.name, text.email_verified) == ("ana@clinica.test", "Ana García", True)
    assert idp.exchanged[0]["code_verifier"] == begun.verifier


async def test_an_id_token_for_another_sign_in_or_an_unverified_address_is_refused() -> None:
    idp, http = idp_of()
    idp.id_token = idp.signed(nonce="somebody-elses")
    with pytest.raises(NotSignedIn, match="different sign-in"):
        await vouched_for(http, CLIENT, begun_signin(), "c")
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce, email_verified=False)
    with pytest.raises(NotSignedIn, match="has not verified"):
        await vouched_for(http, CLIENT, begun, "c")


async def test_an_id_token_that_does_not_verify_is_refused_as_a_sign_in_not_a_failure() -> None:
    idp, http = idp_of()
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce, exp=int(time.time()) - 60)
    with pytest.raises(NotSignedIn, match="did not check out: Signature has expired"):
        await vouched_for(http, CLIENT, begun, "c")
    idp.id_token = idp.signed(nonce=begun.nonce, aud="another-client")
    with pytest.raises(NotSignedIn, match="did not check out"):
        await vouched_for(http, CLIENT, begun, "c")


async def test_an_id_token_for_two_audiences_names_this_client_as_the_authorized_party() -> None:
    idp, http = idp_of()
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce, aud=["the-client", "another"])
    with pytest.raises(NotSignedIn, match="issued to None"):
        await vouched_for(http, CLIENT, begun, "c")
    idp.id_token = idp.signed(nonce=begun.nonce, aud=["the-client", "another"], azp="the-client")
    assert (await vouched_for(http, CLIENT, begun, "c")).subject == "sub-ana"


async def test_an_id_token_naming_no_key_is_verified_against_the_one_key_published() -> None:
    idp, http = idp_of(IdentityProvider(kid=None))
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce)
    assert (await vouched_for(http, CLIENT, begun, "c")).email == "ana@clinica.test"


async def test_a_provider_that_takes_only_basic_auth_is_signed_in_to_that_way() -> None:
    idp, http = idp_of(IdentityProvider(basic_only=True))
    begun = begun_signin()
    idp.id_token = idp.signed(nonce=begun.nonce)
    await vouched_for(http, CLIENT, begun, "c")
    assert "client_secret" not in idp.exchanged[0]


@postgres
async def test_a_provider_seats_a_member_invited_and_makes_one_uninvited_when_told_to(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    idp, _ = idp_of()
    said_ana = await claims_of(idp, "ana@clinica.test")
    options = OrgSso(org.id, CLIENT, ("clinica.test",), role="qa")
    await invite(pool, org.id, ANA, seats=None)
    seated_ana = await seat_vouched(pool, org, options, said_ana)
    assert (seated_ana.status, seated_ana.verified, seated_ana.role) == (
        "active",
        True,
        "developer",
    )
    seated_bo = await seat_vouched(pool, org, options, await claims_of(idp, "bo@clinica.test"))
    assert (seated_bo.role, seated_bo.name) == ("qa", "bo")
    untold = OrgSso(org.id, CLIENT, ("clinica.test",))
    with pytest.raises(NotAllowed, match="seats nobody it was not told to"):
        await seat_vouched(pool, org, untold, await claims_of(idp, "cy@clinica.test"))
    await update(pool, org.id, seated_bo.id, Change(status="disabled"))
    with pytest.raises(NotAllowed, match="disabled"):
        await seat_vouched(pool, org, options, await claims_of(idp, "bo@clinica.test"))


@postgres
async def test_a_person_the_provider_seats_unasked_takes_a_seat_like_an_invited_one(
    pool: Pool,
) -> None:
    org = await org_of(pool)
    await set_quotas(pool, org.id, "production", Quotas(seats=1))
    await seated(pool, org)
    idp, _ = idp_of()
    options = OrgSso(org.id, CLIENT, ("clinica.test",), role="qa")
    with pytest.raises(QuotaExhausted, match="seats"):
        await seat_vouched(pool, org, options, await claims_of(idp, "bo@clinica.test"))
