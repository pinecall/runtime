"""Tests for Meta's webhook door: the handshake, the signature, what it answers past it."""

import hashlib
import hmac
import json

import httpx

from pinecall.domain.types import JsonObject
from pinecall.tenancy import vault
from tests.conftest import Knocking, postgres

SIGNING = "the app's own signing word"


async def the_boxs_meta(knocking: Knocking) -> None:
    """The box's Meta app."""
    meta: JsonObject = {"app_secret": SIGNING, "verify_token": "a word"}
    await vault.put_box_credentials(knocking.box.pool, knocking.box.vault, "whatsapp", meta)


def signature(body: bytes, secret: str = SIGNING) -> dict[str, str]:
    """The header Meta signs a body with."""
    signed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"x-hub-signature-256": f"sha256={signed}"}


@postgres
async def test_metas_handshake_is_echoed_as_plain_text_and_another_word_is_403(
    gateway: Knocking,
) -> None:
    await the_boxs_meta(gateway)
    asked = {"hub.mode": "subscribe", "hub.verify_token": "a word", "hub.challenge": "1158201444"}
    async with httpx.AsyncClient(base_url=gateway.url) as meta:
        echoed = await meta.get("/v1/whatsapp/webhook", params=asked)
        wrong = await meta.get(
            "/v1/whatsapp/webhook", params={**asked, "hub.verify_token": "another"}
        )
    assert (echoed.status_code, echoed.text) == (200, "1158201444")
    assert echoed.headers["content-type"].startswith("text/plain")
    assert wrong.status_code == 403


@postgres
async def test_a_box_with_no_meta_app_answers_503(gateway: Knocking) -> None:
    async with httpx.AsyncClient(base_url=gateway.url) as meta:
        answer = await meta.post("/v1/whatsapp/webhook", content=b"{}")
    assert answer.status_code == 503


@postgres
async def test_an_unsigned_body_and_one_signed_by_another_are_403_and_open_nothing(
    gateway: Knocking,
) -> None:
    await the_boxs_meta(gateway)
    body = b'{"entry": []}'
    async with httpx.AsyncClient(base_url=gateway.url) as meta:
        unsigned = await meta.post("/v1/whatsapp/webhook", content=body)
        stranger = await meta.post(
            "/v1/whatsapp/webhook", content=body, headers=signature(body, "another")
        )
    assert (unsigned.status_code, stranger.status_code) == (403, 403)
    assert gateway.box.threads.open == {}


@postgres
async def test_a_receipt_and_a_number_nobody_routed_are_200_and_open_nothing(
    gateway: Knocking,
) -> None:
    await the_boxs_meta(gateway)
    receipt = {"metadata": {"display_phone_number": "59829001199", "phone_number_id": "1"}}
    message = {
        **receipt,
        "messages": [{"from": "598990", "id": "wamid.1", "type": "text", "text": {"body": "hola"}}],
    }
    async with httpx.AsyncClient(base_url=gateway.url) as meta:
        answers = [
            await meta.post("/v1/whatsapp/webhook", content=body, headers=signature(body))
            for body in (
                json.dumps({"entry": [{"changes": [{"value": receipt}]}]}).encode(),
                json.dumps({"entry": [{"changes": [{"value": message}]}]}).encode(),
            )
        ]
    assert [(one.status_code, one.json()) for one in answers] == [
        (200, {"received": 0}),
        (200, {"received": 1}),
    ]
    assert gateway.box.threads.open == {}
    assert gateway.box.threads.waiting == []
