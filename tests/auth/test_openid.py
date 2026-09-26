"""What the box will knock at on an IdP's word, and which id_token it takes as its own."""

from __future__ import annotations

import re
import time
from typing import Any, override

import httpx
import jwt
import pytest

from pinecall.auth.openid import (
    NOT_ITS_OWN_TOKEN,
    NOT_REACHED,
    OpenIdRefused,
    Provider,
    authorization_url,
    claims,
    configuration,
    exchange,
    reachable,
)
from tests.api.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, KID, FakeIdp, signing_key

pytestmark = pytest.mark.unit

A_NONCE = "nonce-1"


@pytest.mark.parametrize(
    "url",
    [
        "http://idp.test/token",
        "https://127.0.0.1/token",
        "https://[::1]/token",
        "https://10.0.0.7:8080/token",
        "https://localhost/token",
        "https://postgres.internal/token",
        "https://box.local/token",
        "https://metadata.home/token",
        "not a url",
    ],
)
def test_an_endpoint_the_box_will_not_knock_at_is_refused_by_field(url: str) -> None:
    with pytest.raises(
        OpenIdRefused,
        match=re.escape(NOT_REACHED.format(issuer="x", field="token_endpoint", url=url)),
    ):
        reachable(url, issuer="x", field="token_endpoint")


@pytest.mark.parametrize(
    "url",
    ["https://accounts.google.com", "https://oauth2.googleapis.com/token", "https://idp.test/jwks"],
)
def test_an_https_endpoint_at_a_public_name_is_the_url_itself(url: str) -> None:
    assert reachable(url, issuer="x", field="jwks_uri") == url


class _TellsOfAnInternalHost(FakeIdp):
    """An IdP whose published configuration points the token door at the box's own network."""

    @override
    def _configuration(self) -> dict[str, Any]:
        return {**super()._configuration(), "token_endpoint": "https://10.0.0.7/token"}


async def test_a_configuration_naming_a_private_endpoint_is_refused_naming_the_field() -> None:
    async with httpx.AsyncClient(transport=_TellsOfAnInternalHost().transport()) as http:
        with pytest.raises(
            OpenIdRefused, match=re.escape("token_endpoint is 'https://10.0.0.7/token'")
        ):
            await configuration(http, ISSUER)


async def test_an_issuer_that_is_an_address_is_refused_before_anything_is_fetched() -> None:
    async with httpx.AsyncClient(transport=FakeIdp().transport()) as http:
        with pytest.raises(OpenIdRefused, match=re.escape("issuer is 'https://192.168.1.1'")):
            await configuration(http, "https://192.168.1.1")


def test_the_authorization_url_merges_its_query_into_one_the_endpoint_already_carries() -> None:
    provider = _a_provider(authorization_endpoint=f"{ISSUER}/authorize?tenant=t1")
    sent = httpx.URL(
        authorization_url(
            provider, CLIENT_ID, "https://box/back", state="s", nonce="n", verifier="v"
        )
    )
    assert sent.params["tenant"] == "t1"
    assert sent.params["state"] == "s"
    assert sent.params["code_challenge_method"] == "S256"


class _AnswersHtml(FakeIdp):
    """A token door that answers 200 with a page: a proxy in front of the IdP, say."""

    @override
    def _exchanged(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")


async def test_an_exchange_answered_with_no_json_is_the_idps_refusal_and_not_a_crash() -> None:
    async with httpx.AsyncClient(transport=_AnswersHtml().transport()) as http:
        with pytest.raises(OpenIdRefused, match="without an id_token"):
            await exchange(
                http, _a_provider(), CLIENT_ID, CLIENT_SECRET, "code", "https://box/back", "v"
            )


async def test_an_id_token_for_two_audiences_names_this_client_as_the_authorized_party() -> None:
    async with httpx.AsyncClient(transport=FakeIdp().transport()) as http:
        with pytest.raises(
            OpenIdRefused, match=NOT_ITS_OWN_TOKEN.format(azp=None, client_id=CLIENT_ID)
        ):
            await claims(
                http, _a_provider(), _signed(aud=[CLIENT_ID, "another-client"]), CLIENT_ID, A_NONCE
            )
        with pytest.raises(OpenIdRefused, match="issued to 'another-client'"):
            await claims(
                http,
                _a_provider(),
                _signed(aud=[CLIENT_ID, "another-client"], azp="another-client"),
                CLIENT_ID,
                A_NONCE,
            )
        ours = await claims(
            http,
            _a_provider(),
            _signed(aud=[CLIENT_ID, "another-client"], azp=CLIENT_ID),
            CLIENT_ID,
            A_NONCE,
        )
        assert ours.email == "nico@tiendasur.uy"


async def test_an_id_token_naming_no_key_is_verified_against_the_one_key_published() -> None:
    async with httpx.AsyncClient(transport=FakeIdp().transport()) as http:
        ours = await claims(http, _a_provider(), _signed(kid=None), CLIENT_ID, A_NONCE)
        assert ours.subject == "idp-subject-1"


def _a_provider(authorization_endpoint: str = f"{ISSUER}/authorize") -> Provider:
    return Provider(
        issuer=ISSUER,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
    )


def _signed(kid: str | None = KID, **claimed: Any) -> str:
    """One id_token the fake IdP would vouch for, with whatever a test moves."""
    now = int(time.time())
    said: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-subject-1",
        "iat": now,
        "exp": now + 300,
        "nonce": A_NONCE,
        "email": "nico@tiendasur.uy",
        "email_verified": True,
        **claimed,
    }
    headers = {} if kid is None else {"kid": kid}
    return jwt.encode(said, signing_key(), algorithm="RS256", headers=headers)
