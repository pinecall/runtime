"""Who decided a spoken call was over, in the words the log has to use for it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from livekit.agents import llm as agents
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.llm.tool_context import StopResponse
from livekit.agents.voice.events import CloseReason

from pinecall.types import AgentConfig
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall

# The tool is hidden while the agent is greeting. A model that may hang up on its very first turn
# eventually does — the first real call of this project had Haiku reach for a `transfer` tool
# nobody had asked it to reach for — and a greeting is the one turn where no caller has said
# anything for the model to have understood. livekit's own flag for it, so nothing here counts
# turns: `EndCallTool(ignore_on_enter=True)` → ToolFlag.IGNORE_ON_ENTER.
WHILE_GREETING_IT_IS_HIDDEN = True

# Deleting the room is what ends the caller's SIP leg: a room that outlives the agent leaves the
# person on the line listening to nothing. livekit's default, kept, and named here because the
# other value is what a call bridged to a person would want — and that ending is bridging.py's,
# which never goes through this tool: a bridged agent is mute and calls nothing.
THE_ROOM_GOES_WITH_IT = True

# livekit's tool answers the model "say goodbye to the user" and lets it generate one more reply
# after the call is already ending — and Haiku, told that, said "I understand. I'm ready to help
# the next caller" to a caller who had just been thanked and wished goodbye (box, 2026-09-16, a
# Talk from the console). So the goodbye is the model's own, in the turn that reaches for the
# tool, and the tool asks for silence after it: `StopResponse` is livekit's word for a tool
# whose output wants no reply, and the session shuts down when that turn's speech is played out.
SAY_GOODBYE_FIRST = (
    "Say your goodbye in the same reply in which you call this, before the call: nothing you "
    "say after it is heard, and nothing is generated for you."
)


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

    async def a_platform_tool_ran(self, called: ToolCall, result: defs.ToolResult) -> None:
        """tool.call and tool.result for a tool the platform ran itself, so the log has them."""


# The seam the whole adoption turns on. livekit's tool ends the call with `session.shutdown()`,
# which closes as CloseReason.USER_INITIATED; our HOW_IT_ENDED reads that as `drained` by the
# `platform` — the entry a deploy taking the worker down writes. So the reason is set BEFORE the
# shutdown, exactly the way a cold transfer sets its own, and the close mapping never gets to
# guess. voice.py `_how_it_ended` prefers what was set over what it would have derived.
def hangup_toolset(config: AgentConfig, ending: Ends) -> list[agents.Toolset]:
    """livekit's own end_call when the class asked for one, and nothing at all when it did not."""
    declared = config.hangup
    if declared is None:
        return []

    return [
        EndCallTool(
            extra_description=f"{declared.when or ''}\n{SAY_GOODBYE_FIRST}".strip(),
            ignore_on_enter=WHILE_GREETING_IT_IS_HIDDEN,
            delete_room=THE_ROOM_GOES_WITH_IT,
            end_instructions=None,
            on_tool_called=on_end_call_first(ending),
            on_tool_completed=silence_after,
        )
    ]


async def silence_after(_: EndCallTool.ToolCompletedEvent) -> None:
    """No reply is generated after end_call: the goodbye was the model's own, a moment ago."""
    raise StopResponse()


# The app's tools are written as tool.call and tool.result on their way through pending.py; this
# one never passes there, it is livekit's own, and a log without it showed the agent speaking
# twice in a row with nothing in between — "it talked to itself" (box, 2026-09-16, a Talk from
# the console). The tool that ran between the two turns is written down here, as the others are.
def on_end_call_first(ending: Ends) -> Callable[[EndCallTool.ToolCalledEvent], Awaitable[None]]:
    """What runs the moment the model calls end_call, before livekit closes anything."""

    async def before_it_closes(event: EndCallTool.ToolCalledEvent) -> None:
        ending.ended_by_the_model()
        # The context is generic over the session's userdata, which this tool never reads.
        ctx = cast(Any, event.ctx)  # pyright: ignore[reportUnknownMemberType]
        call_id: str = ctx.function_call.call_id
        await ending.a_platform_tool_ran(
            ToolCall(call_id=call_id, name=END_CALL, arguments={}, speech_id=ctx.speech_handle.id),
            defs.ToolResult(call_id=call_id, name=END_CALL, output=None),
        )

    return before_it_closes


END_CALL = "end_call"
