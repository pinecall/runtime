"""One gateway that answers WhatsApp: the two secrets Meta's door needs, and a scripted Meta."""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api import _deps as deps
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.api.whatsapp.threads import Thread, Threads
from pinecall.routes.table import Routes
from pinecall.types import PRODUCTION, Route
from pinecall.whatsapp.signing import SIGNATURE_HEADER
from pinecall_protocol import defs
from tests.api.conftest import (
    A_KEY,
    A_RECORD,
    A_VAULT_KEY,
    AGENT,
    AN_OPS_KEY,
    over_the_asgi_app,
)

# The Meta app's two words, as a ring-0 box holds them. Neither reaches a network.
AN_APP_SECRET = "an-app-secret-nobody-will-ever-register"
A_VERIFY_TOKEN = "a-verify-token-nobody-will-ever-type"

# The box's own Meta token, so an org that brought none still answers.
THE_BOXES_TOKEN = "the-boxes-own-whatsapp-token"

# The number the clinic answers at, and the person writing to it, in the two forms Meta uses: the
# door is E.164, a wa_id is the same digits with no plus.
THE_CLINICS_NUMBER = "+34910000000"
THE_PHONE_NUMBER_ID = "106540352242922"
ANA = "34600000001"
SOMEBODY_ELSE = "34600000002"

WEBHOOK = "/v1/whatsapp/webhook"


@pytest.fixture
def settings() -> Settings:
    """The environment of a box that answers WhatsApp: the app secret, the word, and a token."""
    return Settings(
        ops_key=AN_OPS_KEY,
        vault_key=A_VAULT_KEY,
        whatsapp_app_secret=AN_APP_SECRET,
        whatsapp_verify_token=A_VERIFY_TOKEN,
        whatsapp_access_token=THE_BOXES_TOKEN,
    )


# Two hours, for a suite whose every test finishes in milliseconds: long enough that no test ever
# times a thread out by accident, however loaded the machine running it is. The one test that IS
# about the idle close parametrises `idle_seconds` down to AN_INSTANT.
NEVER_IN_THIS_SUITE = 30.0

# What "two hours of silence" is, when the whole file has ten seconds.
AN_INSTANT = 0.05


@pytest.fixture
def idle_seconds() -> float:
    """How long a thread of this test may go unspoken to before it closes itself."""
    return NEVER_IN_THIS_SUITE


@pytest.fixture
def threads(idle_seconds: float) -> Threads:
    """The conversations open here, none at the start of a test and none inherited."""
    return Threads(idle_seconds=idle_seconds)


# Meta's client, and the whole reason this file does not use the TestClient: httpx's ASGI
# transport runs the real app in the TEST's own loop, so a thread's queue and the test that waits
# on it are the same loop. The Authorization header is ignored here — Meta sends none, and the
# signature IS this door's authentication — and is only what lets the same helper build it.
@pytest.fixture
async def meta(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """A client on the real ASGI app, knocking at the WhatsApp webhook the way Meta does."""
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
def a_box_with_no_token(meta: httpx.AsyncClient) -> None:  # noqa: ARG001
    """A box that answers WhatsApp and has nothing to answer WITH: the two are separate secrets."""
    without = Settings(whatsapp_app_secret=AN_APP_SECRET, whatsapp_verify_token=A_VERIFY_TOKEN)
    app.dependency_overrides[deps.a_settings] = lambda: without


def a_body(*messages: dict[str, Any], number: str = THE_CLINICS_NUMBER) -> dict[str, Any]:
    """Meta's envelope around whatever this test wants delivered at the clinic's number."""
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": number.removeprefix("+"),
            "phone_number_id": THE_PHONE_NUMBER_ID,
        },
        "contacts": [{"profile": {"name": "Ana García"}, "wa_id": ANA}],
        "messages": list(messages),
    }
    return _an_envelope(value)


def a_text(said: str, wa_id: str = ANA, id: str = "wamid.one") -> dict[str, Any]:
    """One text message, as Meta delivers it."""
    return {
        "from": wa_id,
        "id": id,
        "timestamp": "1757400000",
        "type": "text",
        "text": {"body": said},
    }


def a_picture(wa_id: str = ANA) -> dict[str, Any]:
    """One message this door does not read: it is acknowledged, and it opens nothing."""
    return {"from": wa_id, "id": "wamid.pic", "type": "image", "image": {"id": "media_1"}}


def a_delivery_receipt() -> dict[str, Any]:
    """The other envelope Meta sends down the very same webhook: a status, and no message."""
    return _an_envelope(
        {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": THE_CLINICS_NUMBER.removeprefix("+"),
                "phone_number_id": THE_PHONE_NUMBER_ID,
            },
            "statuses": [{"id": "wamid.one", "status": "delivered", "recipient_id": ANA}],
        }
    )


def _an_envelope(value: dict[str, Any]) -> dict[str, Any]:
    """Every body Meta posts has this shape around it, whatever the change turns out to be."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "102290129340398", "changes": [{"field": "messages", "value": value}]}],
    }


def a_signature(body: bytes, secret: str = AN_APP_SECRET) -> dict[str, str]:
    """The header Meta sends, computed here the way Meta computes it: over the raw bytes."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: f"sha256={digest}"}


# ── what every test of this door does before it can say anything ────────────────


# A register needs a socket id and no websocket: nothing in this suite opens one, because httpx's
# ASGI transport speaks no WebSocket and the point of using it is the single event loop.
AN_APP = "app_holding_the_clinic"


async def the_clinic_answers_at_the_number(
    registry: Registry, routes: Routes, typed: bool = True, declared: bool = False
) -> None:
    """The clinic held by an app socket, reachable at the number Meta will deliver to."""
    door = defs.Route(channel="whatsapp", number=THE_CLINICS_NUMBER if declared else None)
    await registry.register(
        AN_APP,
        A_RECORD.org,
        PRODUCTION,
        AGENT,
        [door if declared else defs.Route(channel="web", number=None)],
    )
    if typed:
        await routes.put(the_operators_row(AGENT))


def the_operators_row(agent: str) -> Route:
    """The operator's own row at the clinic's number, for whichever agent a test moves it to."""
    return Route(org=A_RECORD.org, agent=agent, channel="whatsapp", number=THE_CLINICS_NUMBER)


async def delivered(meta: httpx.AsyncClient, body: dict[str, Any]) -> Any:
    """One body at the webhook, signed over exactly the bytes that are posted."""
    # The bytes are built HERE and posted as content: letting httpx re-encode the dict would sign
    # one body and send another, and every signature this door is sent would fail.
    raw = json.dumps(body).encode()
    answer = await meta.post(WEBHOOK, content=raw, headers=a_signature(raw))
    assert answer.status_code == 200, answer.text
    return answer.json()


async def quiet(threads: Threads, wa_id: str = ANA) -> Thread:
    """The thread, once everything delivered on it is answered and no reply is in flight."""
    thread = threads.of(THE_CLINICS_NUMBER, wa_id)
    assert thread is not None, "no thread was opened for that contact"
    await thread.idle()
    return thread
