"""What every door test knocks with: a call of the org's agent, and an app socket holding it."""

import smtplib
from collections.abc import AsyncIterator
from datetime import date
from email.message import Message

import pytest
from websockets.asyncio.client import ClientConnection

from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.names import Env, JsonObject
from pinecall.tenancy import mail, people
from tests.conftest import AGENT, Knocking, a_developer, received_until, sent
from tests.fakes.mail import MailServer, Postbox

A_NUMBER = "+59829001199"
THE_CALLER = "+59899123456"
HER_PHONE = "+59899000001"
THE_BOXS_SENDER = "Pinecall <no-reply@box.test>"


def a_call(knocking: Knocking, *, env: Env = "sandbox", channel: str = "phone") -> CallContext:
    """A call of the org's agent that rang at its number."""
    return CallContext(
        call=new_call_id(),
        channel="phone" if channel == "phone" else "web",
        direction="inbound",
        caller=THE_CALLER,
        route=Route(
            org=knocking.org.id,
            agent=AGENT,
            channel="phone" if channel == "phone" else "web",
            number=A_NUMBER if channel == "phone" else None,
            env=env,
        ),
        today=date(2026, 9, 28),
    )


async def an_app(
    knocking: Knocking,
    key: str | None = None,
    *,
    env: Env = "sandbox",
    console: bool = False,
    answers_dev: bool = False,
) -> ClientConnection:
    """An app socket holding the agent in the world of the key, the org's own unless given."""
    socket = await knocking.socket("/v1/apps", key or knocking.app[env])
    wanted: JsonObject = {"routes": [], "takes_unclaimed": not console, "answers_dev": answers_dev}
    await sent(socket, "agent.register", wanted)
    await received_until(socket, "agent.registered")
    return socket


async def a_companion(knocking: Knocking) -> ClientConnection:
    """What `pinecall start` holds beside the class: no unclaimed call, every dev verb."""
    return await an_app(knocking, console=True, answers_dev=True)


async def bound_to(knocking: Knocking, email: str, agents: frozenset[str]) -> str:
    """A developer's key whose member row names these agents."""
    member, key = await a_developer(knocking, email)
    await people.update(
        knocking.gateway.connections.pool, knocking.org.id, member, people.Change(agents=agents)
    )
    return key


async def first_data(lines: AsyncIterator[str]) -> str:
    """The first `data:` line of an SSE stream, stripped."""
    async for line in lines:
        if line.startswith("data:"):
            return line[5:].strip()
    return ""


@pytest.fixture
def postbox(monkeypatch: pytest.MonkeyPatch) -> Postbox:
    """Every mail server the gateway reaches, answering into one postbox."""
    kept = Postbox()
    monkeypatch.setattr(MailServer, "postbox", kept)
    monkeypatch.setattr(smtplib, "SMTP", MailServer)
    monkeypatch.setattr(smtplib, "SMTP_SSL", MailServer)
    return kept


async def box_can_mail(knocking: Knocking) -> None:
    """The box's own mailbox, stored as the operator stores it."""
    connections = knocking.gateway.connections
    mailbox = mail.Mailbox("smtp.box.test", 587, "starttls", "box", "box-pass", THE_BOXS_SENDER)
    await mail.put_box_mail(connections.pool, connections.vault, mailbox)


async def delivered(knocking: Knocking, postbox: Postbox) -> list[str]:
    """The addresses of every letter the gateway sent, once its outbox is empty."""
    await knocking.gateway.outbox.drained()
    return [str(letter["To"]) for letter in postbox.sent]


def text_of(letter: Message) -> str:
    """The plain-text half of a letter."""
    for part in letter.walk():
        payload = part.get_payload(decode=True)
        if part.get_content_type() == "text/plain" and isinstance(payload, bytes):
            return payload.decode()
    return ""
