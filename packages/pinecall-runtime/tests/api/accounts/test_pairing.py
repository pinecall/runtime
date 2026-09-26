"""`pinecall login`: a word the terminal prints, a key the browser leaves, and one collection."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.auth.keys import PERSONS_PREFIX, KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.pairing import CODE_TTL_S, Pairings
from pinecall.types import PRODUCTION, SANDBOX, Member
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app

pytestmark = pytest.mark.unit

PAIRINGS = "/v1/login/pairings"

ANA = "m_ana"
ANAS_KEY = "pk_test_anas_browser"
A_LAPTOP = "berna-mbp"


@pytest.fixture
def keys() -> MemoryKeys:
    """Ana's browser key, and the org's own — which names nobody and so may sign nobody in."""
    return MemoryKeys(
        {
            ANAS_KEY: KeyRecord(key_id="k_ana", org=A_RECORD.org, env=PRODUCTION, subject=ANA),
            A_KEY: A_RECORD,
        }
    )


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers(
        [
            Member(
                id=ANA,
                org=A_RECORD.org,
                email="ana@clinica.test",
                name="Ana",
                role="developer",
                status="active",
            )
        ]
    )


@pytest.fixture
async def terminal(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """`pinecall login`, which holds no key at all — that is the whole point of the dance."""
    http = over_the_asgi_app("")
    yield http
    await http.aclose()


@pytest.fixture
async def browser(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """The tab Ana is signed into. The password was typed here, never into a terminal."""
    http = over_the_asgi_app(f"Bearer {ANAS_KEY}")
    yield http
    await http.aclose()


async def a_word(terminal: httpx.AsyncClient) -> str:
    opened = await terminal.post(PAIRINGS, json={"device": A_LAPTOP})
    assert opened.status_code == 200
    return str(opened.json()["code"])


async def test_the_terminal_waits_until_the_browser_answers_and_then_gets_a_key(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    code = await a_word(terminal)

    waiting = await terminal.get(f"{PAIRINGS}/{code}/key")
    assert waiting.status_code == 202, "202 and not 404: the browser has simply not answered yet"

    approved = await browser.post(f"{PAIRINGS}/{code}")
    assert approved.status_code == 200
    collected = await terminal.get(f"{PAIRINGS}/{code}/key")

    assert collected.status_code == 200
    assert collected.json()["key"].startswith(PERSONS_PREFIX)


async def test_the_key_the_terminal_gets_is_its_own_and_never_the_browsers(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    """Minted fresh for the same person, so it is revoked on its own from the Keys screen."""
    code = await a_word(terminal)
    await browser.post(f"{PAIRINGS}/{code}")

    key = (await terminal.get(f"{PAIRINGS}/{code}/key")).json()["key"]

    assert key != ANAS_KEY
    record = await keys.verify(key)
    assert record is not None
    assert record.subject == ANA, "the same person"
    assert record.env == SANDBOX, "a terminal is a laptop, and a laptop writes in development"
    assert record.label == A_LAPTOP, "labelled as the terminal, so a revoker knows which"


async def test_the_card_says_which_terminal_it_is_about_to_sign_in(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    """A person approving has to see WHAT they are approving, and that read spends nothing."""
    code = await a_word(terminal)

    asked = await browser.get(f"{PAIRINGS}/{code}")

    assert asked.status_code == 200
    assert asked.json()["device"] == A_LAPTOP
    assert asked.json()["answered"] is False
    assert "key" not in asked.json()
    assert (await terminal.get(f"{PAIRINGS}/{code}/key")).status_code == 202, "still uncollected"


async def test_a_word_is_collected_once_and_is_gone_after(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    code = await a_word(terminal)
    await browser.post(f"{PAIRINGS}/{code}")
    await terminal.get(f"{PAIRINGS}/{code}/key")

    assert (await terminal.get(f"{PAIRINGS}/{code}/key")).status_code == 404


async def test_a_second_approval_is_refused_rather_than_minting_a_key_nobody_collects(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    code = await a_word(terminal)
    await browser.post(f"{PAIRINGS}/{code}")

    again = await browser.post(f"{PAIRINGS}/{code}")

    assert again.status_code == 409


async def test_an_orgs_own_key_signs_nobody_in_because_it_names_nobody(
    terminal: httpx.AsyncClient, tenant_http: httpx.AsyncClient
) -> None:
    code = await a_word(terminal)

    refused = await tenant_http.post(f"{PAIRINGS}/{code}")

    assert refused.status_code == 403
    assert "names nobody" in refused.json()["detail"]


async def test_a_word_nobody_minted_is_the_same_answer_as_one_that_expired(
    terminal: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    assert (await terminal.get(f"{PAIRINGS}/cli_never_minted/key")).status_code == 404
    assert (await browser.post(f"{PAIRINGS}/cli_never_minted")).status_code == 404


def test_a_word_dies_on_its_own_ten_minutes_after_it_was_printed() -> None:
    """Long enough to open a browser and type a password; short enough to be nothing."""
    now = [1_000.0]
    pairings = Pairings(clock=lambda: now[0])
    opened = pairings.open(A_LAPTOP)

    now[0] += CODE_TTL_S + 1

    assert pairings.asking(opened.code) is None
    assert pairings.collect(opened.code) == pairings.collect("cli_never_minted")
