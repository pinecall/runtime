"""Every declared tool as livekit runs one: out to the app's own process, and the answer back."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from livekit.agents import llm as agents
from livekit.agents.llm import ToolError
from livekit.agents.voice import RunContext

from pinecall.log import as_text
from pinecall.session.voice.platform import Platform, PlatformRefused
from pinecall.types import AgentConfig, ToolSpec
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall

# What a template names: {{slot.when}} reads `when` of the `slot` argument. A name nobody filled
# is left as it was written, so a half-rendered sentence is visible instead of silently empty.
_A_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


class Tools:
    """The tools of one call: what the model may call, and what happens when it does."""

    def __init__(self, config: AgentConfig, platform: Platform, call: str) -> None:
        self._config = config
        self._platform = platform
        self._call = call
        # Per call and never module-level: one worker process answers one job, but a read-back
        # belongs to the tool call that earned it and to nothing else.
        self.read_backs: dict[str, str] = {}

    @property
    def visible(self) -> list[agents.Tool]:
        """Everything the app declared, as livekit declares a tool: the schema, and our callable."""
        return self.declared(tuple(self._config.tools_by_name.values()))

    def declared(self, specs: Sequence[ToolSpec]) -> list[agents.Tool]:
        """The given specs as livekit's raw-schema tools, each wired back to this call."""
        return [self._a_tool(spec) for spec in specs]

    # The confirmation gate is deferred (docs/decisions/confirm.md): a tool with a confirm
    # template runs like every other tool, and the sentence it declared is read back afterwards,
    # once the app has answered and the output is entering the model's history. What speaks it is
    # the bridge, on the session event that says the outputs are in — voice.py, _tools_executed.
    async def ran(self, spec: ToolSpec, use: ToolCall) -> str:
        """One tool through the app and back: the text the model reads, or an error it can say."""
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
            return await self.ran(
                spec,
                ToolCall(
                    call_id=context.function_call.call_id,
                    name=spec.name,
                    arguments=dict(raw_arguments),
                    speech_id=context.speech_handle.id,
                ),
            )

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
