"""One call's lookups: recall and search as declared tools, and the pair each run leaves behind."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from livekit.agents import llm as agents
from livekit.agents.llm import ToolError
from livekit.agents.utils.aio import cancel_and_wait

from pinecall.session.tool_declaration import ToolUse, declared
from pinecall.types import AgentConfig, PlatformTool, platform_tools
from pinecall.types.lookup_tools import NOT_LOOKED_UP, PLATFORM_TOOLS, arguments_for, skipped_code
from pinecall_protocol.events import ErrorEvent

# How many words the caller has to have said before this turn's lookups are worth starting on what
# they have said so far. Under it a turn is a greeting or an acknowledgement — "hola", "sí, claro",
# "buenos días" — which no index has an answer for and which would cost an embed to find that out.
# At four words a caller has been talking for over a second (Spanish runs near two and a half words
# a second), which is longer than a lookup takes: 121 ms and 167 ms measured on a live call, 552 ms
# on its worst turn. So the answer is back before they stop, which is the whole point.
WORDS_ENOUGH_TO_SEARCH_WITH = 4


# The other half of the same rule, and the half a written caller is held to, because they type
# what a speaker takes four words to say. A turn with no letter in it is a number being read out
# — a phone, an order, a card — and it is not a query: no prose index answers it, the top of the
# contact's facts comes back ranked by nothing, and it goes to the embedder, which is the last
# place a caller's digits should travel. Measured: `305 555 0101.` searched a cleaning company's
# base and came back with its data-center pages, in the evidence of that very turn (2026-09-20).
#
# It is the whole of the decision on purpose. Deciding when to retrieve is a research problem with
# a literature — Adaptive-RAG routes on a trained classifier, Self-RAG on reflection tokens the
# model is fine-tuned to emit, FLARE on the model's own uncertainty — and none of that belongs in
# the path a caller is waiting on. An agent that wants the model to decide has that already:
# `pinecall docs attach <base> --mode tool`.
def could_be_a_query(said: str) -> bool:
    """Whether these words could be asked of an index at all: something in them is a word."""
    return any(character.isalpha() for character in said)


# What one lookup hands back when it is run as a task nobody may be awaiting: what it found, or the
# failure as a VALUE. A task whose exception is never retrieved warns at collection, and a turn
# that gave up on its budget retrieves nothing.
type Answered = Mapping[str, Any] | BaseException


# One turn's lookups already in flight: the words they were asked with — the caller's so far, and
# not their last — and one task per tool, in the order the tools are declared.
@dataclass(frozen=True)
class _Running:
    """The lookups of the turn being spoken: what they were asked, and the tasks that answer."""

    query: str
    tasks: tuple[asyncio.Task[Answered], ...]


class Lookup(Protocol):
    """Who runs a platform tool: the gateway in-process on text, over HTTP on voice."""

    async def lookup(
        self, call: str, tool: PlatformTool, input: Mapping[str, Any], speech_id: str | None
    ) -> Mapping[str, Any]:
        """What the tool found, as the JSON object the model reads inside a tool result."""
        ...


class NoLookup:
    """A process with nothing to look anything up in: both tools answer with nothing found."""

    async def lookup(
        self,
        call: str,  # noqa: ARG002 — the protocol's shape
        tool: PlatformTool,
        input: Mapping[str, Any],  # noqa: ARG002 — the protocol's shape
        speech_id: str | None,  # noqa: ARG002 — the protocol's shape
    ) -> Mapping[str, Any]:
        """The empty answer in the tool's own shape: nothing found, and nothing pretended."""
        return {"facts": []} if tool == "recall" else {"chunks": []}


# Everything that arrived from outside the conversation reaches the model JSON-encoded inside a
# tool_result, which is the one place both vendors name for it: docs/security/prompt-injection.md.
def as_tool_result(output: Mapping[str, Any]) -> str:
    """What livekit puts in the tool_result block: the lookup's object, as a JSON string."""
    return json.dumps(dict(output), ensure_ascii=False)


