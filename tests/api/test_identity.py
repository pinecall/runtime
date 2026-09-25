"""The doors production is the identity for: on a sandbox they are not there, and say where."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.identity import SIGN_IN_THERE
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.auth.persons import SANDBOX_PERSONS_KEY_LIFE
from pinecall.types import SANDBOX
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app
from tests.api.talking import answering_in
from tests.conftest import THE_IDENTITY

pytestmark = pytest.mark.unit

# Every door a person is made, proved or handed a key by a password at: production's alone.
PRODUCTIONS_DOORS: list[tuple[str, str, dict[str, str] | None]] = [
    ("POST", "/v1/login", {"email": "berna@clinica.uy", "password": "correct horse"}),
    ("POST", "/v1/login/orgs", {"email": "berna@clinica.uy", "password": "correct horse"}),
    ("POST", "/v1/login/redeem", {"code": "lc_x"}),
    ("POST", "/v1/login/pairings", {}),
    ("GET", "/v1/login/pairings/pc_x", None),
    ("POST", "/v1/login/pairings/pc_x", {}),
    ("GET", "/v1/login/pairings/pc_x/key", None),
    ("POST", "/v1/signup", {"org": "tienda", "email": "a@b.co", "person": "A", "password": "x"}),
    ("GET", "/v1/login/google", None),
    ("GET", "/v1/login/google/callback?code=c&state=s", None),
    ("GET", "/v1/login/sso?org=clinica", None),
    ("GET", "/v1/login/sso/callback?code=c&state=s", None),
    ("POST", "/v1/login/sso/discover", {"email": "berna@clinica.uy"}),
    ("POST", "/v1/login/reset", {"email": "berna@clinica.uy"}),
]

# A person's key minted on the sandbox itself — `pinecall start` there — with a few hours left.
A_SANDBOX_PERSON = "pc_berna_on_the_sandbox"
HOURS_LEFT = datetime.now(UTC) + SANDBOX_PERSONS_KEY_LIFE / 4


@pytest.fixture
def keys() -> MemoryKeys:
    berna = KeyRecord(
        key_id="k_berna",
        org=A_RECORD.org,
        env=SANDBOX,
        subject="m_berna",
        name="Berna",
        expires_at=HOURS_LEFT,
    )
    return MemoryKeys({A_KEY: A_RECORD, A_SANDBOX_PERSON: berna})


@pytest.mark.parametrize(("method", "path", "body"), PRODUCTIONS_DOORS)
async def test_a_door_a_person_is_made_or_proved_at_is_not_on_a_sandbox_and_names_production(
    stranger: httpx.AsyncClient,
    settings: Settings,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    answering_in(SANDBOX, settings)
    answer = await stranger.request(method, path, json=body)
    assert (answer.status_code, answer.json()["detail"]) == (
        404,
        SIGN_IN_THERE.format(identity=THE_IDENTITY),
    )


async def test_a_sandbox_spends_a_code_of_its_own_for_a_key_no_longer_lived_than_its_minters(
    stranger: httpx.AsyncClient, settings: Settings, keys: MemoryKeys
) -> None:
    """`pinecall start` on the sandbox prints ?login=<code>: that door stays, and its key is the
    sandbox's kind — never outliving the key that minted the code."""
    answering_in(SANDBOX, settings)
    async with over_the_asgi_app(f"Bearer {A_SANDBOX_PERSON}") as berna:
        minted = await berna.post("/v1/login/codes")
    signed = await stranger.post("/v1/login", json={"code": minted.json()["code"]})
    assert signed.status_code == 200, signed.text
    record = await keys.verify(signed.json()["key"])
    assert record is not None and record.expires_at == HOURS_LEFT
