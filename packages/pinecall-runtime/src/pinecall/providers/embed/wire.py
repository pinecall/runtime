"""Reading an OpenAI-shaped embeddings reply: the two encodings, the nesting, the unit length."""

from __future__ import annotations

import base64
import math
from array import array
from collections.abc import Sequence
from typing import Any, cast

import httpx

# The encodings a request NAMES, so that decoding never has to guess. Without the first, the flat
# door answers base64 and a reader that inferred the type would infer it per reply; the second is
# the only one the contextual door accepts at all, and it is one thousand and twenty-four SIGNED
# bytes — a plain base64 decode reads them as unsigned and points every vector somewhere else.
FLOATS = "float"
SIGNED_BYTES = "base64_int8"


# A flat reply is `data[i].embedding`; a contextual one is `data[d].data[i].embedding`, because
# the request carried a list of DOCUMENTS. An unreadable reply answers with nothing rather than
# raising here: the caller counts the vectors against the chunks it asked about, and "0 vectors
# for 12 chunks" names the vendor and the door, which a KeyError three frames down does not.
def embeddings_under(body: Any, *, of_the_first_document: bool = False) -> list[list[float]]:
    """Every `embedding` of the reply, decoded from whichever of the two encodings it came in."""
    rows = rows_of(body)
    if of_the_first_document:
        rows = rows_of(rows[0]) if rows else []
    return [decode_vector(row.get("embedding")) for row in rows]


def rows_of(body: Any) -> list[dict[str, Any]]:
    """The reply's `data`, or nothing at all when the reply is not the shape it promised."""
    try:
        rows: Any = body["data"]
    except (TypeError, KeyError, IndexError):
        return []
    return cast("list[dict[str, Any]]", rows) if isinstance(rows, list) else []


def decode_vector(raw: Any) -> list[float]:
    """`base64_int8` as the signed bytes it is, and `float` as it already reads."""
    if isinstance(raw, str):
        values = array("b")
        values.frombytes(base64.b64decode(raw))
        return [float(value) for value in values]
    return [float(value) for value in cast("list[float]", raw)] if isinstance(raw, list) else []


def unit_vector(vector: Sequence[float]) -> list[float]:
    """The vector at length one, which is what makes a cosine index a similarity and not a size."""
    length = math.sqrt(sum(value * value for value in vector))
    return list(vector) if length == 0.0 else [value / length for value in vector]


def endpoint_error(answer: httpx.Response) -> str:
    """The endpoint's own `error.message`, when its body carries one; the status otherwise."""
    try:
        said: Any = answer.json()["error"]["message"]
    except (ValueError, TypeError, KeyError, IndexError):
        return f"HTTP {answer.status_code}"
    return str(said) if said else f"HTTP {answer.status_code}"
