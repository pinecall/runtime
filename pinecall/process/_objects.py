"""The box's object store, spoken in S3: any endpoint that takes AWS's version 4 signature."""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

from pinecall.process.settings import Settings

ALGORITHM = "AWS4-HMAC-SHA256"

# What S3 is told a request without a body hashes to.
NO_BODY = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class Signing:
    """What a request is signed with: the region the store names, and the key's id and secret."""

    region: str
    access_key_id: str
    secret_access_key: str


@dataclass(frozen=True)
class ObjectStore:
    """An S3-compatible endpoint and the key the box reads and writes it with."""

    endpoint: str
    signing: Signing
    http: httpx.AsyncClient

    # Path-style: the one address every S3-compatible store answers; a bucket as a host name is
    # AWS's and a few others' alone.
    def url_of(self, bucket: str, name: str) -> str:
        """The object's address."""
        return f"{self.endpoint.rstrip('/')}/{bucket}/{quote(name, safe='/')}"

    def signed(
        self, method: str, url: str, headers: Mapping[str, str], payload_sha256: str = NO_BODY
    ) -> dict[str, str]:
        """The headers to send, signed now: the ones given, the date, the hash and the proof."""
        return signature(method, url, headers, self.signing, (payload_sha256, datetime.now(UTC)))


def object_store_of(settings: Settings, http: httpx.AsyncClient) -> ObjectStore | None:
    """The store the settings name whole; None when any of its four is missing."""
    endpoint, region = settings.s3_endpoint, settings.s3_region
    key, secret = settings.s3_access_key_id, settings.s3_secret_access_key
    if endpoint is None or region is None or key is None or secret is None:
        return None
    return ObjectStore(endpoint, Signing(region, key, secret), http)


# https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html. The store's
# requests name no query, so the canonical query is the empty line.
def signature(
    method: str,
    url: str,
    headers: Mapping[str, str],
    signing: Signing,
    payload: tuple[str, datetime],
) -> dict[str, str]:
    """The headers with the date, the payload's hash and the Authorization that signs them all."""
    payload_sha256, now = payload
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    parts = urlsplit(url)
    signed = {
        **{name.lower(): value.strip() for name, value in headers.items()},
        "host": parts.netloc,
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": stamp,
    }
    names = sorted(signed)
    canonical = "\n".join(
        (
            method,
            parts.path or "/",
            "",
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        )
    )
    scope = f"{stamp[:8]}/{signing.region}/s3/aws4_request"
    to_sign = "\n".join((ALGORITHM, stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    key = f"AWS4{signing.secret_access_key}".encode()
    for step in (stamp[:8], signing.region, "s3", "aws4_request"):
        key = hmac.new(key, step.encode(), hashlib.sha256).digest()
    proof = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    credential = f"Credential={signing.access_key_id}/{scope}"
    authorization = f"{ALGORITHM} {credential}, SignedHeaders={';'.join(names)}, Signature={proof}"
    return {**signed, "authorization": authorization}
