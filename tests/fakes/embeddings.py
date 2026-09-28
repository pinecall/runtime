"""An embedder over HTTP, OpenAI-shaped, on a fake transport."""

import base64
import json
import re
import zlib
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


@dataclass
class Meanings:
    """An embeddings vendor whose vectors mean something: one axis per word it knows."""

    words: tuple[str, ...]
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def transport(self) -> httpx.MockTransport:
        """A transport that answers the flat shape, a vector per text."""
        return httpx.MockTransport(self._answer)

    def inputs(self) -> list[list[str]]:
        """The texts of every request, in order."""
        return [_texts_of(request) for request in self.requests]

    # A word it does not know lands a little on an axis of its own, so two texts that share no
    # known word share almost nothing, and a query of unknown words finds almost nothing.
    def vector(self, text: str) -> list[float]:
        """One axis per known word, counted; a tenth on a hashed axis per unknown word."""
        vector = [0.0] * 1024
        for word in re.findall(r"\w+", text.casefold()):
            if word in self.words:
                vector[self.words.index(word)] += 1.0
                continue
            spare = 1024 - len(self.words)
            vector[len(self.words) + zlib.crc32(word.encode()) % spare] += 0.1
        return vector

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        rows = [
            {"index": at, "embedding": self.vector(text)}
            for at, text in enumerate(_texts_of(request))
        ]
        return httpx.Response(200, json={"data": rows})


def int8_vector(values: list[int]) -> str:
    """A vector as the contextual shape sends it: signed bytes, base64."""
    return base64.b64encode(array("b", values).tobytes()).decode("ascii")


def _texts_of(request: httpx.Request) -> list[str]:
    data = json.loads(request.content)
    return data["input"][0] if request.url.path.endswith(CONTEXTUALIZED) else data["input"]
