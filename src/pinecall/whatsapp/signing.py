"""The one thing that proves a webhook body came from Meta: the App Secret over the raw bytes."""

from __future__ import annotations

import hashlib
import hmac

# Meta signs every POST with the app's App Secret and sends the digest in this header, prefixed.
# Docs: developers.facebook.com/docs/graph-api/webhooks/getting-started#validate-payloads.
SIGNATURE_HEADER = "X-Hub-Signature-256"
_SHA256 = "sha256="


# The RAW body, never the parsed dict: JSON round-tripped through Python is not the bytes Meta
# hashed — a re-encoded body differs in whitespace and key order, and every signature would fail.
def signed(app_secret: str, body: bytes, header: str | None) -> bool:
    """Whether this body is the one Meta signed. A missing or malformed header is a no."""
    if header is None or not header.startswith(_SHA256):
        return False
    ours = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest and never ==: a byte-by-byte comparison that stops early tells an attacker
    # how much of a forged digest was right.
    return hmac.compare_digest(ours, header.removeprefix(_SHA256))
