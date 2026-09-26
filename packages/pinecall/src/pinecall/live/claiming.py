"""A caller's code claimed by the live call it reached: bound on its table, said on its log."""

from __future__ import annotations

from typing import Literal

from pinecall.live.calls import Served
from pinecall.orgs.caller_codes import Codes
from pinecall_protocol import encode
from pinecall_protocol.events import CallClaimed

# How a code reached a call: the caller keyed it, or the agent heard it said and claimed it.
type Via = Literal["keypad", "agent"]


# One claim, from the keypad or from the agent: the code is taken on the agent's log, and the call
# says so on its own — the agent's class learns the person is on the site, the console shows it.
async def claim_code(codes: Codes, served: Served, call: str, code: str, via: Via) -> bool:
    """Bind the call to the code, call.claimed on its log; False when no page waits on the code."""
    issued = await codes.claim(served.context.env, served.agent, code, call)
    if issued is None:
        return False
    await served.log.append("call.claimed", encode(CallClaimed(code=code, via=via)))
    return True
