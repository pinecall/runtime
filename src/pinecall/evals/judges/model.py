"""Who grades: one Haiku, and the tally of how many questions were actually put to it."""

from __future__ import annotations

from typing import Any, override

from livekit.agents.llm import LLM, ChatContext, LLMStream, Tool, ToolChoice
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

from pinecall._settings import Settings, load_settings
from pinecall.providers.models import Chat, models_for
from pinecall.types import NOTHING_BROUGHT


# The judge is a model like any other, so it is built the way every model in this tree is built —
# `providers/models.py`, the llm vendor table, the key read once. Asking for none names the vendor
# file's own default, which is Haiku (providers/llm/anthropic.py:11): the model CLAUDE.md puts
# wherever a test calls one. Every judge in livekit's framework takes an `llm.LLM`
# (evals/evaluation.py:100-130), which is what the table hands back, so nothing adapts anything.
#
# On the BOX's key and never a tenant's, deliberately: the conversation is the tenant's and runs on
# whatever keys their org brought (docs/decisions/provider-keys.md), while the judgment is the
# platform's own measurement of it, made in the same words for every org on the box. It is also the
# only way one rule holds in both places a call is judged — a run has the org in hand, a text call
# judging itself at hang-up does not. What judging may spend is a ceiling, not a key: see
# `judge_ceiling_eur` and docs/decisions/scoring.md.
def a_judge(settings: Settings | None = None) -> Chat:
    """The one model this package ever asks a question of, in the shape a Judge is handed."""
    return models_for(settings or load_settings())(None, NOTHING_BROUGHT)


# What a judged assertion costs is not something livekit reports: `JudgmentResult` carries a
# verdict, a reasoning and the instructions, and nothing else (evals/judge.py:34-57). So this
# package counts questions rather than inventing a price — the judge's tokens are an LLM row like
# any other, priced where every other LLM cost in this runtime is priced.
class Counted(LLM[Any]):
    """The judge model with a tally of the questions that actually reached a provider through it."""

    def __init__(self, judge: LLM[Any]) -> None:
        super().__init__()
        self._judge = judge
        self.calls = 0

    @property
    @override
    def model(self) -> str:
        """The name the judge answers to, unchanged: this wrapper is a counter, not a model."""
        return self._judge.model

    # livekit's own signature, kept whole (llm/llm.py:158-167): a narrower one would stop this
    # being an LLM, and a judge handed it would refuse to ask.
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
        """One question on its way to the judge, counted as it leaves."""
        self.calls += 1
        return self._judge.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )
