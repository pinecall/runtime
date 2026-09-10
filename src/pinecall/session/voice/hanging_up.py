"""How a spoken call ends: who decided it was over, in the words the log has to use."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from livekit.agents import llm as agents
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.voice.events import CloseReason

from pinecall.types import AgentConfig
from pinecall_protocol import defs

# The tool is hidden while the agent is greeting. A model that may hang up on its very first turn
# eventually does — the first real call of this project had Haiku reach for a `transfer` tool
# nobody had asked it to reach for — and a greeting is the one turn where no caller has said
# anything for the model to have understood. livekit's own flag for it, so nothing here counts
# turns: `EndCallTool(ignore_on_enter=True)` → ToolFlag.IGNORE_ON_ENTER.
WHILE_GREETING_IT_IS_HIDDEN = True

# Deleting the room is what ends the caller's SIP leg: a room that outlives the agent leaves the
# person on the line listening to nothing. livekit's default, kept, and named here because the
# other value is what a WARM transfer would want and we do not do warm transfers.
THE_ROOM_GOES_WITH_IT = True


# How livekit's own reason for closing the session reads on our wire. JOB_SHUTDOWN is the platform
# taking the worker down with the call still on it (a deploy, a stop, a drain) and USER_INITIATED
# is our own code closing the session outside hangup — the console's Ctrl+C is one — so both are
# `drained`: nobody's fault and not an error. See docs/decisions/voice-bridge.md.
HOW_IT_ENDED: dict[CloseReason, tuple[defs.EndReason, defs.EndedBy]] = {
    CloseReason.PARTICIPANT_DISCONNECTED: ("caller_hung_up", "caller"),
    CloseReason.ERROR: ("error", "platform"),
    CloseReason.JOB_SHUTDOWN: ("drained", "platform"),
    CloseReason.USER_INITIATED: ("drained", "platform"),
    CloseReason.TASK_COMPLETED: ("agent_hung_up", "agent"),
}


class Ends(Protocol):
    """Who writes down that the model itself ended the call, before livekit closes the session."""

    def ended_by_the_model(self) -> None:
        """The next call.ended says the agent hung up, whatever reason the session closes with."""


# The seam the whole adoption turns on. livekit's tool ends the call with `session.shutdown()`,
# which closes as CloseReason.USER_INITIATED; our HOW_IT_ENDED reads that as `drained` by the
# `platform` — the entry a deploy taking the worker down writes. So the reason is set BEFORE the
# shutdown, exactly the way a cold transfer sets its own, and the close mapping never gets to
# guess. voice.py `_how_it_ended` prefers what was set over what it would have derived.
def a_way_to_hang_up(config: AgentConfig, ending: Ends) -> list[agents.Toolset]:
    """livekit's own end_call when the class asked for one, and nothing at all when it did not."""
    declared = config.hangup
    if declared is None:
        return []

    return [
        EndCallTool(
            extra_description=declared.when,
            ignore_on_enter=WHILE_GREETING_IT_IS_HIDDEN,
            delete_room=THE_ROOM_GOES_WITH_IT,
            on_tool_called=the_reason_first(ending),
        )
    ]


def the_reason_first(ending: Ends) -> Callable[[EndCallTool.ToolCalledEvent], Awaitable[None]]:
    """What runs the moment the model calls end_call, before livekit closes anything."""

    async def before_it_closes(_: EndCallTool.ToolCalledEvent) -> None:
        ending.ended_by_the_model()

    return before_it_closes
