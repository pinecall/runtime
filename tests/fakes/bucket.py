"""A Cloud Storage bucket and the metadata server's token, on a fake transport."""

from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx

TOKEN = "ya29.a-token-of-the-machine"


@dataclass
class Bucket:
    """One bucket's objects by name, answering upload, ranged read and delete as Google does."""

    name: str = "pinecall-recordings"
    objects: dict[str, bytes] = field(default_factory=dict[str, bytes])
    # A status every request to the bucket is answered with instead; None answers.
    refusal: int | None = None
    asked: list[str] = field(default_factory=list[str])

    def transport(self) -> httpx.MockTransport:
        """The metadata server and storage.googleapis.com, as the machine reaches them."""
        return httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        """A token for the machine, or the bucket's answer to an object's request."""
        if request.url.host == "metadata.google.internal":
            assert request.headers["metadata-flavor"] == "Google"
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        self.asked.append(f"{request.method} {request.url.path}")
        if self.refusal is not None:
            return httpx.Response(self.refusal, text="refused")
        if request.url.path == f"/upload/storage/v1/b/{self.name}/o":
            self.objects[request.url.params["name"]] = request.content
            return httpx.Response(200, json={"name": request.url.params["name"]})
        prefix = f"/storage/v1/b/{self.name}/o/"
        name = unquote(request.url.raw_path.decode().split("?")[0].removeprefix(prefix))
        if name not in self.objects:
            return httpx.Response(404, text="No such object")
        if request.method == "DELETE":
            del self.objects[name]
            return httpx.Response(204)
        return self._read(self.objects[name], request.headers.get("range"))

    def _read(self, data: bytes, byte_range: str | None) -> httpx.Response:
        if byte_range is None:
            return httpx.Response(200, content=data, headers={"accept-ranges": "bytes"})
        first, _, last = byte_range.removeprefix("bytes=").partition("-")
        start = int(first)
        if start >= len(data):
            return httpx.Response(416, headers={"content-range": f"bytes */{len(data)}"})
        end = min(int(last) if last else len(data) - 1, len(data) - 1)
        return httpx.Response(
            206,
            content=data[start : end + 1],
            headers={"content-range": f"bytes {start}-{end}/{len(data)}", "accept-ranges": "bytes"},
        )
