"""A call's prompt read back: each block its log names by hash, with the words the org kept."""

from fastapi import APIRouter

from pinecall.domain.errors import NotFound
from pinecall.gateway import _deps
from pinecall.gateway._deps import CallsKey, GatewayDep, ScopeDep, asked_by
from pinecall.log import queries
from pinecall.tenancy import prompts, reads
from pinecall.tenancy.reads import Read
from pinecall.wire.events import PromptChanged
from pinecall.wire.rest.calls import CallPromptResponse, PromptBlockRow

router = APIRouter()


# A key's read alone, never a page's token: the prompt is the operator's and the app's words.
# It is a read of the call, and on the record as one.
@router.get("/v1/calls/{call}/prompt")
async def call_prompt(
    call: str, key: CallsKey, scope: ScopeDep, gateway: GatewayDep
) -> CallPromptResponse:
    """Every block of prompt the call was told, in order, each with its words when kept."""
    reader = _deps.Reader(acting=key, scope=scope)
    await _deps.check_readable(gateway, reader, call)
    pool = gateway.connections.pool
    kept = await queries.scope_of_call(pool, call)
    entries = await gateway.logs.store.whole(call)
    if kept is None or kept.scope is None or not entries:
        raise NotFound(_deps.NO_SUCH_CALL.format(call=call))
    await reads.record(pool, scope, Read(call, "log", asked_by(key)))
    changes = [
        (entry.seq, PromptChanged.model_validate(entry.data))
        for entry in entries
        if entry.type == "prompt.changed"
    ]
    texts = await prompts.texts_of(pool, kept.scope.org, {block.hash for _, block in changes})
    return CallPromptResponse(
        call=call,
        blocks=[
            PromptBlockRow(
                seq=seq,
                name=block.name,
                hash=block.hash,
                chars=block.chars,
                text="" if block.chars == 0 else texts.get(block.hash),
            )
            for seq, block in changes
        ],
    )
