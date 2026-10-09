"""Tests for Meta's side of WhatsApp: the handshake, the signature, a body's messages, a reply."""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from cryptography.fernet import Fernet

from pinecall.channels import whatsapp
from pinecall.channels.whatsapp import Inbound, Meta
from pinecall.domain.call import Route
from pinecall.domain.errors import (
    NotAllowed,
    NotAvailable,
    UpstreamFailed,
)
from pinecall.domain.names import JsonObject
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool, open_pool
from pinecall.process.connections import vault_of
from pinecall.tenancy import carriers, orgs, vault
from pinecall.tenancy.carriers import WhatsappAccount
from tests.channels.conftest import Line
from tests.conftest import DSN, postgres
from tests.fakes.meta import Graph, outside
from tests.fakes.twilio import Twilio
from tests.fakes.webhooks import Receiver

SIGNING = "the app's own secret"
OUR_NUMBER = "+59829001199"


def meta_app() -> Meta:
    """The box's Meta app."""
    return Meta.model_validate(
        {"app_secret": SIGNING, "verify_token": "a word", "access_token": "the box's"}
    )


def signed(body: bytes, secret: str = SIGNING) -> str:
    """The header Meta sends with a body."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def a_body(*messages: dict[str, object], contacts: list[dict[str, object]] | None = None) -> bytes:
    """Meta's envelope around these messages, to the box's number."""
    value: dict[str, object] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": OUR_NUMBER.lstrip("+"), "phone_number_id": "1055"},
        "contacts": contacts
        if contacts is not None
        else [{"wa_id": "59899000001", "profile": {"name": "Ana"}}],
        "messages": list(messages),
    }
    return json.dumps(
        {"entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}
    ).encode()


def a_text(text: str, sender: str = "59899000001", message: str = "wamid.1") -> dict[str, object]:
    """One text message as Meta writes it."""
    return {"from": sender, "id": message, "type": "text", "text": {"body": text}, "timestamp": "1"}


# ── the handshake ──


def test_metas_handshake_is_echoed_back_when_the_word_is_the_right_one() -> None:
    assert whatsapp.handshake(meta_app(), "subscribe", "a word", "1158201444") == "1158201444"


@pytest.mark.parametrize(
    ("mode", "word"), [("subscribe", "another"), ("unsubscribe", "a word"), (None, None)]
)
def test_a_handshake_with_another_word_echoes_nothing(mode: str | None, word: str | None) -> None:
    with pytest.raises(NotAllowed):
        whatsapp.handshake(meta_app(), mode, word, "1158201444")


# ── the signature ──


def test_the_body_meta_signed_is_taken() -> None:
    body = a_body(a_text("hola"))
    assert whatsapp.is_signed(SIGNING, body, signed(body))


def test_no_header_at_all_is_refused() -> None:
    assert not whatsapp.is_signed(SIGNING, b"{}", None)


def test_a_header_without_the_prefix_is_refused() -> None:
    body = a_body(a_text("hola"))
    assert not whatsapp.is_signed(SIGNING, body, signed(body).removeprefix("sha256="))


def test_a_body_signed_with_another_secret_is_refused() -> None:
    body = a_body(a_text("hola"))
    assert not whatsapp.is_signed(SIGNING, body, signed(body, "another secret"))


def test_a_body_changed_after_it_was_signed_is_refused() -> None:
    body = a_body(a_text("hola"))
    assert not whatsapp.is_signed(SIGNING, body.replace(b"hola", b"chau"), signed(body))


# ── the messages in a body ──


def test_one_text_arrives_with_the_door_the_person_and_what_they_wrote() -> None:
    (message,) = whatsapp.messages_in(a_body(a_text("quiero un turno")))
    assert message == Inbound(
        number=OUR_NUMBER,
        phone_number_id="1055",
        wa_id="59899000001",
        name="Ana",
        message_id="wamid.1",
        kind="text",
        text="quiero un turno",
    )
    assert message.caller == "+59899000001"


def test_two_messages_in_one_body_arrive_in_the_order_meta_sent_them() -> None:
    body = a_body(a_text("uno", message="wamid.1"), a_text("dos", message="wamid.2"))
    assert [item.text for item in whatsapp.messages_in(body)] == ["uno", "dos"]


def test_a_delivery_receipt_carries_no_message_and_yields_none() -> None:
    value = {
        "metadata": {"display_phone_number": "598", "phone_number_id": "1"},
        "statuses": [{"id": "wamid.1", "status": "read"}],
    }
    body = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
    assert whatsapp.messages_in(body) == []


def test_an_image_arrives_as_its_own_kind_with_no_text() -> None:
    (image,) = whatsapp.messages_in(
        a_body({"from": "59899000001", "id": "wamid.9", "type": "image", "image": {"id": "m"}})
    )
    assert (image.kind, image.text) == ("image", None)


def test_a_sender_no_contact_row_names_is_still_a_message_and_has_no_name() -> None:
    (message,) = whatsapp.messages_in(a_body(a_text("hola"), contacts=[]))
    assert message.name is None


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"entry": "nope"}'])
def test_a_body_that_is_not_a_message_envelope_at_all_yields_none(body: bytes) -> None:
    assert whatsapp.messages_in(body) == []


# ── a reply ──


async def test_a_reply_goes_from_the_number_written_to_with_no_preview_on_the_token() -> None:
    graph = Graph()
    async with httpx.AsyncClient(transport=outside(Twilio(), graph, Receiver())) as http:
        (message,) = whatsapp.messages_in(a_body(a_text("hola")))
        await whatsapp.send_text(http, "a token", message, "¡Hola Ana!")
    (sent,) = graph.sent
    assert sent["from"] == "1055"
    assert (sent["to"], sent["text"]) == (
        "59899000001",
        {"body": "¡Hola Ana!", "preview_url": False},
    )
    assert graph.tokens == ["Bearer a token"]


async def test_a_reply_meta_refuses_is_refused_in_metas_own_words() -> None:
    graph = Graph(refusal=(400, "Message failed to send because more than 24 hours have passed"))
    async with httpx.AsyncClient(transport=outside(Twilio(), graph, Receiver())) as http:
        (message,) = whatsapp.messages_in(a_body(a_text("hola")))
        with pytest.raises(UpstreamFailed, match="more than 24 hours"):
            await whatsapp.send_text(http, "a token", message, "hola")


# ── whose token, and the waiting room ──


async def test_the_number_an_account_answers_at_is_asked_of_meta_in_e164() -> None:
    graph = Graph(number="+1 555-010-0000", name="Clinica Norte")
    async with httpx.AsyncClient(transport=outside(Twilio(), graph, Receiver())) as http:
        assert await whatsapp.display_number(http, "a token", "1055") == (
            "+15550100000",
            "Clinica Norte",
        )
        graph.refusal = (401, "Error validating access token: Session has expired")
        with pytest.raises(UpstreamFailed, match="expired"):
            await whatsapp.display_number(http, "a token", "1055")


@postgres
async def test_a_box_with_no_meta_app_has_its_door_closed(pool: Pool) -> None:
    sealed = vault_of(Fernet.generate_key().decode())
    with pytest.raises(NotAvailable, match="credentials/whatsapp"):
        await whatsapp.box_account(pool, sealed)


@postgres
async def test_a_message_kept_waits_on_the_agents_log_until_it_is_taken(store: Store) -> None:
    logs = Logs(store)
    route = Route(
        org="org_a", agent="recepcion", channel="whatsapp", number=OUR_NUMBER, env="sandbox"
    )
    (first, second) = whatsapp.messages_in(
        a_body(a_text("uno", message="wamid.1"), a_text("dos", message="wamid.2"))
    )
    kept = await whatsapp.kept(logs, route, first)
    await whatsapp.kept(logs, route, second)
    await whatsapp.taken(logs, kept, "call_1")
    (waiting,) = await whatsapp.waiting_in(store)
    assert (waiting.agent, waiting.env, waiting.inbound) == ("recepcion", "sandbox", second)


@postgres
async def test_the_orgs_own_meta_token_answers_and_an_org_with_none_answers_on_the_boxs(
    line: Line, pool: Pool
) -> None:
    other = await orgs.create(pool, "otra", "Otra")
    assert await whatsapp.meta_token_for(pool, line.connections.vault, line.org, "1055") is None
    app: JsonObject = {"app_secret": "s", "verify_token": "w", "access_token": "the box's"}
    await vault.put_box_credentials(pool, line.connections.vault, "whatsapp", app)
    at_meta = WhatsappAccount.model_validate(
        {"phone_number_id": "1055", "access_token": "the org's"}
    )
    await carriers.put_carrier(line.connections.pool, line.connections.vault, line.org, at_meta)
    assert (
        await whatsapp.meta_token_for(pool, line.connections.vault, line.org, "1055") == "the org's"
    )
    assert (
        await whatsapp.meta_token_for(pool, line.connections.vault, other.id, "1055") == "the box's"
    )


# ── a message read once ──

NOW = 1_800_000_000.0


@postgres
async def test_a_message_claimed_is_reading_until_read_then_seen_for_every_later_delivery(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW) == "new"
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + 1) == "reading"
    await whatsapp.read(pool, org, "wamid.1", NOW + 1)
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + 2) == "seen"
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + whatsapp.READING_S + 5) == "seen"


@postgres
async def test_one_orgs_message_id_never_shadows_anothers(pool: Pool) -> None:
    first = (await orgs.create(pool, "clinica", "Clinica")).id
    second = (await orgs.create(pool, "otra", "Otra")).id
    assert await whatsapp.claimed(pool, first, "wamid.1", NOW) == "new"
    assert await whatsapp.claimed(pool, second, "wamid.1", NOW) == "new"


@postgres
async def test_a_claim_released_or_left_unread_past_its_lease_is_read_by_the_next_delivery(
    pool: Pool,
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW) == "new"
    await whatsapp.released(pool, org, "wamid.1")
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + 1) == "new"
    # Nobody reads it or gives it back: the process holding it died.
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + whatsapp.READING_S) == "reading"
    assert await whatsapp.claimed(pool, org, "wamid.1", NOW + whatsapp.READING_S + 2) == "new"


@postgres
async def test_of_two_gateways_delivered_the_same_message_at_once_exactly_one_reads_it(
    pool: Pool, schema: str
) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    other = await open_pool(DSN, schema=schema, max_size=4)
    try:
        claims = await asyncio.gather(
            *(
                whatsapp.claimed(gateway, org, f"wamid.{number}", NOW)
                for number in range(20)
                for gateway in (pool, other)
            )
        )
    finally:
        await other.close()
    assert sorted(claims).count("new") == 20
    assert sorted(claims).count("reading") == 20


@postgres
async def test_the_ids_claimed_past_metas_seven_days_of_retries_are_forgotten(pool: Pool) -> None:
    org = (await orgs.create(pool, "clinica", "Clinica")).id
    await whatsapp.claimed(pool, org, "wamid.old", NOW)
    await whatsapp.claimed(pool, org, "wamid.new", NOW + 60)
    assert await whatsapp.forget_seen(pool, NOW + whatsapp.SEEN_KEPT_S) == 0
    assert await whatsapp.forget_seen(pool, NOW + whatsapp.SEEN_KEPT_S + 30) == 1
    assert await whatsapp.claimed(pool, org, "wamid.new", NOW + 61) == "reading"
    assert await whatsapp.claimed(pool, org, "wamid.old", NOW + 61) == "new"
