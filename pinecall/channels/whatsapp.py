"""WhatsApp's Cloud API: the webhook's handshake and signature, the messages in a body, a reply."""

import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx
from cryptography.fernet import MultiFernet
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pinecall.domain.errors import NotAllowed, NotAvailable, UpstreamFailed
from pinecall.domain.types import Env, Route
from pinecall.log.log import Logs
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import vault
from pinecall.wire.events import MessageTaken, MessageWaiting

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
SIGNED_WITH = "sha256="
SIGNATURE_HEADER = "x-hub-signature-256"
TIMEOUT_S = 10.0
# A conversation quiet this long is over: the next message opens a new call. It is under
# Meta's 24 h customer-service window, which only the idle close keeps.
IDLE_S = 2 * 60 * 60
# Meta lets a business write freely only this long after the contact's last message
# (developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages).
WINDOW_S = 24 * 60 * 60
# The box's Meta app, sealed in box_settings as credentials/whatsapp.
THE_BOXS = "whatsapp"
WAITING = "message.waiting"
TAKEN = "message.taken"

NO_META = "this box has no Meta app (credentials/whatsapp): its WhatsApp door is closed"
NOT_THE_WORD = "the verify token is not this box's"
META_SAID = "WhatsApp: {said}"


class Meta(BaseModel):
    """The box's Meta app: the secret every body is signed with, the handshake word, a token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    app_secret: str = Field(min_length=1)
    verify_token: str = Field(min_length=1)
    # Replies for an org that brought no token of its own.
    access_token: str | None = None


async def the_boxs(pool: Pool, sealed: MultiFernet) -> Meta:
    """The box's Meta app; NotAvailable when it has none."""
    held = (await vault.box_credentials(pool, sealed)).get(THE_BOXS)
    try:
        return Meta.model_validate(held)
    except ValidationError:
        raise NotAvailable(NO_META) from None


# Meta keeps the subscription only when the challenge comes back as it was sent, as plain text.
def handshake(meta: Meta, mode: str | None, word: str | None, challenge: str | None) -> str:
    """The challenge to echo back, when Meta subscribes with this box's word."""
    if mode != "subscribe" or word is None or challenge is None:
        raise NotAllowed(NOT_THE_WORD)
    if not hmac.compare_digest(word.encode(), meta.verify_token.encode()):
        raise NotAllowed(NOT_THE_WORD)
    return challenge


# The raw bytes, before anything parses them: a body re-serialised signs differently.
def is_signed(app_secret: str, body: bytes, header: str | None) -> bool:
    """Whether the body carries the app's HMAC-SHA256 signature."""
    if header is None or not header.startswith(SIGNED_WITH):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix(SIGNED_WITH), expected)


@dataclass(frozen=True)
class Inbound:
    """One message to one of the box's WhatsApp numbers."""

    number: str
    phone_number_id: str
    wa_id: str
    name: str | None
    message_id: str
    kind: str
    text: str | None

    @property
    def caller(self) -> str:
        """The sender in E.164: WhatsApp names them by digits alone."""
        return f"+{self.wa_id}"


# Meta adds fields without notice, and a webhook answered 4xx for long is disabled: every
# model ignores what it does not know.
class _Lenient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class _Text(_Lenient):
    body: str | None = None


class _Message(_Lenient):
    id: str = ""
    sender: str = Field("", alias="from")
    type: str = ""
    text: _Text | None = None


class _Profile(_Lenient):
    name: str | None = None


class _Contact(_Lenient):
    wa_id: str = ""
    profile: _Profile | None = None


class _Metadata(_Lenient):
    display_phone_number: str = ""
    phone_number_id: str = ""


# A delivery receipt arrives in this shape, with `statuses` and no messages.
class _Value(_Lenient):
    metadata: _Metadata | None = None
    contacts: list[_Contact] = Field(default_factory=list[_Contact])
    messages: list[_Message] = Field(default_factory=list[_Message])


class _Change(_Lenient):
    value: _Value | None = None


class _Entry(_Lenient):
    changes: list[_Change] = Field(default_factory=list[_Change])


class _Body(_Lenient):
    entry: list[_Entry] = Field(default_factory=list[_Entry])


def messages_in(body: bytes) -> list[Inbound]:
    """Every message in Meta's body, in Meta's order; none in a receipt or a stranger's body."""
    try:
        said = _Body.model_validate_json(body)
    except ValidationError:
        return []
    found: list[Inbound] = []
    for change in (one for entry in said.entry for one in entry.changes):
        value = change.value
        if value is None or value.metadata is None:
            continue
        names = {one.wa_id: one.profile.name for one in value.contacts if one.profile}
        found += [
            Inbound(
                number=f"+{value.metadata.display_phone_number.lstrip('+')}",
                phone_number_id=value.metadata.phone_number_id,
                wa_id=message.sender,
                name=names.get(message.sender),
                message_id=message.id,
                kind=message.type,
                text=None if message.text is None else message.text.body,
            )
            for message in value.messages
        ]
    return found


class _Refused(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str = ""


class _Refusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    error: _Refused | None = None


class _Number(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    display_phone_number: str
    verified_name: str = ""


async def display_number(
    http: httpx.AsyncClient, token: str, phone_number_id: str
) -> tuple[str, str]:
    """The number a WhatsApp account answers at, in E.164, and its name at Meta."""
    answer = await http.get(
        f"{GRAPH}/{phone_number_id}",
        params={"fields": "display_phone_number,verified_name"},
        headers={"authorization": f"Bearer {token}"},
        timeout=TIMEOUT_S,
    )
    if answer.is_error:
        raise UpstreamFailed(META_SAID.format(said=_refused_by(answer)))
    said = _Number.model_validate_json(answer.content)
    number = "+" + "".join(one for one in said.display_phone_number if one.isdigit())
    return number, said.verified_name


async def send_text(http: httpx.AsyncClient, token: str, inbound: Inbound, text: str) -> None:
    """Send the text to the contact from the number they wrote to; Meta's refusal in its words."""
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": inbound.wa_id,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }
    answer = await http.post(
        f"{GRAPH}/{inbound.phone_number_id}/messages",
        json=body,
        headers={"authorization": f"Bearer {token}"},
        timeout=TIMEOUT_S,
    )
    if answer.is_error:
        raise UpstreamFailed(META_SAID.format(said=_refused_by(answer)))


def _refused_by(answer: httpx.Response) -> str:
    try:
        refused = _Refusal.model_validate_json(answer.content).error
    except ValidationError:
        refused = None
    if refused is not None and refused.message:
        return refused.message
    return str(answer.status_code)


# ── the waiting room: the agent's log is the queue ──


@dataclass(frozen=True)
class Waiting:
    """A message kept for an agent nobody held when it arrived."""

    agent: str
    env: Env
    inbound: Inbound
    received_at: float


async def kept(logs: Logs, route: Route, inbound: Inbound) -> Waiting:
    """Keep the message on the agent's log until somebody holds the agent."""
    now = logs.store.clock()
    said = MessageWaiting.model_validate(
        {
            "channel": "whatsapp",
            "env": route.env,
            "number": inbound.number,
            "phone_number_id": inbound.phone_number_id,
            "from": inbound.wa_id,
            "name": inbound.name,
            "message_id": inbound.message_id,
            "text": inbound.text or "",
            "received_at": now,
        }
    )
    await logs.agent(route.agent).append(WAITING, said.written())
    return Waiting(route.agent, route.env, inbound, now)


async def taken(logs: Logs, waiting: Waiting, call: str | None) -> None:
    """The message off the queue: the call it went on, or none when its window closed."""
    said = MessageTaken(message_id=waiting.inbound.message_id, call=call)
    await logs.agent(waiting.agent).append(TAKEN, said.written())


async def waiting_in(store: Store) -> list[Waiting]:
    """Every message still waiting, oldest first, read off every agent's log."""
    waiting: dict[str, Waiting] = {}
    after = 0
    while page := await store.across([WAITING, TAKEN], after=after):
        for row in page:
            entry = row.entry
            if entry.type == TAKEN:
                waiting.pop(str(entry.data.get("message_id")), None)
                continue
            said = MessageWaiting.model_validate(entry.data)
            inbound = Inbound(
                number=said.number,
                phone_number_id=said.phone_number_id,
                wa_id=said.from_,
                name=said.name,
                message_id=said.message_id,
                kind="text",
                text=said.text,
            )
            waiting[said.message_id] = Waiting(entry.agent, said.env, inbound, said.received_at)
        after = page[-1].position
    return list(waiting.values())
