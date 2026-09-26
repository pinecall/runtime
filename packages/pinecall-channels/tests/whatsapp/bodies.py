"""What Meta posts at the webhook: the bodies, the numbers, and the signature over the raw bytes."""

import hashlib
import hmac
from typing import Any

from pinecall.whatsapp.webhook_signature import SIGNATURE_HEADER

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
