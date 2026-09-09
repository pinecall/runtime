"""One inbound WhatsApp message, as this door reads Meta's envelope: only the fields it uses."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from pinecall.whatsapp.meta import LENIENT

# The Graph API speaks wa_ids — digits, no plus — and the log speaks E.164 everywhere. The one
# conversion between the two lives here, at the door that knows which side it is on.
_PLUS = "+"


# What the rest of the runtime is handed: one message, flat, with the two ids that route it.
@dataclass(frozen=True)
class Inbound:
    """One message a person sent to one of an org's WhatsApp numbers."""

    number: str
    phone_number_id: str
    wa_id: str
    name: str | None
    message_id: str
    kind: str
    text: str | None

    @property
    def caller(self) -> str:
        """The person, as the log names a calling side: their wa_id in E.164."""
        return f"{_PLUS}{self.wa_id}"


class _Text(BaseModel):
    model_config = LENIENT
    body: str | None = None


class _Message(BaseModel):
    model_config = LENIENT
    id: str = ""
    # `from` is a keyword, so the field is named for what it holds and aliased to the wire's word.
    sender: str = Field(default="", alias="from")
    type: str = ""
    text: _Text | None = None


class _Profile(BaseModel):
    model_config = LENIENT
    name: str | None = None


class _Contact(BaseModel):
    model_config = LENIENT
    wa_id: str = ""
    profile: _Profile | None = None


class _Metadata(BaseModel):
    model_config = LENIENT
    display_phone_number: str = ""
    phone_number_id: str = ""


class _Value(BaseModel):
    model_config = LENIENT
    metadata: _Metadata | None = None
    # `statuses` arrives in this very shape and carries no messages: nothing below reads it, so a
    # delivery receipt is an envelope with nothing in it for us.
    contacts: list[_Contact] = Field(default_factory=list[_Contact])
    messages: list[_Message] = Field(default_factory=list[_Message])


class _Change(BaseModel):
    model_config = LENIENT
    value: _Value | None = None


class _Entry(BaseModel):
    model_config = LENIENT
    changes: list[_Change] = Field(default_factory=list[_Change])


class Payload(BaseModel):
    """Meta's webhook envelope: many entries, each with many changes, each with many messages."""

    model_config = LENIENT
    entry: list[_Entry] = Field(default_factory=list[_Entry])


def messages_in(payload: Payload) -> tuple[Inbound, ...]:
    """Every message in this body, in the order Meta sent them. A body with none yields none."""
    return tuple(
        message
        for entry in payload.entry
        for change in entry.changes
        if change.value is not None
        for message in _messages_of(change.value)
    )


# Meta sends the contacts as a list beside the messages rather than on each message, so the name
# is looked up by wa_id: it is whatever WhatsApp shows for that person, never anything the org
# typed. A message whose sender is in no contact row still opens a thread, unnamed.
def _messages_of(value: _Value) -> list[Inbound]:
    """The messages of one change, with the door and the person they arrived from filled in."""
    metadata = value.metadata
    if metadata is None:
        return []
    named = {contact.wa_id: contact for contact in value.contacts}
    return [
        Inbound(
            number=f"{_PLUS}{metadata.display_phone_number}",
            phone_number_id=metadata.phone_number_id,
            wa_id=message.sender,
            name=_name_of(named.get(message.sender)),
            message_id=message.id,
            kind=message.type,
            text=message.text.body if message.text is not None else None,
        )
        for message in value.messages
    ]


def _name_of(contact: _Contact | None) -> str | None:
    """What WhatsApp shows for this person, when the envelope carried a profile for them."""
    if contact is None or contact.profile is None:
        return None
    return contact.profile.name
