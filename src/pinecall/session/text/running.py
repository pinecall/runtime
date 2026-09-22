"""One tool as livekit runs it: the app's own process behind, and the answer back to the model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from livekit.agents.llm import ToolError
from livekit.agents.llm.tool_context import StopResponse

from pinecall.log import as_text
from pinecall.session.declaring import ToolUse
from pinecall.session.pending import ToolCalls
from pinecall.session.visibility import Visibility
from pinecall.types import AgentConfig
from pinecall_protocol.events import StateCauseTool

if TYPE_CHECKING:
    from pinecall.session.text.session import TextSession


class Running:
    """The tools of one call: what the app has been asked for and has not answered yet."""

    def __init__(self, session: TextSession, config: AgentConfig) -> None:
        self._session = session
        self.calls = ToolCalls(config)
        self.visibility = Visibility(config)

    # livekit executes the tool itself, so this callable is where the platform stands between the
    # model and the app: a tool the app closed is refused here, every other call goes straight
    # through, irreversible or not.
    async def ran(self, call: ToolUse) -> str:
        """One tool call as livekit runs it, through the app's own process and back."""
        await self.visibility.admitted(call.name, self._session.emit)
        text, failed = await self.through_app(call)
        if failed:  # is_error is what the model reads a failure as, and livekit sets it from this
            raise ToolError(text)
        # A supervisor took the thread while this tool ran — the ask for a person is the tool
        # that does it — and the model must not write over them. The result is in the log and in
        # the history; the reply to it is what is dropped.
        if self._session.taken_by is not None:
            raise StopResponse
        return text

    async def through_app(self, call: ToolUse) -> tuple[str, bool]:
        """One tool through the app, stamped as the cause of whatever state it changes."""
        session = self._session
        session.cause = StateCauseTool(kind="tool", tool=call.name, call_id=call.call_id)
        result = await self.calls.ran(call, session.speech_now, session.emit)
        session.cause = None
        return as_text(result), result.error is not None
