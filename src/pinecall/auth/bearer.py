"""What a door does with a bad key: one parser of the Authorization header, one close code."""

from collections.abc import Mapping

# 1008 is "policy violation": the handshake is well formed and the answer is still no. Every socket
# that refuses a caller closes with this one, with no body — a key that is wrong learns nothing
# about why, and three doors saying it separately is three chances to say something else.
POLICY_VIOLATION = 1008

# The scheme, lowercased once: HTTP says a scheme is case-insensitive, so the header is folded
# before it is compared and never the other way around.
_BEARER = "bearer "


def bearer_of(headers: Mapping[str, str]) -> str | None:
    """The credential in `Authorization: Bearer …`, or None when there is none to read."""
    header = headers.get("authorization", "")
    if not header.lower().startswith(_BEARER):
        return None
    return header[len(_BEARER) :].strip() or None
