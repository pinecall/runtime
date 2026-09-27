"""A call to run a session on: its context, its agent, and the platform around it, all real."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date

import pytest

from pinecall.domain.types import (
    AgentConfig,
    CallContext,
    Channel,
    Contact,
    Json,
    JsonObject,
    Route,
)
from pinecall.log.log import Log
from pinecall.log.store import Store
from pinecall.providers.build import Running
from pinecall.session.call import Call, Platform, ToolUse
from pinecall.session.session import Session, written
from pinecall.wire.events import ToolCall
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import PlatformTool, ToolResult
from tests.fakes import ACME, AcmeLLM, Server

AGENT = "clinica-norte"
A_NUMBER = "+59829001199"
THE_CALLER = "+59899123456"
MONDAY = date(2026, 9, 28)


def context_of(
    call: str,
    channel: Channel = "whatsapp",
    *,
    contact: Contact | None = None,
    run: str | None = None,
) -> CallContext:
    """A call of the agent's through its number, on the channel."""
    route = Route(
        org="org_1", agent=AGENT, channel=channel, number=None if channel == "web" else A_NUMBER
    )
    return CallContext(
        call=call,
        channel=channel,
        direction="inbound",
        caller=THE_CALLER,
        route=route,
        today=MONDAY,
        contact=contact,
        run=run,
    )


@dataclass
class Box:
    """The platform a session reaches: the call's real log, and a gateway that answers as told."""

    log: Log
    answers: dict[str, ToolResult] = field(default_factory=dict[str, ToolResult])
    found: dict[PlatformTool, JsonObject] = field(default_factory=dict[PlatformTool, JsonObject])
    failing: set[PlatformTool] = field(default_factory=set[PlatformTool])
    used: list[ToolUse] = field(default_factory=list[ToolUse])
    looked: list[tuple[PlatformTool, JsonObject]] = field(
        default_factory=list[tuple[PlatformTool, JsonObject]]
    )
    sealed: list[tuple[list[ModelUsage], str]] = field(
        default_factory=list[tuple[list[ModelUsage], str]]
    )

    # The gateway writes the round trip, as ToolCalls does, around the app's answer.
    async def tool(self, use: ToolUse, speech: str | None) -> ToolResult:
        """What the app answers the tool, or `ok`, written as the gateway writes it."""
        self.used.append(use)
        called = ToolCall(
            call_id=use.call_id, name=use.name, arguments=use.arguments, speech_id=speech
        )
        await self.log.append("tool.call", called.written())
        answered = self.answers.get(use.name)
        result = answered or ToolResult(call_id=use.call_id, name=use.name, output="ok")
        await self.log.append("tool.result", result.written())
        return result

    async def lookup(
        self, tool: PlatformTool, arguments: JsonObject, _speech: str | None
    ) -> JsonObject:
        """What the index finds, nothing, or a failure."""
        self.looked.append((tool, arguments))
        if tool in self.failing:
            raise ConnectionError(f"the {tool} index did not answer")
        return self.found.get(tool, {"facts": []} if tool == "recall" else {"chunks": []})

    # The gateway's seal prices, judges and writes call.score; the log is sealed by it.
    async def seal(self, usage: list[ModelUsage], outcome: str) -> None:
        """Keep what the session handed over, and end the log."""
        self.sealed.append((usage, outcome))
        await self.log.append(
            "call.score", {"judges": [], "panel": [], "judge_calls": 0, "not_judged": "none"}
        )

    def platform(self) -> Platform:
        """The four doors the session is given."""
        return Platform(append=self.log.append, tool=self.tool, lookup=self.lookup, seal=self.seal)


@pytest.fixture
def box(store: Store, call: str, acme: str) -> Box:
    """The platform around one call, on the test's own log, the acme vendor installed."""
    del acme
    return Box(Log(store, call, AGENT))


def a_session(
    box: Box,
    config: AgentConfig,
    *replies: list[Json],
    contact: Contact | None = None,
    run: str | None = None,
) -> Session:
    """A written session whose model answers with the replies, in order."""
    assert box.log.call is not None
    call = Call(context_of(box.log.call, contact=contact, run=run), config, box.platform())
    scripted: list[Json] = list(replies)
    script: JsonObject = {"replies": scripted}
    return written(call, Running(ACME, "a-key", options=script))


def model_of(session: Session) -> AcmeLLM:
    """The scripted model the session runs."""
    (model,) = session.built
    assert isinstance(model, AcmeLLM)
    return model


def heard_live(box: Box) -> list[Entry]:
    """Every entry the log takes from now on, the ephemeral ones too."""
    seen: list[Entry] = []

    async def tap(entry: Entry) -> None:
        seen.append(entry)

    box.log.tapped(tap)
    return seen


async def kinds(store: Store, call: str) -> list[str]:
    """The types of the call's entries, in log order."""
    return [entry.type for entry in await store.whole(call)]


@pytest.fixture
async def server() -> AsyncIterator[Server]:
    """Livekit's server client, its doors the test's, closed at the end."""
    opened = Server()
    yield opened
    await opened.aclose()