# One per call. A lookup the platform runs itself leaves a real tool_use / tool_result pair in the
# request and nothing in the history: the pair is rebuilt when the caller's turn ends and never
# piles up, exactly as the view is. A lookup the MODEL calls reaches the same service through the
# same callable, and livekit writes that pair into the history itself.
class TurnLookups:
    """One call's platform tools: what they declare, what the turn ran, and the pair it left."""

    def __init__(
        self,
        lookup: Lookup,
        call: str,
        contact: str | None,
        config: AgentConfig,
        budget_ms: int,
    ) -> None:
        self._lookup = lookup
        self._call = call
        self._contact = contact
        self._config = config
        self._budget_ms = budget_ms
        self._items: tuple[agents.ChatItem, ...] = ()
        self._speech: str | None = None
        self._runs = 0
        self._running: _Running | None = None

    @property
    def declared_tools(self) -> list[agents.Tool]:
        """recall and search as livekit declares a tool, when the class declared what they read."""
        return declared(platform_tools(self._config), self.called)

    @property
    def items(self) -> tuple[agents.ChatItem, ...]:
        """This turn's pairs, in the order they ran: the request carries them and holds none."""
        return self._items

    # The caller is still talking, and a lookup takes about as long as one phrase does. Started on
    # the first interim that carries a real question, its answer is already here when the turn ends
    # and the budget only ever covers a tail. Run at turn end instead, it spends the whole budget
    # inside the caller's silence and then skips anyway — which is what a live call measured on
    # every turn it had. ONE run per turn: a second start is a second embed of the same sentence,
    # on the tenant's money. A written caller has no interim, so a text session never calls this.
    #
    # The run is bound to its turn by being CONSUMED there, and this runtime has no turn that never
    # ends: it runs one agent per call and so never pauses livekit's scheduling (agent_activity.py
    # :1254 is the drain), and a transcript too short to cut the agent off is retained by livekit
    # and folded into the turn that does complete (:2477 and audio_recognition.py:1218).
    def heard_so_far(self, said: str) -> None:
        """What the caller has said so far: this turn's lookups start here, or not at all."""
        tools = self._what_the_platform_runs
        if self._running is not None or not tools:
            return
        if len(said.split()) < WORDS_ENOUGH_TO_SEARCH_WITH or not could_be_a_query(said):
            return
        # No speech exists while the caller is still speaking — the reply's handle is created after
        # the turn ends (agent_activity.py:2672) — which is what the turn-end path files under too.
        self._running = self._a_run(tools, said, None)

    # The query is the caller's words SO FAR when a run started on them, and their whole turn when
    # none did. A prefix finds what the finished phrase finds — "cuánto cuesta una revi" ranks the
    # same chunks as "cuánto cuesta una revisión", because both indexes rank by the words that ARE
    # there — and re-asking on the final text would spend the half second this exists to save. The
    # pair carries the words that were actually asked, so a reader of the log and the model both
    # see the prefix and never a query nobody sent; and `search` stands declared either way, so a
    # turn that changed its mind halfway can ask again in its own words.
    async def turn_ended(self, query: str, speech_id: str | None) -> tuple[ErrorEvent, ...]:
        """The lookups this declaration asks for before a turn; what did not run, as entries."""
        self._items = ()
        self._speech = speech_id
        running, self._running = self._running, None
        tools = self._what_the_platform_runs
        if not tools:
            return ()
        # A turn nothing could be asked of runs nothing, and says nothing: a skip entry would tell
        # the grounded judge a lookup was tried and failed, and none was worth trying.
        if running is None and not could_be_a_query(query):
            return ()
        run = running or self._a_run(tools, query, speech_id)
        return self._what_came_back(tools, run.query, await self._within_the_budget(run.tasks))

    # livekit's own callable for a tool the MODEL chose: the same service, the same encoding, and
    # a failure the model reads in its own turn rather than a turn that never comes.
    async def called(self, use: ToolUse) -> str:
        """One lookup the model asked for, as the JSON string it reads back."""
        tool = _a_platform_tool(use.name)
        try:
            output = await self._lookup.lookup(self._call, tool, use.arguments, self._speech)
        except Exception as failed:
            raise ToolError(
                NOT_LOOKED_UP.format(tool=tool, why=str(failed) or type(failed).__name__)
            ) from failed
        return as_tool_result(output)

    @property
    def _what_the_platform_runs(self) -> tuple[PlatformTool, ...]:
        """What runs before a turn: memory whenever it is declared, docs unless the model has it."""
        tools: list[PlatformTool] = []
        if self._config.memory is not None:
            tools.append("recall")
        if any(docs.mode == "retrieved" for docs in self._config.bases):
            tools.append("search")
        return tuple(tools)

    def _a_run(self, tools: Sequence[PlatformTool], query: str, speech_id: str | None) -> _Running:
        """This turn's lookups, one task per tool, running from now on these words."""
        return _Running(
            query,
            tuple(asyncio.create_task(self._ran(tool, query, speech_id)) for tool in tools),
        )

    # The budget is what the CALLER waits, so it is measured from the moment their turn ended and
    # never from the moment a run started: a lookup already back is read with no wait at all. It is
    # per tool, so a recall back in 487 ms is used even when the search beside it took 1085 and is
    # still out: a skip is written for the tool the model really got nothing from and for no other.
    # A task still running when the budget expires is cancelled — nobody will read its answer, and
    # the tenant should stop paying for it the moment that is true.
    async def _within_the_budget(self, tasks: Sequence[asyncio.Task[Answered]]) -> list[Answered]:
        """What each lookup answered by the end of the budget, and a timeout for what did not."""
        pending = [task for task in tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=self._budget_ms / 1000)
        answered: list[Answered] = []
        for task in tasks:
            if task.done():
                answered.append(task.result())
                continue
            task.cancel()
            answered.append(TimeoutError(f"no answer within {self._budget_ms} ms"))
        return answered

    # The failure comes back as a value and is never raised: a run started while the caller was
    # still speaking may finish after the turn gave up on it, and a task nobody awaits must leave
    # its complaint in the log rather than at the garbage collector.
    # A run started on the caller's last words, with no turn end to consume it, outlived the call:
    # a task nobody awaits, still asking the platform for a call that has hung up (2026-09-26).
    async def close(self) -> None:
        """Cancel whatever is still running: the call is over and nobody will read the answer."""
        running, self._running = self._running, None
        if running is not None:
            await cancel_and_wait(*running.tasks)

    async def _ran(self, tool: PlatformTool, query: str, speech_id: str | None) -> Answered:
        """One lookup the platform runs on the caller's words, straight from the service."""
        try:
            return await self._lookup.lookup(
                self._call, tool, arguments_for(tool, query, self._contact), speech_id
            )
        except Exception as failed:
            return failed

    def _what_came_back(
        self,
        tools: Sequence[PlatformTool],
        query: str,
        answered: Sequence[Answered],
    ) -> tuple[ErrorEvent, ...]:
        """The pairs this turn carries, and one entry per lookup that came back a failure."""
        items: list[agents.ChatItem] = []
        skipped: list[ErrorEvent] = []
        for tool, answer in zip(tools, answered, strict=True):
            if isinstance(answer, BaseException):
                skipped.append(_skipped(tool, str(answer) or type(answer).__name__))
                continue
            if not _found_anything(answer):
                continue
            items.extend(self._a_pair(tool, arguments_for(tool, query, self._contact), answer))
        self._items = tuple(items)
        return tuple(skipped)

    # A pair livekit's formatter can group: it matches a call to its output by call_id
    # (llm/_provider_format/utils.py:group_tool_calls), and a half it cannot match it drops with a
    # warning — which would leave the model a tool_use no result ever answered. The same shape
    # session/date_tool.py puts today's date in, and for the same reason: a pair is the only way
    # text from outside the conversation travels without being read as somebody's words.
    def _a_pair(
        self, tool: PlatformTool, input: Mapping[str, Any], output: Mapping[str, Any]
    ) -> tuple[agents.FunctionCall, agents.FunctionCallOutput]:
        """One run of one tool as the model sees it: the call it could have made, and the answer."""
        self._runs += 1
        call_id = f"lu_{self._runs}_{tool}"
        return (
            agents.FunctionCall(
                call_id=call_id, name=tool, arguments=json.dumps(dict(input), ensure_ascii=False)
            ),
            # reply_required is False because nothing was asked: the output is context, not a
            # turn owed an answer, and a realtime model would otherwise speak on reading it.
            agents.FunctionCallOutput(
                call_id=call_id,
                name=tool,
                output=as_tool_result(output),
                is_error=False,
                reply_required=False,
            ),
        )


