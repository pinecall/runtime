"""Meta's Graph API on a fake transport, and the one transport for everything outside the box."""

import json
from dataclasses import dataclass, field

import httpx

from tests.fakes.twilio import Twilio


@dataclass
class Graph:
    """Meta's Graph API on a fake transport: every message sent, or a refusal."""

    sent: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    tokens: list[str] = field(default_factory=list[str])
    # How Graph answers a send it refuses; None sends.
    refusal: tuple[int, str] | None = None

    # The number the fake account answers at, as Meta shows it.
    number: str = "+598 29 001 199"
    name: str = "Clinica"

    def answer(self, request: httpx.Request) -> httpx.Response:
        """A send to a number's messages, kept, or refused as told; the account's number on GET."""
        if request.method == "GET":
            if self.refusal is not None:
                status, text = self.refusal
                return httpx.Response(status, json={"error": {"message": text, "code": 190}})
            text = {"display_phone_number": self.number, "verified_name": self.name}
            return httpx.Response(200, json={**text, "id": request.url.path.split("/")[2]})
        if self.refusal is not None:
            status, text = self.refusal
            return httpx.Response(status, json={"error": {"message": text, "code": 131047}})
        self.tokens.append(request.headers.get("authorization", ""))
        body: dict[str, object] = json.loads(request.content)
        self.sent.append({"from": request.url.path.split("/")[2], **body})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.sent)}"}]})


def outside(twilio: Twilio, graph: Graph) -> httpx.MockTransport:
    """One transport for everything outside the box: Meta's Graph, and Twilio for the rest."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return graph.answer(request)
        return twilio.transport().handle_request(request)

    return httpx.MockTransport(answer)
