"""A judge model that reaches no provider and files every question: the price of a judge, read."""

from __future__ import annotations

import json
from typing import Any, override

from livekit.agents.llm import (
    LLM,
    ChatChunk,
    ChatContext,
    ChoiceDelta,
    FunctionToolCall,
    LLMStream,
    Tool,
    ToolChoice,
)
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

NAME = "counting-judge"

VERDICT = "submit_verdict"


# The whole point of a policy that answers by code is that it costs nothing to ask, and the only
# honest way to test that is to count the questions a judge was actually sent. So this one talks to
# nobody, answers whatever the test told it to, and keeps the prompts for the test to assert on.
class CountingJudge(LLM[Any]):
    """An `llm.LLM` that never leaves the process and files away every question put to it."""

    def __init__(self, *, verdict: str = "pass", reason: str = "the fake judge was asked") -> None:
        super().__init__()
        self.prompts: list[str] = []
        self._verdict = verdict
        self._reason = reason

    @property
    @override
    def model(self) -> str:
        """The name a report would print beside a score."""
        return NAME

    @override
    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> LLMStream:
        """One question, filed whole, answered with the verdict this fake was built with."""
        self.prompts.append(
            "\n".join(item.text_content or "" for item in chat_ctx.items if item.type == "message")
        )
        return _OneVerdict(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            arguments=json.dumps({"verdict": self._verdict, "reasoning": self._reason}),
        )


# livekit reads a verdict off the tool call of a collected response, so a fake that answered in
# prose would be testing a path the real judge never takes. This is the one chunk a provider
# would have streamed, pushed into the stream's own channel exactly as a plugin does
# (plugins/anthropic/llm.py:312-330).
class _OneVerdict(LLMStream):
    """A stream of exactly one chunk: the forced call to `submit_verdict`, and then the end."""

    def __init__(
        self,
        llm: LLM[Any],
        *,
        chat_ctx: ChatContext,
        tools: list[Tool],
        conn_options: APIConnectOptions,
        arguments: str,
    ) -> None:
        self._arguments = arguments
        super().__init__(  # pyright: ignore[reportUnknownMemberType] — livekit's stream is bare
            llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options
        )

    @override
    async def _run(self) -> None:
        """The whole answer in one chunk. Nothing here is IO, so there is nothing to await."""
        self._event_ch.send_nowait(
            ChatChunk(
                id=NAME,
                delta=ChoiceDelta(
                    role="assistant",
                    tool_calls=[
                        FunctionToolCall(call_id=NAME, name=VERDICT, arguments=self._arguments)
                    ],
                ),
            )
        )