# A lookup that found nothing carries nothing, and it must not be in the request at all.
#
# Measured 2026-09-11 on clinica-norte, replaying a run's own recorded requests: with the empty
# pairs in, `ofrece-las-horas-del-martes` called freeSlots 0 times in 8 and `identifica-al-paciente`
# called findPatient 0 in 6. With them out, every other byte identical, 8 of 8 and 6 of 6. Two
# tool rounds that answered `{"facts": []}` and `{"chunks": []}` sit between the caller's words and
# the view, and a model that has just made two calls and found nothing writes an answer instead of
# making a third. The caller's own sentence ends up five messages back from the end of the request.
#
# The tools stay declared, so a turn that really wants to ask can ask, and the log is untouched:
# what a lookup did is written by the service that ran it (lookups/log_entries.py), never by the
# pair.
def _found_anything(output: Mapping[str, Any]) -> bool:
    """Whether a lookup came back with something. `{"facts": []}` is not context; it is noise."""
    return any(bool(value) for value in output.values())


def _skipped(tool: PlatformTool, why: str) -> ErrorEvent:
    """The error entry of a lookup the platform could not run: recoverable, and it names why."""
    return ErrorEvent(
        code=skipped_code(tool), message=NOT_LOOKED_UP.format(tool=tool, why=why), recoverable=True
    )


def _a_platform_tool(name: str) -> PlatformTool:
    """The name livekit called our callable with, narrowed to the tool it was declared as."""
    if name not in PLATFORM_TOOLS:
        raise ToolError(f"{name} is not a tool the platform runs")
    return cast("PlatformTool", name)
