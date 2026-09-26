"""Where a call token is spent: the dispatch that opens the call, once; a second one is refused."""

from __future__ import annotations

from pinecall.errors import PinecallError
from pinecall.log.writers import Logs
from pinecall.tokens.ledger import Tokens
from pinecall.types import CallContext
from pinecall.types.dispatch import SCOPE_KEY
from pinecall_protocol import encode
from pinecall_protocol.events import ErrorEvent

# The error's code in the agent's log, and the two sentences a worker reads in its 409. The log
# entry is the reason a person finds: the worker's process log names the call, the agent's own
# log names the call AND stays where the console reads it.
TOKEN_SPENT = "token_spent"
ALREADY_SPENT = "call {call} was already opened by its token: a call token opens one call, once"
NEVER_MINTED = "call {call} names a token this runtime never minted"


class TokenRefused(PinecallError):
    """The token that opened this call was spent already, or was never minted here."""


# Only a dispatch that carries a scope was minted by POST /v1/tokens: a phone call's room is named
# by the media plane, a console's by livekit, an outbound call's by the verb that placed it, and
# none of them holds a row here. The check is the ledger's one guarded UPDATE, so two workers
# racing for one token are told apart by the database, never by this process.
async def spent(context: CallContext, agent: str, tokens: Tokens, logs: Logs) -> None:
    """Spend the token that opened this call, or refuse the call with the reason in the log."""
    if not isinstance(context.metadata.get(SCOPE_KEY), str):
        return
    outcome = await tokens.spend(context.call)
    if outcome == "spent":
        return
    sentence = ALREADY_SPENT if outcome == "already_spent" else NEVER_MINTED
    reason = sentence.format(call=context.call)
    refused = ErrorEvent(code=TOKEN_SPENT, message=reason, recoverable=True)
    await logs.writing_agent(agent).append("error", encode(refused))
    raise TokenRefused(reason)
