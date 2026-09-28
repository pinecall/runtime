"""An embedder over HTTP, OpenAI-shaped, on a fake transport."""

import base64
import json
from array import array
from dataclasses import dataclass, field

import httpx

from pinecall.domain.names import JsonObject

# Too big, in the words one vendor refuses a request with.
TOO_BIG = "Input total size exceeds maximum number of allowed tokens"

CONTEXTUALIZED = "/contextualizedembeddings"


@dataclass
class Embeddings:
    """An embeddings vendor on a fake transport: both wire shapes, a script of failures first."""

    width: int = 1024
    # The value every float, and every signed byte of a contextual vector, carries.
    value: float = 0.5
    byte: int = 3
    # A request with more inputs than this is refused as too big.
    too_big_over: int | None = None
    # Answered in order before any request is answered well.
    script: list[httpx.Response | httpx.TransportError] = field(
        default_factory=list[httpx.Response | httpx.TransportError]
    )
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as the vendor does."""
        return httpx.MockTransport(self._answer)

    def sent(self) -> list[JsonObject]:
        """Every body the vendor was sent, in order."""
        return [json.loads(request.content) for request in self.requests]

    def inputs(self) -> list[list[str]]:
        """The texts of every request, in order: a contextual one's are its one window."""
        return [_texts_of(request) for request in self.requests]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            scripted = self.script.pop(0)
            if isinstance(scripted, httpx.TransportError):
                raise scripted
            return scripted
        texts = _texts_of(request)
        if self.too_big_over is not None and len(texts) > self.too_big_over:
            return httpx.Response(400, json={"error": {"message": TOO_BIG}})
        if request.url.path.endswith(CONTEXTUALIZED):
            vector = int8_vector([self.byte] * self.width)
            rows = [{"data": [{"embedding": vector} for _ in texts]}]
            return httpx.Response(200, json={"data": rows})
        rows = [{"index": at, "embedding": [self.value] * self.width} for at in range(len(texts))]
        return httpx.Response(200, json={"object": "list", "data": rows})


def _texts_of(request: httpx.Request) -> list[str]:
    data = json.loads(request.content)
    return data["input"][0] if request.url.path.endswith(CONTEXTUALIZED) else data["input"]


def int8_vector(values: list[int]) -> str:
    """A vector as the contextual shape sends it: signed bytes, base64."""
    return base64.b64encode(array("b", values).tobytes()).decode("ascii")
