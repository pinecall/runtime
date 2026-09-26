"""The one place the WhatsApp Cloud API is called: a text back out, over the org's own token."""

from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from pinecall.errors import PinecallError
from pinecall.whatsapp.meta_json import LENIENT

# Meta's own host and the version this door was written against. Pinned: an unversioned URL would
# change the payload shape under a running box. developers.facebook.com/docs/whatsapp/cloud-api.
GRAPH = "https://graph.facebook.com/v21.0"

# What a refusal from Meta reads as. The token never appears in it: an error line is the one place
# a secret leaks into a log file, and this is the only sentence this module makes.
REFUSED = "the WhatsApp Graph API refused with {status}: {said}"


class GraphRefused(PinecallError):
    """Meta would not take the message. The call goes on; the log says the message did not."""

    def __init__(self, status: int, said: str) -> None:
        super().__init__(REFUSED.format(status=status, said=said))
        self.status = status


class Graph(Protocol):
    """What this door needs of Meta: one text to one person, on one of the org's numbers."""

    async def send_text(self, token: str, phone_number_id: str, to: str, text: str) -> None:
        """Put this text on the wire, or raise GraphRefused carrying Meta's own explanation."""
        ...


class HttpGraph:
    """The real Graph API, over one httpx client for the life of the process."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # A message is sent FROM the phone_number_id, not from the number: the number is what a person
    # dials and the id is what Meta routes on. The thread remembers the id the inbound carried, so
    # nothing about a WhatsApp Business Account is stored anywhere in this runtime.
    async def send_text(self, token: str, phone_number_id: str, to: str, text: str) -> None:
        """One text message out. A non-2xx answer is a GraphRefused carrying Meta's own words."""
        answer = await self._http.post(
            f"{GRAPH}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=_a_text_message(to, text),
        )
        if answer.status_code >= httpx.codes.BAD_REQUEST:
            raise GraphRefused(answer.status_code, _why(answer))


# The wamid Meta answers with is deliberately dropped: the only thing it is good for is joining a
# delivery receipt to the message it belongs to, and this door reads no statuses. The card that
# reads them mints the join. See docs/decisions/whatsapp.md.
class _Error(BaseModel):
    model_config = LENIENT
    message: str = ""


class _Refusal(BaseModel):
    """What Meta answers a bad request with: one error object, its message written for a human."""

    model_config = LENIENT
    error: _Error | None = None


def _a_text_message(to: str, text: str) -> dict[str, object]:
    """The body Meta takes for a plain text reply, with link previews off."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        # preview_url off: an agent that mentions a URL must not have Meta unfurl it into the
        # thread as a card nobody wrote.
        "text": {"preview_url": False, "body": text},
    }


# Meta answers a refusal with {"error": {"message": …, "code": …}} and an unreachable one with
# whatever the proxy in front of it sends. Both end up as one sentence rather than a traceback.
def _why(answer: httpx.Response) -> str:
    """Meta's own explanation of a refusal, or the body as it came when there is no JSON in it."""
    try:
        refusal = _Refusal.model_validate_json(answer.content)
    except ValidationError:
        return answer.text
    return refusal.error.message if refusal.error is not None else answer.text


# ── how a route asks for it ─────────────────────────────────────────────────────
