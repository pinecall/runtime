"""Every declared tool as livekit runs one: out to the app's own process, and the answer back."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from livekit.agents import llm as agents
from livekit.agents.llm import ToolError
from livekit.agents.llm.tool_context import StopResponse
from livekit.agents.voice import RunContext

from pinecall.log import as_text
from pinecall.session.pending import Emit
from pinecall.session.visibility import Visibility
from pinecall.session.voice.hold import HoldMusic
from pinecall.session.voice.platform import Platform, PlatformRefused
from pinecall.session.voice.reading_back import read_back
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall

# What a template names: {{slot.when}} reads `when` of the `slot` argument. A name nobody filled
# is left as it was written, so a half-rendered sentence is visible instead of silently empty.
_A_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _nobody() -> bool:
    """Nobody is holding the line: what a call with no supervision on it answers."""
    return False


class Tools:
    """The tools of one call: what the model may call, and what happens when it does."""

    def __init__(
        self,
        config: AgentConfig,
        platform: Platform,
        call: str,
        emit: Emit,
        hold: HoldMusic | None = None,
    ) -> None:
        self._config = config
        # What the caller hears while a tool runs: nothing until the worker gives it a room.
        self.hold = hold or HoldMusic()
        self._platform = platform
        self._call = call
        self._emit = emit
        # Per call and never module-level: one worker process answers one job, but a read-back
        # belongs to the tool call that earned it and to nothing else.
        self.read_backs: dict[str, str] = {}
        self.visibility = Visibility(config)
        # Whether a person is holding the line right now. The bridge sets it once supervision
        # exists; until then nobody is, which is what every call starts as.
        self.a_person_has_the_line: Callable[[], bool] = _nobody

    # Every declared tool, once, for the life of the call: a tools.set moves `visibility` and
    # never livekit's tool list, because a re-declared tool throws the provider's whole cache away.
    @property
    def declared_tools(self) -> list[agents.Tool]:
        """Everything the app declared, as livekit declares a tool: the schema, and our callable."""
        return [self._a_tool(spec) for spec in self._config.tools]

    # The confirmation gate is deferred (docs/decisions/confirm.md): a tool with a confirm
    # template runs like every other tool, and the sentence it declared is read back INSIDE this
    # call, which is where livekit documents speaking around a tool — "use session.say() inside
    # the tool" (docs/agents/logic/tools/design). Said here it reaches the line before the model
    # has written a word about the result, because the model is still waiting for this to return.
    # Said afterwards it arrived last instead: by then the model had generated and queued the
    # whole reply, ending in "¿Alguna cosa más?", and the receipt spoke after the agent had
    # handed the turn back (2026-09-13, heard on a real call).
    async def ran(self, spec: ToolSpec, use: ToolCall) -> str:
        """One tool through the app and back: the text the model reads, or an error it can say."""
        await self.visibility.admitted(use.name, self._emit)
        result = await self._through_app(spec, use)
        text = as_text(result)
        if result.error is not None:
            # ToolError is how livekit sets is_error on the output the model reads back.
            raise ToolError(text)
        if spec.confirm:
            self.read_backs[use.call_id] = rendered(spec.confirm, use.arguments, result)
        return text

    async def _through_app(self, spec: ToolSpec, use: ToolCall) -> defs.ToolResult:
        """The round trip, with a platform that will not answer treated as a tool that did not."""
        try:
            return await self._platform.tool(self._call, self._config.slug, use, spec.timeout_s)
        except PlatformRefused as refused:
            return defs.ToolResult(call_id=use.call_id, name=use.name, error=str(refused))

    def _a_tool(self, spec: ToolSpec) -> agents.Tool:
        """One ToolSpec as livekit's raw-schema tool: our parameters, and our own body."""

        async def call(raw_arguments: dict[str, Any], context: RunContext[Any]) -> str:
            use = ToolCall(
                call_id=context.function_call.call_id,
                name=spec.name,
                arguments=dict(raw_arguments),
                speech_id=context.speech_handle.id,
            )
            # NOT while the agent is talking. The model emits its text and its tool call in ONE
            # response, so livekit begins the tool the instant the call arrives on the stream —
            # while the line announcing it is still being spoken, and often before its first
            # audio has played. The tool's own SpeechHandle cannot be awaited from in here (the
            # turn does not finish until the tool returns; `wait_for_playout()` raises with that
            # reason), but the STEP can: `RunContext.wait_for_playout()` waits for exactly the
            # words spoken before this tool (voice/events.py:103), and livekit's own error names it
            # as the way. So the announcement is heard, THEN the thing announced happens — and the
            # log reads in that order too, because the turn is written when its playout ends.
            await context.wait_for_playout()
            # The melody covers the round trip and nothing after it: it has stopped before the
            # read-back is said, so the caller never hears the sentence over the music.
            async with self.hold.playing():
                text = await self.ran(spec, use)
            # Heard out before the tool returns: the reply to this result is generated the instant
            # it does, from the history as it stands, and the receipt joins the history only once
            # its audio has played (reading_back.py). Returned earlier, the model answered the
            # result never having seen the receipt, and said the booking a second time.
            said = self.read_backs.pop(use.call_id, None)
            if said is not None:
                await read_back(context.session, said)
            # A supervisor took the line while this tool ran — the ask for a person is the tool
            # that does it — and the model must not answer over them. The result is in the
            # history and in the log; the reply to it is what is dropped. StopResponse is
            # livekit's own word for a tool whose output wants no reply.
            if self.a_person_has_the_line():
                raise StopResponse
            return text

        schema: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.parameters),
        }
        return agents.function_tool(call, raw_schema=schema)


# The template is the tenant's own sentence and the arguments are what the model asked for, so
# the read-back says what was actually done and not what the model believes it did. `result`
# reaches the template too: a booking that came back with a reference can read it out.
def rendered(template: str, arguments: Mapping[str, Any], result: defs.ToolResult) -> str:
    """The confirm template with its names filled in from the call and from what came back."""
    said: dict[str, Any] = {**dict(arguments), "result": result.output}
    return _A_PLACEHOLDER.sub(lambda found: _read(said, found.group(1), found.group(0)), template)


def _read(said: Mapping[str, Any], path: str, written: str) -> str:
    """One dotted name out of the arguments, or the placeholder itself when nothing filled it."""
    found: Any = said
    for step in path.split("."):
        if not isinstance(found, Mapping) or step not in found:
            return written
        found = cast("Mapping[str, Any]", found)[step]
    return "" if found is None else str(found)
