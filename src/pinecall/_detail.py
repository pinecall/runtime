"""What a gateway's refusal says, read back by whoever knocked: the `detail`, else the body."""

from __future__ import annotations

import json
from typing import cast


# FastAPI answers a refusal as {"detail": "<sentence>"}, and the whole body is what a client
# raises with. Whoever writes ONE line for a person — the hop's log line, the CLI's error, what
# production answered a sandbox — writes the sentence somebody can act on, never the JSON around
# it. A body that is not JSON, or JSON with no detail, is said as it came.
def refusal_detail(said: str) -> str:
    """The `detail` of a refused answer, or the answer as it came when there is none."""
    opened = said.find("{")
    if opened == -1:
        return said
    try:
        read: object = json.loads(said[opened:])
    except ValueError:
        return said
    if not isinstance(read, dict):
        return said
    detail = cast("dict[str, object]", read).get("detail")
    return detail if isinstance(detail, str) and detail else said
