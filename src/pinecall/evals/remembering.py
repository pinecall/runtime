"""A golden's own memory: the facts a call opens knowing, answered to `recall` and never stored."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pinecall.session.lookup_tools import Lookup
from pinecall.types import PlatformTool

# Where a fact of a golden says it came from. A live fact names the call that taught it; this one
# names the file, so a run's log reads honestly and nobody mistakes it for something remembered.
A_GOLDEN = "golden"


# The one question memory exists to answer is whether the agent USES what it remembered, and that
# is a question about a conversation, not about a table: the facts are the golden's, the tool call
# and its result are the real ones, and nothing is written to or read from Postgres. `search` is
# not touched — a golden that also retrieves is asking the real index, which is the point.
class Remembering:
    """A Lookup that answers `recall` from a golden and hands everything else to the real one."""

    def __init__(self, lookups: Lookup, facts: Sequence[str]) -> None:
        self._lookups = lookups
        self._facts = tuple(facts)

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        """The golden's facts for a recall, the gateway's own answer for anything else."""
        if tool != "recall":
            return await self._lookups.lookup(call, tool, input, speech_id)
        return {"facts": [{"text": fact, "source": A_GOLDEN} for fact in self._facts]}
