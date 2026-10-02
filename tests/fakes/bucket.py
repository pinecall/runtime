"""An S3-compatible bucket on a fake transport, refusing any request whose signature is wrong."""

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx

from pinecall.process._objects import ObjectStore, Signing

ENDPOINT = "https://objects.box.test"

REGION = "auto"

ACCESS_KEY_ID = "PCTESTKEYID"

SECRET_ACCESS_KEY = "made-up-by-this-test/secret"

# What a box names its object store with, as store.env and the sealed credential give it.
STORE_SETTINGS = {
    "PINECALL_S3_ENDPOINT": ENDPOINT,
    "PINECALL_S3_REGION": REGION,
    "PINECALL_S3_ACCESS_KEY_ID": ACCESS_KEY_ID,
    "PINECALL_S3_SECRET_ACCESS_KEY": SECRET_ACCESS_KEY,
}

# AWS's own worked example of a signed GET with a range (docs.aws.amazon.com, "Signature
# Calculations for the Authorization Header", GET Object): the key pair and the signature it gives.
AWS_EXAMPLE_KEY_ID = "AKIAIOSFODNN7EXAMPLE"

AWS_EXAMPLE_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

AWS_EXAMPLE_SIGNATURE = "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"

_AUTHORIZATION = re.compile(
    r"^AWS4-HMAC-SHA256 Credential=(?P<key>[^/]+)/(?P<scope>[^,]+), "
    r"SignedHeaders=(?P<names>[^,]+), Signature=(?P<proof>[0-9a-f]{64})$"
)


@dataclass
class Bucket:
    """One bucket's objects by name, answering put, ranged get, head and delete as S3 does."""

    name: str = "pinecall-recordings"
    objects: dict[str, bytes] = field(default_factory=dict[str, bytes])
    # A status every request to the bucket is answered with instead; None answers.
    refusal: int | None = None
    asked: list[str] = field(default_factory=list[str])

    def store_on(self, http: httpx.AsyncClient) -> ObjectStore:
        """The object store this bucket is in, as a box names it, over this client."""
        return ObjectStore(ENDPOINT, Signing(REGION, ACCESS_KEY_ID, SECRET_ACCESS_KEY), http)

    def transport(self) -> httpx.MockTransport:
        """The store's endpoint, as the box reaches it."""
        return httpx.MockTransport(self.answer)

    def answer(self, request: httpx.Request) -> httpx.Response:
        """The bucket's answer to an object's request, once its signature proves the key."""
        assert request.url.host == "objects.box.test"
        if (why := _unsigned(request)) is not None:
            return httpx.Response(403, text=f"SignatureDoesNotMatch: {why}")
        self.asked.append(f"{request.method} {request.url.path}")
        if self.refusal is not None:
            return httpx.Response(self.refusal, text="refused")
        prefix = f"/{self.name}/"
        assert request.url.path.startswith(prefix)
        name = unquote(request.url.raw_path.decode().removeprefix(prefix))
        if request.method == "PUT":
            self.objects[name] = request.content
            return httpx.Response(200)
        if name not in self.objects:
            return httpx.Response(404, text="NoSuchKey")
        return self._kept(request, name)

    def _kept(self, request: httpx.Request, name: str) -> httpx.Response:
        if request.method == "DELETE":
            del self.objects[name]
            return httpx.Response(204)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(self.objects[name]))})
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


# Recomputed from what arrived, not from what the client meant to send: the host, the path and
# every header as they crossed the wire.
def _unsigned(request: httpx.Request) -> str | None:
    found = _AUTHORIZATION.match(request.headers.get("authorization", ""))
    stamp = request.headers.get("x-amz-date", "")
    payload = request.headers.get("x-amz-content-sha256", "")
    refusals = (
        (found is None, "no AWS4 Authorization"),
        (found is not None and found["key"] != ACCESS_KEY_ID, "an unknown key"),
        (
            found is not None and found["scope"] != f"{stamp[:8]}/{REGION}/s3/aws4_request",
            "a scope that is not this store's",
        ),
        (
            payload != hashlib.sha256(request.content).hexdigest(),
            "a body that is not the one hashed",
        ),
    )
    why = next((reason for refused, reason in refusals if refused), None)
    if why is not None or found is None:
        return why
    names = found["names"].split(";")
    if not {"host", "x-amz-date", "x-amz-content-sha256"} <= set(names):
        return "the host, the date or the hash left unsigned"
    lines = "".join(f"{name}:{request.headers[name].strip()}\n" for name in names)
    path = request.url.raw_path.decode()
    canonical = f"{request.method}\n{path}\n\n{lines}\n{found['names']}\n{payload}"
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{found['scope']}\n{digest}"
    key = ("AWS4" + SECRET_ACCESS_KEY).encode()
    for step in found["scope"].split("/"):
        key = hmac.new(key, step.encode(), hashlib.sha256).digest()
    expected = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return None if hmac.compare_digest(expected, found["proof"]) else "a signature that differs"
