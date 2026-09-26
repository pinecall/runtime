"""POST /v1/evals/judge/{call}: the judges over a finished call nobody judged, on somebody's ask."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException

from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.log_sink import the_calls_corner
from pinecall.api.deps import CallIndexDep, EvalsKeyDep, SettingsDep, StoreDep
from pinecall.evals.score import a_score
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.types import AgentConfig
from pinecall_protocol import encode
from pinecall_protocol.events import CallScore

router = APIRouter()

STILL_GOING = "call {call} is still going: it is judged when it hangs up"

ALREADY_JUDGED = (
    "call {call} was judged at hang-up: ?again=true judges it again, and pays for it again"
)


# What a hang-up does, done later: the org had judging off, the judge broke, or a person wants a
# second opinion. The verdict is written onto the call's own log — the one entry a sealed log takes
# (Store.rescored) — so the call index, the list and the day read it like any other, and usage
# meters what the judges cost. The box's ceiling holds here exactly as it holds at hang-up.
@router.post("/v1/evals/judge/{call}")
async def judge(
    call: str,
    key: EvalsKeyDep,
    store: StoreDep,
    index: CallIndexDep,
    registry: RegistryDep,
    settings: SettingsDep,
    again: bool = False,
) -> CallScore:
    """The judges' verdict on this finished call, written onto its log and answered."""
    corner = await the_calls_corner(index, key, call)
    entries = await whole(store, call)
    if not any(entry.type == "call.ended" for entry in entries):
        raise HTTPException(409, STILL_GOING.format(call=call))
    if _judged(entries) and not again:
        raise HTTPException(409, ALREADY_JUDGED.format(call=call))
    declared = registry.declared(corner.agent) or AgentConfig(slug=corner.agent)
    scored = await a_score(entries, declared, settings)
    await store.rescored(call, corner.agent, encode(scored))
    return scored


def _judged(entries: Sequence[Entry]) -> bool:
    """Whether some call.score of this call already carries a verdict."""
    return any(
        entry.type == "call.score" and entry.data.get("passed") is not None for entry in entries
    )
